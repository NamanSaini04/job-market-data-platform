"""
Rebuild the raw layer from saved files (company boards and Reed).

Use it when:
  * the database was down during a run (files exist, rows don't), or
  * the database was wiped and you want everything back.

Safe to run any number of times: loading is idempotent.

Run:  python -m ingestion.reload_files                  (all files)
      python -m ingestion.reload_files --date 2026-10-15
"""

import argparse
import json
import sys

from ingestion import loader
from ingestion.db import connect
from ingestion.extractors import reed
from ingestion.extractors.ats import SPECS, Rejected, parse_board
from ingestion.raw_files import RawFileInfo, parse_raw_path, read_raw
from ingestion.settings import Settings, get_settings

PIPELINE_FOR_SOURCE = {**{s: "ats_daily" for s in SPECS},
                       "reed_search": "reed_daily", "reed_detail": "reed_daily"}


def _load_file(conn, info: RawFileInfo, body: bytes) -> tuple[str, str, dict, int]:
    """Returns (source, board, load stats, records fetched) for one raw file."""
    if info.source in SPECS:
        records, rejected = parse_board(info.source, info.board, json.loads(body))
        stats = loader.load_board(conn, info.run_id, info.observed_date, records, rejected)
        return info.source, info.board, stats, len(records) + len(rejected)

    if info.source == "reed_search":
        term_slug, posted_by, _page = info.board.rsplit("__", 2)
        records, rejected, _total = reed.parse_search_page(json.loads(body))
        stats = loader.load_board(conn, info.run_id, info.observed_date, records, rejected,
                                  ignore_fields=reed.VOLATILE_FIELDS["search"],
                                  search_hit=(term_slug, posted_by))
        return "reed", f"search:{term_slug}:{posted_by}", stats, len(records) + len(rejected)

    if info.source == "reed_detail":
        records, bad = [], []
        for item in json.loads(body):
            try:
                records.append(reed.parse_detail(str(item["jobId"]), json.loads(item["body"])))
            except Exception as e:  # noqa: BLE001
                bad.append(Rejected("reed", "detail", f"{type(e).__name__}: {e}"[:200], None))
        stats = loader.load_board(conn, info.run_id, info.observed_date, records, bad,
                                  observe=False, ignore_fields=reed.VOLATILE_FIELDS["detail"])
        return "reed", "detail", stats, len(records) + len(bad)

    raise ValueError(f"unknown source folder {info.source!r}")


def reload(settings: Settings | None = None, only_date: str | None = None, conn=None) -> dict:
    settings = settings or get_settings()
    pattern = f"*/{only_date}/*.json.gz" if only_date else "*/*/*.json.gz"
    files = sorted((settings.data_dir / "raw").glob(pattern))
    own = conn is None
    conn = conn or connect(settings)
    totals = {"files": 0, "loaded": 0, "duplicate": 0, "failed_files": 0}
    touched_runs = set()
    per_board: dict[tuple, dict] = {}     # Reed pages add up to one row per query

    for path in files:
        info = parse_raw_path(path)
        if info.source not in PIPELINE_FOR_SOURCE:
            continue
        loader.ensure_run(conn, info.run_id, PIPELINE_FOR_SOURCE[info.source], info.observed_date)
        touched_runs.add(info.run_id)
        try:
            source, board, stats, fetched = _load_file(conn, info, read_raw(path))
            agg = per_board.setdefault((info.run_id, source, board),
                                       {"fetched": 0, "loaded": 0, "rejected": 0, "file": path})
            agg["fetched"] += fetched
            agg["loaded"] += stats["loaded"]
            agg["rejected"] += stats["rejected"]
            totals["loaded"] += stats["loaded"]
            totals["duplicate"] += stats["duplicate"]
        except Exception as e:  # noqa: BLE001
            totals["failed_files"] += 1
            print(f"FAILED {path.name}: {type(e).__name__}: {e}")
        totals["files"] += 1

    for (run_id, source, board), agg in per_board.items():
        loader.record_source_run(conn, run_id, source, board, status="success",
                                 fetched=agg["fetched"], loaded=agg["loaded"],
                                 rejected=agg["rejected"],
                                 raw_file=str(agg["file"].relative_to(settings.data_dir)))
    for run_id in touched_runs:
        status = conn.execute("SELECT status FROM ops.pipeline_run WHERE run_id=%s",
                              (run_id,)).fetchone()[0]
        if status == "running":   # run was cut short by a database outage
            status = "reloaded"
        loader.finish_run(conn, run_id, status=status, duplicate=0, new_versions=0)
    if own:
        conn.close()
    return totals


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="only files for this observed date (YYYY-MM-DD)")
    totals = reload(only_date=p.parse_args().date)
    print(totals)
    return 0 if totals["failed_files"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
