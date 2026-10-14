"""
Rebuild the raw layer from saved files.

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
from ingestion.extractors.ats import SPECS, parse_board
from ingestion.raw_files import parse_raw_path, read_raw
from ingestion.run_ats import PIPELINE
from ingestion.settings import Settings, get_settings


def reload(settings: Settings | None = None, only_date: str | None = None, conn=None) -> dict:
    settings = settings or get_settings()
    pattern = f"*/{only_date}/*.json.gz" if only_date else "*/*/*.json.gz"
    files = sorted((settings.data_dir / "raw").glob(pattern))
    own = conn is None
    conn = conn or connect(settings)
    totals = {"files": 0, "loaded": 0, "duplicate": 0, "failed_files": 0}
    touched_runs = set()

    for path in files:
        info = parse_raw_path(path)
        if info.source not in SPECS:
            continue
        loader.ensure_run(conn, info.run_id, PIPELINE, info.observed_date)
        touched_runs.add(info.run_id)
        try:
            records, rejected = parse_board(info.source, info.board, json.loads(read_raw(path)))
            stats = loader.load_board(conn, info.run_id, info.observed_date, records, rejected)
            loader.record_source_run(conn, info.run_id, info.source, info.board, status="success",
                                     fetched=len(records) + len(rejected), loaded=stats["loaded"],
                                     rejected=stats["rejected"],
                                     raw_file=str(path.relative_to(settings.data_dir)))
            totals["loaded"] += stats["loaded"]
            totals["duplicate"] += stats["duplicate"]
        except Exception as e:  # noqa: BLE001
            totals["failed_files"] += 1
            print(f"FAILED {path.name}: {type(e).__name__}: {e}")
        totals["files"] += 1

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
