"""
Reed tests. Unit tests on real sample responses, then the full Reed pipeline
against real Postgres with a fake Reed API (tests/fake_reed.py).

Test config (conftest): 2 search terms x 2 poster types = 4 queries.
"""

import json
from dataclasses import replace

import pytest

from conftest import count, fixture_records
from fake_reed import FakeReed, job
from ingestion import reload_files, run_reed
from ingestion.extractors import reed
from ingestion.extractors.ats import SchemaChangedError
from ingestion.loader import payload_hash

# ---------------- unit ----------------


def test_parses_real_reed_search_page():
    sample = fixture_records("reed_search")
    good, bad, total = reed.parse_search_page({"results": sample, "totalResults": 1234})
    assert [r.source_job_id for r in good] == [str(j["jobId"]) for j in sample]
    assert bad == [] and total == 1234


def test_search_shape_change_fails_loudly():
    with pytest.raises(SchemaChangedError):
        reed.parse_search_page({"results": []})                 # no totalResults
    with pytest.raises(SchemaChangedError):
        reed.parse_search_page([])


def test_detail_must_match_the_job_asked_for():
    d = fixture_records("reed_detail")[0]
    assert reed.parse_detail(str(d["jobId"]), d).source_job_id == str(d["jobId"])
    with pytest.raises(SchemaChangedError):
        reed.parse_detail("999", d)                              # wrong job returned


def test_title_filter_uses_role_patterns_not_the_search_term():
    patterns = ["data engineer", "data analyst"]
    assert reed.title_matches("Senior Data Engineer (Azure)", patterns)
    assert not reed.title_matches("AI Solutions Engineer", patterns)   # real Phase 1 example


def test_application_count_is_not_a_content_change():
    a = job(1, "Data Engineer", applications=5)
    b = job(1, "Data Engineer", applications=97)
    c = job(1, "Lead Data Engineer", applications=5)
    ignore = reed.VOLATILE_FIELDS["search"]
    assert payload_hash(a, ignore) == payload_hash(b, ignore)
    assert payload_hash(a, ignore) != payload_hash(c, ignore)


def test_search_params_page_and_flag():
    p = reed.search_params("data engineer", "agency", 2)
    assert p["resultsToSkip"] == 200 and p["resultsToTake"] == 100
    assert p["postedByRecruitmentAgency"] == "true" and "postedByDirectEmployer" not in p


# ---------------- pipeline ----------------

def market(n_direct_de=250, n_agency_de=40, n_direct_da=30, n_agency_da=20):
    """A small job market. Ids are unique per bucket; titles mostly match."""
    return {
        ("data engineer", "direct"): [job(1000 + i, "Data Engineer" if i % 5 else "Platform Engineer")
                                      for i in range(n_direct_de)],
        ("data engineer", "agency"): [job(2000 + i, "Senior Data Engineer") for i in range(n_agency_de)],
        ("data analyst", "direct"): [job(3000 + i, "Data Analyst") for i in range(n_direct_da)],
        ("data analyst", "agency"): [job(4000 + i, "BI Data Analyst") for i in range(n_agency_da)],
    }


def test_pages_through_everything_and_tags_agency(db, settings):
    api = FakeReed(market())
    s = run_reed.run(settings, fetch=api, conn=db, max_details=0)
    assert s["status"] == "success"
    de_direct = next(q for q in s["queries"] if q["board"] == "search:data-engineer:direct")
    assert de_direct["pages"] == 3 and de_direct["fetched"] == 250        # 100 + 100 + 50
    assert count(db, "raw.job_observation") == 340
    tags = dict(db.execute("SELECT posted_by, count(*) FROM raw.reed_search_hit "
                           "GROUP BY posted_by").fetchall())
    assert tags == {"direct": 280, "agency": 60}
    assert db.execute("SELECT status FROM ops.source_run WHERE board='search'").fetchone() == ("success",)
    # run totals must not double-count the 'search' summary row (bug fixed in migration 003)
    assert db.execute("SELECT records_fetched FROM ops.pipeline_run").fetchone() == (340,)


