"""Unit tests: parsing, validation, hashing, raw files. No network, no database."""

import json
import uuid
from datetime import date

import pytest

from conftest import fixture_records
from ingestion.extractors.ats import SchemaChangedError, board_url, parse_board
from ingestion.loader import payload_hash
from ingestion.raw_files import parse_raw_path, raw_path, read_raw, write_raw


# ---------- parsing real responses ----------

@pytest.mark.parametrize("source,wrap", [
    ("greenhouse", lambda jobs: {"jobs": jobs, "meta": {}}),
    ("lever", lambda jobs: jobs),
    ("ashby", lambda jobs: {"jobs": jobs}),
])
def test_parses_real_sample_responses(source, wrap):
    jobs = fixture_records(source)
    good, bad = parse_board(source, "board", wrap(jobs))
    assert len(good) == 3 and bad == []
    assert [r.source_job_id for r in good] == [str(j["id"]) for j in jobs]
    assert all(isinstance(r.source_job_id, str) for r in good)   # Greenhouse ids are numbers


def test_record_level_validation_rejects_bad_jobs_but_keeps_good_ones():
    data = {"jobs": [
        {"id": 1, "title": "Data Engineer"},
        {"title": "No id"},
        {"id": 2, "title": "   "},
        {"id": 1, "title": "Same id again"},
        "not even an object",
    ]}
    good, bad = parse_board("greenhouse", "b", data)
    assert [r.source_job_id for r in good] == ["1"]
    assert sorted(r.reason for r in bad) == sorted([
        "missing 'id'", "missing 'title'", "duplicate id in response", "job is not an object"])


@pytest.mark.parametrize("source,data", [
    ("greenhouse", [{"id": 1}]),            # list where an object was expected
    ("ashby", {"postings": []}),            # renamed key
    ("lever", {"jobs": []}),                # object where a list was expected
])
def test_response_shape_change_fails_loudly(source, data):
    with pytest.raises(SchemaChangedError):
        parse_board(source, "b", data)


def test_urls_include_the_parameters_phase_1_showed_we_need():
    assert "content=true" in board_url("greenhouse", "monzo")
    assert "includeCompensation=true" in board_url("ashby", "faculty")


# ---------- hashing ----------

def test_hash_ignores_key_order_but_not_content():
    a = {"id": 1, "title": "Data Engineer", "loc": {"city": "London"}}
    b = {"loc": {"city": "London"}, "title": "Data Engineer", "id": 1}
    c = {"id": 1, "title": "Senior Data Engineer", "loc": {"city": "London"}}
    assert payload_hash(a) == payload_hash(b)
    assert payload_hash(a) != payload_hash(c)


# ---------- raw files ----------

def test_raw_file_round_trip_and_name_parsing(tmp_path):
    run_id = uuid.uuid4()
    body = json.dumps({"jobs": [{"id": 1, "title": "x"}]}).encode()
    path = write_raw(raw_path(tmp_path, "greenhouse", date(2026, 10, 15), "monzo", run_id), body)
    assert path.name.endswith(".json.gz")
    assert read_raw(path) == body                      # exact bytes back
    info = parse_raw_path(path)
    assert (info.source, info.board, info.run_id, info.observed_date) == \
           ("greenhouse", "monzo", run_id, date(2026, 10, 15))
    assert not list(tmp_path.rglob("*.tmp"))           # no half-written leftovers
