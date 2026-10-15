"""
Load parsed records into the raw layer, and keep the run log up to date.

Idempotent by design: running the same day twice writes nothing new.
  * job_payload:      ON CONFLICT DO NOTHING  (same content already stored)
  * job_observation:  ON CONFLICT DO NOTHING  (job already observed today)
"""

import hashlib
import json
from datetime import date
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from ingestion.extractors.ats import RawRecord, Rejected


def payload_hash(payload: dict, ignore: tuple[str, ...] = ()) -> str:
    """SHA-256 of canonical JSON: key order and whitespace don't change the hash.

    `ignore` lists top-level fields that change constantly without the job
    changing (e.g. Reed's application count). They are still stored, just not
    counted as a content change.
    """
    if ignore:
        payload = {k: v for k, v in payload.items() if k not in ignore}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _has_nul(payload) -> bool:
    # Postgres JSONB cannot store the \u0000 character. Such records are rejected,
    # not silently changed. The raw file still has the exact original.
    return "\\u0000" in json.dumps(payload)


# ---------- run log ----------

def start_run(conn: psycopg.Connection, run_id: UUID, pipeline: str, observed: date,
              boards_total: int) -> None:
    conn.execute(
        """INSERT INTO ops.pipeline_run (run_id, pipeline, observed_date, boards_total)
           VALUES (%s, %s, %s, %s)""",
        (run_id, pipeline, observed, boards_total))
    conn.commit()


def ensure_run(conn: psycopg.Connection, run_id: UUID, pipeline: str, observed: date) -> None:
    """Used when reloading files whose run was never logged (database was down)."""
    conn.execute(
        """INSERT INTO ops.pipeline_run (run_id, pipeline, observed_date, status)
           VALUES (%s, %s, %s, 'reloaded') ON CONFLICT (run_id) DO NOTHING""",
        (run_id, pipeline, observed))
    conn.commit()


def record_source_run(conn: psycopg.Connection, run_id: UUID, source: str, board: str, *,
                      status: str, fetched: int = 0, loaded: int = 0, rejected: int = 0,
                      duration_ms: int | None = None, raw_file: str | None = None,
                      error: str | None = None) -> None:
    conn.execute(
        """INSERT INTO ops.source_run
             (run_id, source, board, status, records_fetched, records_loaded,
              records_rejected, duration_ms, raw_file, error_message)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (run_id, source, board) DO UPDATE SET
             status = EXCLUDED.status,
             records_fetched = EXCLUDED.records_fetched,
             records_loaded = ops.source_run.records_loaded + EXCLUDED.records_loaded,
             records_rejected = EXCLUDED.records_rejected,
             raw_file = COALESCE(EXCLUDED.raw_file, ops.source_run.raw_file),
             error_message = EXCLUDED.error_message""",
        (run_id, source, board, status, fetched, loaded, rejected, duration_ms, raw_file,
         (error or "")[:500] or None))
    conn.commit()


def finish_run(conn: psycopg.Connection, run_id: UUID, *, status: str, duplicate: int,
               new_versions: int, error: str | None = None) -> None:
    """Totals are recomputed from ops.source_run, so they can never drift from the detail."""
    conn.execute(
        """UPDATE ops.pipeline_run r SET
             finished_at      = now(),
             status           = %s,
             boards_failed    = s.failed,
             records_fetched  = s.fetched,
             records_loaded   = s.loaded,
             records_rejected = s.rejected,
             records_duplicate = r.records_duplicate + %s,
             payload_versions_new = r.payload_versions_new + %s,
             error_message    = %s
           FROM (SELECT count(*) FILTER (WHERE status = 'failed') AS failed,
                        coalesce(sum(records_fetched), 0)  AS fetched,
                        coalesce(sum(records_loaded), 0)   AS loaded,
                        coalesce(sum(records_rejected), 0) AS rejected
                   FROM ops.source_run WHERE run_id = %s) s
           WHERE r.run_id = %s""",
        (status, duplicate, new_versions, (error or "")[:500] or None, run_id, run_id))
    conn.commit()


# ---------- data ----------

def load_board(conn: psycopg.Connection, run_id: UUID, observed: date,
               records: list[RawRecord], rejected: list[Rejected], *,
               ignore_fields: tuple[str, ...] = (), observe: bool = True,
               search_hit: tuple[str, str] | None = None) -> dict:
    """Load one board (or one Reed search page) in ONE transaction: all of it or none.

    observe=False    store content only, no daily observation (Reed detail calls:
                     fetched once per job, not a daily snapshot).
    search_hit       (search_term, posted_by): also record which Reed search found
                     each job (raw.reed_search_hit).
    """
    stats = {"loaded": 0, "duplicate": 0, "new_versions": 0, "rejected": len(rejected)}
    extra_rejects: list[Rejected] = []

    with conn.transaction(), conn.cursor() as cur:
        for rec in records:
            if _has_nul(rec.payload):
                extra_rejects.append(Rejected(rec.source, rec.board,
                                              "contains NUL character", None))
                continue
            h = payload_hash(rec.payload, ignore_fields)
            cur.execute(
                """INSERT INTO raw.job_payload
                     (source, board, source_job_id, payload_hash, payload, first_seen_run)
                   VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (rec.source, rec.board, rec.source_job_id, h, Jsonb(rec.payload), run_id))
            stats["new_versions"] += cur.rowcount
            if observe:
                cur.execute(
                    """INSERT INTO raw.job_observation
                         (source, board, source_job_id, observed_date, payload_hash, run_id)
                       VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (rec.source, rec.board, rec.source_job_id, observed, h, run_id))
                if cur.rowcount:
                    stats["loaded"] += 1
                else:
                    stats["duplicate"] += 1   # already seen today (re-run, or another search)
            else:
                stats["loaded"] += 1
            if search_hit:
                cur.execute(
                    """INSERT INTO raw.reed_search_hit
                         (source_job_id, observed_date, search_term, posted_by, run_id)
                       VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (rec.source_job_id, observed, search_hit[0], search_hit[1], run_id))

        for rej in rejected + extra_rejects:
            payload = None if rej.payload is None or _has_nul(rej.payload) else Jsonb(rej.payload)
            cur.execute(
                """INSERT INTO raw.rejected_record (run_id, source, board, reason, payload)
                   VALUES (%s,%s,%s,%s,%s)""",
                (run_id, rej.source, rej.board, rej.reason, payload))

    stats["rejected"] += len(extra_rejects)
    return stats
