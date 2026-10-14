"""
Integration tests: the whole pipeline against real Postgres.

Each test proves one promise the pipeline makes:
  idempotent re-runs, change detection, survival of a failed board,
  survival of bad JSON, survival of a database outage, rebuild from files.
"""

import json
from dataclasses import replace

from conftest import count
from ingestion import reload_files, run_ats
from ingestion.db import migrate


def test_migrations_run_once(db, settings):
    assert migrate(db, settings.migrations_dir) == []      # already applied by the fixture


def test_first_run_loads_everything(db, settings, fetcher):
    s = run_ats.run(settings, fetch=fetcher, conn=db)
    assert s["status"] == "success"
    assert len(s["boards"]) == 3                            # the unverified board is skipped
    assert count(db, "raw.job_observation") == 9
    assert count(db, "raw.job_payload") == 9
    run = db.execute("SELECT status, records_fetched, records_loaded, boards_failed "
                     "FROM ops.pipeline_run").fetchone()
    assert run == ("success", 9, 9, 0)
    assert count(db, "ops.source_run") == 3
    assert len(list((settings.data_dir / "raw").rglob("*.json.gz"))) == 3
    assert len(list((settings.data_dir / "runs").rglob("*.json"))) == 1


def test_rerun_same_day_is_idempotent(db, settings, fetcher):
    run_ats.run(settings, fetch=fetcher, conn=db)
    s = run_ats.run(settings, fetch=fetcher, conn=db)
    assert s["status"] == "success"
    assert s["records_loaded"] == 0 and s["records_duplicate"] == 9
    assert count(db, "raw.job_observation") == 9          # no duplicates
    assert count(db, "raw.job_payload") == 9
    assert count(db, "ops.pipeline_run") == 2             # but both runs are logged


def test_changed_job_content_is_stored_as_a_new_version(db, settings, responses, fetcher):
    run_ats.run(settings, fetch=fetcher, conn=db)
    gh = json.loads(responses["greenhouse"])
    gh["jobs"][0]["title"] = gh["jobs"][0]["title"] + " (Updated)"
    responses["greenhouse"] = json.dumps(gh).encode()

    s = run_ats.run(settings, fetch=fetcher, conn=db)
    assert s["payload_versions_new"] == 1
    assert count(db, "raw.job_payload") == 10             # old version kept, new one added
    assert count(db, "raw.job_observation") == 9


def test_one_failing_board_does_not_stop_the_others(db, settings, fetcher):
    def flaky(url):
        if "lever" in url:
            raise run_ats.FetchError("HTTP 503")
        return fetcher(url)

    s = run_ats.run(settings, fetch=flaky, conn=db)
    assert s["status"] == "partial" and s["boards_failed"] == 1
    assert count(db, "raw.job_observation") == 6
    failed = db.execute("SELECT board, error_message FROM ops.source_run "
                        "WHERE status='failed'").fetchall()
    assert failed == [("leverco", "FetchError: HTTP 503")]


def test_garbage_response_fails_that_board_but_keeps_the_raw_file(db, settings, responses, fetcher):
    responses["ashby"] = b"<html>Service Unavailable</html>"
    s = run_ats.run(settings, fetch=fetcher, conn=db)
    ashby = next(b for b in s["boards"] if b["source"] == "ashby")
    assert ashby["status"] == "failed" and "JSONDecodeError" in ashby["error"]
    assert ashby["raw_file"]                               # evidence kept for debugging
    assert s["status"] == "partial"


def test_rejected_records_are_stored_not_dropped(db, settings, responses, fetcher):
    gh = json.loads(responses["greenhouse"])
    gh["jobs"].append({"title": "Job with no id"})
    responses["greenhouse"] = json.dumps(gh).encode()
    s = run_ats.run(settings, fetch=fetcher, conn=db)
    assert s["records_rejected"] == 1
    assert db.execute("SELECT reason FROM raw.rejected_record").fetchall() == [("missing 'id'",)]


def test_database_outage_saves_files_then_reload_recovers(db, settings, fetcher):
    down = replace(settings, pg_port=1)                   # nothing listens on port 1
    s = run_ats.run(down, fetch=fetcher)
    assert s["db_available"] is False
    assert s["status"] == "partial"                        # not 'failed': no data was lost
    assert {b["status"] for b in s["boards"]} == {"file_only"}
    assert count(db, "raw.job_observation") == 0

    totals = reload_files.reload(settings, conn=db)
    assert totals == {"files": 3, "loaded": 9, "duplicate": 0, "failed_files": 0}
    assert count(db, "raw.job_observation") == 9
    assert db.execute("SELECT status FROM ops.pipeline_run").fetchone()[0] == "reloaded"


def test_raw_layer_can_be_rebuilt_from_files(db, settings, fetcher):
    run_ats.run(settings, fetch=fetcher, conn=db)
    before = count(db, "raw.job_observation"), count(db, "raw.job_payload")

    db.execute("DROP SCHEMA raw CASCADE")
    db.execute("DROP SCHEMA ops CASCADE")
    db.commit()
    migrate(db, settings.migrations_dir)
    reload_files.reload(settings, conn=db)

    assert (count(db, "raw.job_observation"), count(db, "raw.job_payload")) == before
