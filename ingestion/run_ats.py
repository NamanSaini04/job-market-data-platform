"""
Daily collector for company job boards (Greenhouse, Lever, Ashby).

For every verified board in config/ats_companies.yaml:
  1. fetch the full board            (retries on 429/5xx)
  2. save the untouched response     (data/raw/..., before any parsing)
  3. validate and parse              (bad records rejected, not dropped)
  4. load into Postgres raw layer    (one transaction per board, idempotent)
  5. log the board's result          (ops.source_run + data/logs/pipeline.jsonl)

Failure rules:
  * One board failing never stops the others. The run ends as 'partial'.
  * If Postgres is down, responses are still saved to files and the run ends
    'partial'. Load them later with:  python -m ingestion.reload_files
  * A run summary is always written to data/runs/, even when the database is down.

Run:   python -m ingestion.run_ats                 (all verified boards)
       python -m ingestion.run_ats --only monzo    (one board, for testing)
Exit:  0 = success, 1 = partial or failed
"""

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Callable

import psycopg
import yaml

from ingestion import loader
from ingestion.db import connect
from ingestion.extractors.ats import SPECS, board_url, parse_board
from ingestion.http_client import make_session
from ingestion.logging_setup import setup_logging
from ingestion.raw_files import raw_path, write_raw
from ingestion.settings import Settings, get_settings

PIPELINE = "ats_daily"


class FetchError(Exception):
    pass


def load_boards(settings: Settings, only: str | None = None) -> list[dict]:
    companies = yaml.safe_load((settings.config_dir / "ats_companies.yaml").read_text())["companies"]
    boards = [c for c in companies if c["status"] == "verified" and c["ats"] in SPECS]
    if only:
        boards = [c for c in boards if c["slug"] == only]
    return boards


def http_fetcher(settings: Settings) -> Callable[[str], bytes]:
    session = make_session(settings)

    def fetch(url: str) -> bytes:
        r = session.get(url, timeout=settings.http_timeout_s)
        if r.status_code != 200:
            raise FetchError(f"HTTP {r.status_code}")
        return r.content
    return fetch


def run(settings: Settings | None = None, fetch: Callable[[str], bytes] | None = None,
        only: str | None = None, conn: psycopg.Connection | None = None) -> dict:
    settings = settings or get_settings()
    log = setup_logging(settings.data_dir / "logs")
    fetch = fetch or http_fetcher(settings)
    boards = load_boards(settings, only)

    run_id = uuid.uuid4()
    observed = datetime.now(timezone.utc).date()
    summary = {"run_id": str(run_id), "pipeline": PIPELINE, "observed_date": observed.isoformat(),
               "started_at": datetime.now(timezone.utc).isoformat(), "db_available": True,
               "boards": []}
    duplicate_total = 0
    new_versions_total = 0

    # Database is optional for the run to continue: files are the safety net.
    own_conn = conn is None
    if conn is None:
        try:
            conn = connect(settings)
        except psycopg.OperationalError as e:
            conn = None
            log.error("database unavailable, saving files only", extra={"error": str(e)[:200]})
    if conn is not None:
        loader.start_run(conn, run_id, PIPELINE, observed, len(boards))
    else:
        summary["db_available"] = False

    log.info("run started", extra={"run_id": str(run_id), "records": len(boards)})

    for i, b in enumerate(boards):
        source, slug = b["ats"], b["slug"]
        t0 = time.monotonic()
        result = {"source": source, "board": slug, "status": "failed", "fetched": 0,
                  "loaded": 0, "rejected": 0, "error": None, "raw_file": None}
        try:
            body = fetch(board_url(source, slug))
            path = write_raw(raw_path(settings.data_dir, source, observed, slug, run_id), body)
            result["raw_file"] = str(path.relative_to(settings.data_dir))
            records, rejected = parse_board(source, slug, json.loads(body))
            result["fetched"] = len(records) + len(rejected)
            result["rejected"] = len(rejected)
            if conn is not None:
                try:
                    stats = loader.load_board(conn, run_id, observed, records, rejected)
                    result.update(loaded=stats["loaded"], rejected=stats["rejected"],
                                  status="success")
                    duplicate_total += stats["duplicate"]
                    new_versions_total += stats["new_versions"]
                except psycopg.OperationalError as e:
                    # Lost the database mid-run: keep going on files only.
                    conn = None
                    summary["db_available"] = False
                    log.error("database lost during load", extra={"error": str(e)[:200]})
            if conn is None:
                # Data is safe in the raw file. Not a failure: reload_files will load it.
                result["status"] = "file_only"
                result["error"] = "saved to file only (database unavailable)"
        except Exception as e:  # noqa: BLE001  one board must never stop the run
            result["status"] = "failed"
            result["error"] = f"{type(e).__name__}: {e}"[:300]
        result["duration_ms"] = int((time.monotonic() - t0) * 1000)

        if conn is not None:
            try:
                loader.record_source_run(
                    conn, run_id, source, slug, status=result["status"],
                    fetched=result["fetched"], loaded=result["loaded"],
                    rejected=result["rejected"], duration_ms=result["duration_ms"],
                    raw_file=result["raw_file"], error=result["error"])
            except psycopg.OperationalError:
                conn = None
                summary["db_available"] = False

        level = log.info if result["status"] == "success" else log.warning
        level("board done", extra={"run_id": str(run_id), "source": source, "board": slug,
                                   "status": result["status"], "records": result["fetched"],
                                   "duration_ms": result["duration_ms"],
                                   **({"error": result["error"]} if result["error"] else {})})
        summary["boards"].append(result)
        if i < len(boards) - 1:
            time.sleep(settings.pause_between_requests_s)

    failed = sum(1 for r in summary["boards"] if r["status"] == "failed")
    if boards and failed == len(boards):
        status = "failed"
    elif failed or not summary["db_available"]:
        status = "partial"
    else:
        status = "success"
    summary.update(status=status, boards_failed=failed,
                   finished_at=datetime.now(timezone.utc).isoformat(),
                   records_fetched=sum(r["fetched"] for r in summary["boards"]),
                   records_loaded=sum(r["loaded"] for r in summary["boards"]),
                   records_rejected=sum(r["rejected"] for r in summary["boards"]),
                   records_duplicate=duplicate_total, payload_versions_new=new_versions_total)

    if conn is not None:
        loader.finish_run(conn, run_id, status=status, duplicate=duplicate_total,
                          new_versions=new_versions_total,
                          error=f"{failed} board(s) failed" if failed else None)
        if own_conn:
            conn.close()

    runs_dir = settings.data_dir / "runs" / observed.isoformat()
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / f"{run_id}.json").write_text(json.dumps(summary, indent=2))

    log.info("run finished", extra={"run_id": str(run_id), "status": status,
                                    "records": summary["records_fetched"]})
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", help="run a single board slug (for testing)")
    args = parser.parse_args()
    summary = run(only=args.only)
    print(f"\nstatus={summary['status']}  boards={len(summary['boards'])}  "
          f"failed={summary['boards_failed']}  fetched={summary['records_fetched']}  "
          f"new_observations={summary['records_loaded']}  re-run_duplicates={summary['records_duplicate']}  "
          f"new_or_changed_content={summary['payload_versions_new']}  rejected={summary['records_rejected']}")
    return 0 if summary["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
