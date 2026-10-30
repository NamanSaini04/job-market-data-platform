"""
Daily collector for Reed.

Step 1, search (cheap, ~130 calls/day):
  For every role search term x {direct employer, recruitment agency}:
  page through ALL results (Reed has no date filter, so there is no other way
  to find new jobs). Every page is saved raw, parsed, loaded. Each job gets one
  observation per day, plus a search-hit row saying which search found it.

Step 2, detail (expensive, 1 call per job):
  Queue = jobs seen today, with a title matching a role family in roles.yaml,
  that have no detail stored yet. Newest first. Capped per day (REED_MAX_DETAILS).
  Unfinished queue carries over automatically: it is recomputed from the database.

Rate limits: Reed publishes none. On HTTP 429 (after the client's own retries),
the run STOPS calling Reed for the day instead of pushing harder. The run ends
'partial'; tomorrow continues.

Closure safety: board 'search' is marked success only if EVERY query finished.
If any query failed, jobs missing today are "unknown", never "closed".

Resume (added 30 Oct 2026): a run killed half-way (laptop asleep -> Airflow stopped it)
must be safe AND cheap to retry. So a run first reads what earlier runs already did TODAY:
  * searches that already succeeded today are skipped, not fetched again
  * if Reed rate-limited us earlier today, make no calls at all
  * the detail cap is per DAY: details fetched by earlier runs today count against it
A retry therefore costs only the missing searches, never a second full sweep.
--no-resume forces a full sweep (tests, or a manual re-check).

Run:   python -m ingestion.run_reed
       python -m ingestion.run_reed --max-details 50     (small test run)
       python -m ingestion.run_reed --no-resume          (ignore today's earlier runs)
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
from ingestion.extractors import reed
from ingestion.extractors.ats import Rejected
from ingestion.http_client import make_session
from ingestion.logging_setup import setup_logging
from ingestion.raw_files import raw_path, write_raw
from ingestion.settings import Settings, get_settings

PIPELINE = "reed_daily"
DETAIL_BATCH = 100

Fetch = Callable[[str, dict | None], bytes]


class FetchError(Exception):
    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")
        self.status = status


class RateLimited(Exception):
    pass


def http_fetcher(settings: Settings) -> Fetch:
    session = make_session(settings)

    def fetch(path: str, params: dict | None) -> bytes:
        # The key goes in the Authorization header (HTTP Basic), never in the URL,
        # so it cannot leak through error messages or logs.
        r = session.get(reed.API + path, params=params, auth=(settings.reed_api_key, ""),
                        timeout=settings.http_timeout_s)
        if r.status_code == 429:
            raise RateLimited("HTTP 429 after retries")
        if r.status_code != 200:
            raise FetchError(r.status_code)
        return r.content
    return fetch


def load_roles(settings: Settings) -> dict:
    return yaml.safe_load((settings.config_dir / "roles.yaml").read_text())


def search_terms(roles: dict) -> list[str]:
    seen, out = set(), []
    for fam in roles["role_families"]:
        for t in fam["search_terms"]:
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out


def detail_queue(conn: psycopg.Connection, observed, patterns: list[str]) -> list[str]:
    """Jobs seen today, data-role title, no detail yet. Newest posting first."""
    rows = conn.execute(
        """SELECT o.source_job_id, p.payload->>'jobTitle', p.payload->>'date'
             FROM raw.job_observation o
             JOIN raw.job_payload p
               ON (p.source, p.board, p.source_job_id, p.payload_hash)
                = (o.source, o.board, o.source_job_id, o.payload_hash)
            WHERE o.source = 'reed' AND o.board = 'search' AND o.observed_date = %s
              AND NOT EXISTS (SELECT 1 FROM raw.job_payload d
                               WHERE d.source = 'reed' AND d.board = 'detail'
                                 AND d.source_job_id = o.source_job_id)""",
        (observed,)).fetchall()

    def posted(d: str | None):
        try:
            return datetime.strptime(d, "%d/%m/%Y")
        except (TypeError, ValueError):
            return datetime.min
    matching = [(jid, posted(d)) for jid, title, d in rows if reed.title_matches(title, patterns)]
    matching.sort(key=lambda x: x[1], reverse=True)
    return [jid for jid, _ in matching]


def progress_today(conn: psycopg.Connection, observed) -> dict:
    """What earlier Reed runs already achieved today (from ops.source_run)."""
    rows = conn.execute(
        """SELECT s.board, s.status, s.error_message, s.records_fetched
             FROM ops.source_run s JOIN ops.pipeline_run r USING (run_id)
            WHERE r.pipeline = %s AND r.observed_date = %s AND s.source = 'reed'""",
        (PIPELINE, observed)).fetchall()
    return {
        "searches_done": {b for b, st, _, _ in rows if b.startswith("search:") and st == "success"},
        "rate_limited": any((e or "").startswith("HTTP 429") for _, _, e, _ in rows),
        "details_fetched": sum(f or 0 for b, _, _, f in rows if b == "detail"),
    }


def run(settings: Settings | None = None, fetch: Fetch | None = None,
        conn: psycopg.Connection | None = None, max_details: int | None = None,
        resume: bool = True) -> dict:
    settings = settings or get_settings()
    log = setup_logging(settings.data_dir / "logs")
    max_details = settings.reed_max_details if max_details is None else max_details
    run_id = uuid.uuid4()
    observed = datetime.now(timezone.utc).date()
    summary = {"run_id": str(run_id), "pipeline": PIPELINE, "observed_date": observed.isoformat(),
               "started_at": datetime.now(timezone.utc).isoformat(), "db_available": True,
               "queries": [], "detail": {}, "rate_limited": False}

    if fetch is None:
        if not settings.reed_api_key:
            log.error("REED_API_KEY missing in .env")
            summary.update(status="failed", error="REED_API_KEY missing")
            return summary
        fetch = http_fetcher(settings)

    roles = load_roles(settings)
    patterns = reed.title_patterns(roles)
    queries = [(t, pb) for t in search_terms(roles) for pb in reed.POSTED_BY]

    own_conn = conn is None
    if conn is None:
        try:
            conn = connect(settings)
        except psycopg.OperationalError as e:
            log.error("database unavailable, saving files only", extra={"error": str(e)[:200]})
    earlier = {"searches_done": set(), "rate_limited": False, "details_fetched": 0}
    if conn is not None:
        if resume:
            earlier = progress_today(conn, observed)       # read BEFORE this run adds rows
        loader.start_run(conn, run_id, PIPELINE, observed, len(queries) + 2)
    else:
        summary["db_available"] = False
    if earlier["rate_limited"]:
        summary["rate_limited"] = True                     # respect today's 429: no calls at all
    summary["resumed_searches"] = len(earlier["searches_done"])
    log.info("reed run started", extra={"run_id": str(run_id), "records": len(queries)})

    def log_source(board: str, **kw) -> None:
        nonlocal conn
        if conn is None:
            return
        try:
            loader.record_source_run(conn, run_id, "reed", board, **kw)
        except psycopg.OperationalError:
            conn = None
            summary["db_available"] = False

    duplicate_total = new_versions_total = 0

    # ---------------- step 1: search ----------------
    for term, posted_by in queries:
        board = f"search:{reed.slug(term)}:{posted_by}"
        q = {"board": board, "status": "success", "pages": 0, "fetched": 0, "loaded": 0,
             "rejected": 0, "total_reported": None, "error": None, "resumed": False}
        t0 = time.monotonic()
        if board in earlier["searches_done"]:
            q["resumed"] = True                            # done by an earlier run today
        elif summary["rate_limited"]:
            q.update(status="failed", error="skipped: Reed rate limit reached earlier")
        else:
            try:
                page = 0
                while True:
                    body = fetch("/search", reed.search_params(term, posted_by, page))
                    write_raw(raw_path(settings.data_dir, "reed_search", observed,
                                       f"{reed.slug(term)}__{posted_by}__p{page:03d}", run_id), body)
                    records, rejected, total = reed.parse_search_page(json.loads(body))
                    q["total_reported"] = total
                    q["fetched"] += len(records) + len(rejected)
                    q["rejected"] += len(rejected)
                    if conn is not None:
                        try:
                            s = loader.load_board(conn, run_id, observed, records, rejected,
                                                  ignore_fields=reed.VOLATILE_FIELDS["search"],
                                                  search_hit=(reed.slug(term), posted_by))
                            q["loaded"] += s["loaded"]
                            duplicate_total += s["duplicate"]
                            new_versions_total += s["new_versions"]
                        except psycopg.OperationalError:
                            conn = None
                            summary["db_available"] = False
                    page += 1
                    q["pages"] = page
                    if not records and not rejected:
                        break                                   # empty page: end of results
                    if page * reed.PAGE_SIZE >= total:
                        break                                   # all pages read
                    if page >= settings.reed_max_pages_per_query:
                        q.update(status="failed",
                                 error=f"stopped at page cap ({page} pages, {total} reported)")
                        break
                    time.sleep(settings.pause_between_requests_s)
            except RateLimited:
                summary["rate_limited"] = True
                q.update(status="failed", error="HTTP 429: Reed rate limit, stopping for today")
            except Exception as e:  # noqa: BLE001  one query must never stop the run
                q.update(status="failed", error=f"{type(e).__name__}: {e}"[:300])
        q["duration_ms"] = int((time.monotonic() - t0) * 1000)
        log_source(board, status=q["status"], fetched=q["fetched"], loaded=q["loaded"],
                   rejected=q["rejected"], duration_ms=q["duration_ms"], error=q["error"])
        (log.info if q["status"] == "success" else log.warning)(
            "reed query done", extra={"run_id": str(run_id), "source": "reed", "board": board,
                                      "status": q["status"], "records": q["fetched"],
                                      **({"error": q["error"]} if q["error"] else {})})
        summary["queries"].append(q)
        if not q["resumed"]:
            time.sleep(settings.pause_between_requests_s)

    search_ok = all(q["status"] == "success" for q in summary["queries"])
    # The row closure logic will check: was the WHOLE search complete today?
    log_source("search", status="success" if search_ok else "failed",
               fetched=sum(q["fetched"] for q in summary["queries"]),
               error=None if search_ok else "one or more queries failed: missing jobs are unknown, not closed")

    # ---------------- step 2: detail ----------------
    d = {"queue": 0, "attempted": 0, "loaded": 0, "not_found": 0, "remaining": 0, "error": None}
    if conn is None:
        d["error"] = "skipped: database unavailable (queue is computed from the database)"
    elif summary["rate_limited"]:
        d["error"] = "skipped: Reed rate limit reached during search"
    else:
        queue = detail_queue(conn, observed, patterns)
        d["queue"] = len(queue)
        d["cap_today"] = max(0, max_details - earlier["details_fetched"])   # the cap is per DAY
        todo = queue[:d["cap_today"]]
        batch: list[tuple[str, bytes]] = []
        not_found: list[Rejected] = []

        def flush(batch_no: int) -> None:
            nonlocal conn
            if not batch and not not_found:
                return
            body = json.dumps([{"jobId": j, "body": b.decode("utf-8")} for j, b in batch]).encode()
            write_raw(raw_path(settings.data_dir, "reed_detail", observed,
                               f"batch{batch_no:03d}", run_id), body)
            if conn is None:          # database lost mid-run: the raw file above is the backup
                batch.clear()
                not_found.clear()
                return
            records, bad = [], list(not_found)
            for job_id, b in batch:
                try:
                    records.append(reed.parse_detail(job_id, json.loads(b)))
                except Exception as e:  # noqa: BLE001
                    bad.append(Rejected("reed", "detail", f"{type(e).__name__}: {e}"[:200], None))
            try:
                s = loader.load_board(conn, run_id, observed, records, bad, observe=False,
                                      ignore_fields=reed.VOLATILE_FIELDS["detail"])
                d["loaded"] += s["loaded"]
            except psycopg.OperationalError:
                conn = None
                summary["db_available"] = False
            batch.clear()
            not_found.clear()

        t0 = time.monotonic()
        batch_no = 0
        for job_id in todo:
            if conn is None:
                break
            d["attempted"] += 1
            try:
                batch.append((job_id, fetch(f"/jobs/{job_id}", None)))
            except RateLimited:
                summary["rate_limited"] = True
                d["error"] = "HTTP 429: Reed rate limit, stopping details for today"
                break
            except FetchError as e:
                if e.status == 404:      # job removed between search and detail
                    d["not_found"] += 1
                    not_found.append(Rejected("reed", "detail", f"detail 404 for job {job_id}", None))
                else:
                    d["error"] = f"detail HTTP {e.status} for job {job_id}"
            except Exception as e:  # noqa: BLE001
                d["error"] = f"{type(e).__name__}: {e}"[:300]
            if len(batch) >= DETAIL_BATCH:
                batch_no += 1
                flush(batch_no)
            time.sleep(settings.pause_between_requests_s)
        batch_no += 1
        flush(batch_no)
        d["remaining"] = max(0, len(queue) - d["attempted"])
        d["duration_ms"] = int((time.monotonic() - t0) * 1000)

    detail_failed = summary["rate_limited"] or (d["error"] or "").startswith("skipped")
    log_source("detail", status="failed" if detail_failed else "success",
               fetched=d["attempted"], loaded=d["loaded"], rejected=d["not_found"],
               duration_ms=d.get("duration_ms"),
               error=d["error"] or (f"backlog remaining: {d['remaining']}" if d["remaining"] else None))
    log.info("reed detail done", extra={"run_id": str(run_id), "source": "reed", "board": "detail",
                                         "records": d["loaded"],
                                         **({"error": d["error"]} if d["error"] else {})})
    summary["detail"] = d

    # ---------------- finish ----------------
    failed_queries = sum(1 for q in summary["queries"] if q["status"] != "success")
    if failed_queries == len(queries):
        status = "failed"
    elif failed_queries or summary["rate_limited"] or not summary["db_available"]:
        status = "partial"
    else:
        status = "success"
    summary.update(status=status, queries_failed=failed_queries,
                   finished_at=datetime.now(timezone.utc).isoformat(),
                   search_fetched=sum(q["fetched"] for q in summary["queries"]),
                   new_observations=sum(q["loaded"] for q in summary["queries"]),
                   duplicates=duplicate_total, new_or_changed_content=new_versions_total)
    if conn is not None:
        loader.finish_run(conn, run_id, status=status, duplicate=duplicate_total,
                          new_versions=new_versions_total,
                          error=f"{failed_queries} quer(y/ies) failed" if failed_queries else None)
        if own_conn:
            conn.close()

    runs_dir = settings.data_dir / "runs" / observed.isoformat()
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / f"{run_id}.json").write_text(json.dumps(summary, indent=2))
    log.info("reed run finished", extra={"run_id": str(run_id), "status": status,
                                          "records": summary["search_fetched"]})
    return summary


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--max-details", type=int, help="cap detail calls for today")
    p.add_argument("--no-resume", action="store_true",
                   help="ignore what earlier runs did today and sweep everything again")
    a = p.parse_args()
    s = run(max_details=a.max_details, resume=not a.no_resume)
    if "queries" not in s or not s["queries"]:
        print(f"status={s['status']}  error={s.get('error')}")
        return 1
    d = s["detail"]
    print(f"\nstatus={s['status']}  queries={len(s['queries'])}  failed={s['queries_failed']}  "
          f"search_results={s['search_fetched']}  new_jobs_today={s['new_observations']}  "
          f"already_seen_today={s['duplicates']}  new_or_changed={s['new_or_changed_content']}")
    print(f"detail: queue={d.get('queue', 0)}  fetched={d.get('loaded', 0)}  "
          f"not_found={d.get('not_found', 0)}  backlog_remaining={d.get('remaining', 0)}"
          + (f"  note={d['error']}" if d.get("error") else ""))
    return 0 if s["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