def test_job_found_by_two_searches_is_observed_once_with_two_hits(db, settings):
    m = market(n_direct_de=1, n_agency_de=0, n_direct_da=0, n_agency_da=0)
    m[("data analyst", "direct")] = [job(1000, "Data Engineer / Data Analyst")]   # same id
    run_reed.run(settings, fetch=FakeReed(m), conn=db, max_details=0)
    assert count(db, "raw.job_observation") == 1
    assert count(db, "raw.reed_search_hit") == 2


def test_detail_queue_title_filter_cap_and_carry_over(db, settings):
    api = FakeReed(market())
    # 340 jobs; 50 'Platform Engineer' titles don't match -> 290 in the queue
    s1 = run_reed.run(settings, fetch=api, conn=db, max_details=100)
    assert s1["detail"]["queue"] == 290
    assert s1["detail"]["loaded"] == 100 and s1["detail"]["remaining"] == 190
    assert api.detail_calls() == 100

    # a second run TODAY with a daily cap of 200: earlier run's 100 count against it
    s2 = run_reed.run(settings, fetch=api, conn=db, max_details=200)
    assert s2["detail"]["queue"] == 190                                  # carried over, not restarted
    assert s2["detail"]["cap_today"] == 100
    assert db.execute("SELECT count(*) FROM raw.job_payload WHERE board='detail'").fetchone()[0] == 200
    titles = db.execute("""SELECT DISTINCT p.payload->>'jobTitle' FROM raw.job_payload p
                           JOIN raw.job_payload d ON d.source_job_id = p.source_job_id
                                                 AND d.board = 'detail'
                           WHERE p.board = 'search'""").fetchall()
    assert ("Platform Engineer",) not in titles                          # never paid for


def test_newest_jobs_get_details_first(db, settings):
    m = {("data engineer", "direct"): [job(1, "Data Engineer", "01/09/2026"),
                                       job(2, "Data Engineer", "14/10/2026"),
                                       job(3, "Data Engineer", "20/09/2026")]}
    api = FakeReed(m)
    run_reed.run(settings, fetch=api, conn=db, max_details=2)
    asked = [p for p, _ in api.calls if p.startswith("/jobs/")]
    assert asked == ["/jobs/2", "/jobs/3"]


def test_removed_job_404_is_recorded_not_fatal(db, settings):
    api = FakeReed(market(n_direct_de=3, n_agency_de=0, n_direct_da=0, n_agency_da=0),
                   missing_details={"1001"})
    s = run_reed.run(settings, fetch=api, conn=db, max_details=10)
    assert s["detail"]["not_found"] == 1 and s["detail"]["loaded"] == 1   # 1000 is 'Platform Engineer'
    assert s["status"] == "success"
    assert db.execute("SELECT reason FROM raw.rejected_record").fetchall() == [("detail 404 for job 1001",)]


def test_rate_limit_stops_calling_reed_for_the_day(db, settings):
    api = FakeReed(market(), rate_limit_after=4)        # 429 on the 5th call
    s = run_reed.run(settings, fetch=api, conn=db, max_details=100)
    assert s["rate_limited"] and s["status"] == "partial"
    assert len(api.calls) == 5                           # no hammering after the 429
    skipped = [q for q in s["queries"] if "skipped" in (q["error"] or "")]
    assert len(skipped) >= 1
    assert api.detail_calls() == 0
    assert db.execute("SELECT status FROM ops.source_run WHERE board='search'").fetchone() == ("failed",)


def test_one_failing_query_marks_search_incomplete(db, settings):
    api = FakeReed(market(), fail_queries={("data analyst", "agency")})
    s = run_reed.run(settings, fetch=api, conn=db, max_details=0)
    assert s["status"] == "partial" and s["queries_failed"] == 1
    # the safety rule for later closure logic: today's search is NOT complete
    assert db.execute("SELECT status FROM ops.source_run WHERE board='search'").fetchone() == ("failed",)


