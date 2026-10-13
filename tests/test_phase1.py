"""
Phase 1 tests. No network needed.

They check two things:
  1. The config files are valid (a broken config would silently break the pipeline later).
  2. The small helper functions in check_sources.py behave correctly.
"""

from pathlib import Path

import yaml

from scripts.check_sources import ATS_URLS, extract_records, first_verified_per_ats, trim

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    return yaml.safe_load((ROOT / "config" / name).read_text())


# ---------- config: ats_companies.yaml ----------

def test_every_company_has_required_fields():
    for c in load("ats_companies.yaml")["companies"]:
        assert {"company", "ats", "slug", "status"} <= c.keys(), c


def test_ats_types_are_supported():
    for c in load("ats_companies.yaml")["companies"]:
        assert c["ats"] in ATS_URLS, f"unknown ats '{c['ats']}' for {c['company']}"


def test_status_values_are_allowed():
    for c in load("ats_companies.yaml")["companies"]:
        assert c["status"] in {"verified", "unverified"}, c


def test_no_duplicate_boards():
    pairs = [(c["ats"], c["slug"]) for c in load("ats_companies.yaml")["companies"]]
    assert len(pairs) == len(set(pairs)), "same board listed twice"


# ---------- config: roles.yaml ----------

def test_six_starting_role_families():
    names = [f["name"] for f in load("roles.yaml")["role_families"]]
    assert set(names) == {
        "Data Analyst", "Data Engineer", "Data Scientist",
        "ML Engineer", "AI Engineer", "Analytics Engineer",
    }


def test_title_patterns_are_lowercase_and_not_empty():
    for fam in load("roles.yaml")["role_families"]:
        assert fam["search_terms"] and fam["title_patterns"], fam["name"]
        for p in fam["title_patterns"]:
            assert p == p.lower(), f"pattern '{p}' must be lowercase"


def test_no_pattern_belongs_to_two_families():
    seen = {}
    for fam in load("roles.yaml")["role_families"]:
        for p in fam["title_patterns"]:
            assert p not in seen, f"'{p}' in both {seen.get(p)} and {fam['name']}"
            seen[p] = fam["name"]


# ---------- helpers ----------

def test_extract_records_handles_lever_list():
    assert extract_records([{"id": 1}]) == [{"id": 1}]


def test_extract_records_handles_jobs_dict():
    assert extract_records({"jobs": [{"id": 1}], "meta": {}}) == [{"id": 1}]


def test_extract_records_handles_bad_input():
    assert extract_records(None) == []
    assert extract_records({"error": "not found"}) == []


def test_trim_shortens_long_values_only():
    out = trim({"short": "abc", "long": "x" * 200}, max_len=10)
    assert out["short"] == "abc"
    assert out["long"] == "x" * 10 + "..."


def test_first_verified_per_ats_skips_unverified_and_duplicates():
    companies = [
        {"ats": "greenhouse", "slug": "a", "status": "unverified"},
        {"ats": "greenhouse", "slug": "b", "status": "verified"},
        {"ats": "greenhouse", "slug": "c", "status": "verified"},
        {"ats": "ashby", "slug": "d", "status": "verified"},
    ]
    assert first_verified_per_ats(companies) == [("greenhouse", "b"), ("ashby", "d")]
