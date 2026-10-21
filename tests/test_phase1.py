"""
Phase 1 tests. No network needed.

They check two things:
  1. The config files are valid (a broken config would silently break the pipeline later).
  2. The small helper functions in check_sources.py behave correctly.
"""

import re
from pathlib import Path

import pytest
import yaml

from ingestion.roles import all_patterns, classify, title_matches
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

def test_six_starting_role_families_are_still_tracked():
    names = [f["name"] for f in load("roles.yaml")["role_families"]]
    assert {
        "Data Analyst", "Data Engineer", "Data Scientist",
        "ML Engineer", "AI Engineer", "Analytics Engineer",
    } <= set(names)
    assert len(names) == len(set(names)), "duplicate family name"


def test_suspected_training_course_titles_do_not_match_any_role():
    # Evidence 17 Oct 2026: these titles came from suspected course advertisers.
    # Widening patterns must never let them in by accident.
    patterns = [p for f in load("roles.yaml")["role_families"] for p in f["title_patterns"]]
    for title in ["Data Science Trainee", "Junior AI Developer", "Trainee AI Programmer",
                  "Entry Level Grad Scheme"]:
        assert not title_matches(title, patterns), title


def test_title_patterns_are_lowercase_and_not_empty():
    for fam in load("roles.yaml")["role_families"]:
        assert fam["search_terms"] and fam["title_patterns"], fam["name"]
        for p in fam["title_patterns"]:
            assert p == p.lower(), f"pattern '{p}' must be lowercase"


def test_patterns_are_valid_regex():
    for p in all_patterns(load("roles.yaml")):
        re.compile(p)
        assert not re.search(r"\\[bBdDwWsS]|\(\?[=!<]", p), f"'{p}': syntax Postgres reads differently"
        assert not re.compile(p).search(""), f"'{p}' matches an empty title"


# Real titles from Reed, 21 Oct 2026 (BI Developer review). The family a title must get.
REAL_TITLES = {
    "Power BI Developer": "BI Developer",
    "BI Developer - Banking": "BI Developer",
    "Senior Business Intelligence Developer": "BI Developer",
    "Power BI & Application Developer - AI": "BI Developer",
    "Power BI Developer & Data Modeller": "BI Developer",
    "Power BI & Data Modeller": "BI Developer",
    "Application Developer - Power BI | React": "BI Developer",
    "Head of Data Services": "Data Leadership",
    "Chief Data Officer": "Data Leadership",
    "Senior Data Engineer (Azure)": "Data Engineer",
    "Lead Analytics Engineer": "Analytics Engineer",
    "Senior Data Analyst - Power BI": "Data Analyst",
    # mention the tool, are not BI developers -> no family
    "Business Analyst | Power BI | £60,000 + Bonus": None,
    "Finance Business Partner (Power BI dashboards, Group Reporting)": None,
    "Finance Business Partner Power BI dashboards, Group Reporting - Morgan McKinley": None,
    "Systems Accountant (Power BI)": None,
    "Data Science Trainee": None,
}


@pytest.mark.parametrize("title,family", REAL_TITLES.items())
def test_real_titles_get_the_right_family(title, family):
    assert classify(title, load("roles.yaml")) == family


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