def test_rerun_is_idempotent_and_ignores_application_count_changes(db, settings):
    m = market()
    run_reed.run(settings, fetch=FakeReed(m), conn=db, max_details=0)
    for jobs in m.values():
        for j in jobs:
            j["applications"] += 50                       # only the volatile field moves
    # resume=False: really fetch everything again, to prove the LOAD is idempotent
    s = run_reed.run(settings, fetch=FakeReed(m), conn=db, max_details=0, resume=False)
    assert s["new_observations"] == 0
    assert s["new_or_changed_content"] == 0
    assert count(db, "raw.job_observation") == 340


def test_database_outage_then_reload_restores_reed_search(db, settings):
    down = replace(settings, pg_port=1)
    s = run_reed.run(down, fetch=FakeReed(market()), max_details=50)
    assert s["db_available"] is False and s["status"] == "partial"
    assert "database unavailable" in s["detail"]["error"]

    reload_files.reload(settings, conn=db)
    assert count(db, "raw.job_observation") == 340
    assert count(db, "raw.reed_search_hit") == 340
    boards = {b for (b,) in db.execute("SELECT board FROM ops.source_run").fetchall()}
    assert "search:data-engineer:direct" in boards
    # the summary row closure logic depends on must be rebuilt too
    assert db.execute("SELECT status FROM ops.source_run WHERE board='search'").fetchone() == ("success",)


def test_missing_api_key_fails_clearly(settings):
    s = run_reed.run(replace(settings, reed_api_key=""))
    assert s["status"] == "failed" and s["error"] == "REED_API_KEY missing"


def test_migration_003_repairs_doubled_totals_from_before_the_fix(db, settings):
    from ingestion.db import migrate
    run_reed.run(settings, fetch=FakeReed(market()), conn=db, max_details=0)
    db.execute("UPDATE ops.pipeline_run SET records_fetched = 680")          # the old bug
    db.execute("DELETE FROM ops.schema_migration WHERE version = '003_fix_reed_run_totals.sql'")
    db.commit()
    assert migrate(db, settings.migrations_dir) == ["003_fix_reed_run_totals.sql"]
    assert db.execute("SELECT records_fetched FROM ops.pipeline_run").fetchone() == (340,)


# ---------------- resume after an interrupted run (Phase 6, 30 Oct 2026) ----------------
# On the laptop, Airflow killed Reed runs half-way when the Mac slept. A retry must finish
# the day cheaply: only what is missing, never a second full sweep, never after a 429.

def test_retry_fetches_only_the_searches_that_failed(db, settings):
    m = market()
    run_reed.run(settings, fetch=FakeReed(m, fail_queries={("data analyst", "agency")}),
                 conn=db, max_details=0)
    retry = FakeReed(m)
    s = run_reed.run(settings, fetch=retry, conn=db, max_details=0)
    searched = {(p["keywords"], "direct" if "postedByDirectEmployer" in p else "agency")
                for path, p in retry.calls if path == "/search"}
    assert searched == {("data analyst", "agency")}                # 1 page, not ~5
    assert s["status"] == "success" and s["resumed_searches"] == 3
    # and the day now counts as COMPLETE for the closure rule
    assert db.execute("""SELECT bool_or(status = 'success') FROM ops.source_run
                         WHERE board = 'search'""").fetchone() == (True,)


def test_no_calls_at_all_if_reed_rate_limited_us_earlier_today(db, settings):
    run_reed.run(settings, fetch=FakeReed(market(), rate_limit_after=4), conn=db, max_details=100)
    retry = FakeReed(market())
    s = run_reed.run(settings, fetch=retry, conn=db, max_details=100)
    assert retry.calls == []
    assert s["rate_limited"] and s["status"] != "success"


def test_detail_cap_is_per_day_not_per_run(db, settings):
    api = FakeReed(market())
    run_reed.run(settings, fetch=api, conn=db, max_details=60)
    run_reed.run(settings, fetch=api, conn=db, max_details=60)    # same day, e.g. an Airflow retry
    assert api.detail_calls() == 60


def test_no_resume_flag_sweeps_everything_again(db, settings):
    run_reed.run(settings, fetch=FakeReed(market()), conn=db, max_details=0)
    again = FakeReed(market())
    run_reed.run(settings, fetch=again, conn=db, max_details=0, resume=False)
    assert sum(1 for p, _ in again.calls if p == "/search") == 6     # 3 + 1 + 1 + 1 pages
