"""
Reed Jobseeker API: parsing, validation and the title filter.

Phase 1 findings this code is built on:
  * Search is loose: many results for "data engineer" are not data engineer jobs.
    So every job is re-classified by TITLE using config/roles.yaml.
  * No date filter or sort: the only way to find new jobs is to page through
    every result for every query and compare job IDs with what we have.
  * postedByDirectEmployer / postedByRecruitmentAgency split results exactly,
    so running each query twice tags every job for free.
  * Search gives a short snippet; the full description (needed for skills)
    is only in the detail endpoint: 1 call per job. That is the expensive part.
"""

import re
from typing import Any

from ingestion.extractors.ats import RawRecord, Rejected, SchemaChangedError

API = "https://www.reed.co.uk/api/1.0"
PAGE_SIZE = 100                       # Reed's maximum
POSTED_BY = {"direct": "postedByDirectEmployer", "agency": "postedByRecruitmentAgency"}

# Fields that change constantly without the job changing. Left in the stored
# payload, but ignored when deciding "has this job's content changed?".
# Without this, every Reed job would look "changed" every day.
VOLATILE_FIELDS = {"search": ("applications",), "detail": ("applicationCount",)}


def slug(term: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-")


def search_params(term: str, posted_by: str, page: int) -> dict:
    return {"keywords": term, POSTED_BY[posted_by]: "true",
            "resultsToTake": PAGE_SIZE, "resultsToSkip": page * PAGE_SIZE}


def parse_search_page(data: Any) -> tuple[list[RawRecord], list[Rejected], int]:
    """One search page -> (valid records, rejected records, totalResults)."""
    if (not isinstance(data, dict) or not isinstance(data.get("results"), list)
            or not isinstance(data.get("totalResults"), int)):
        raise SchemaChangedError("expected {'results': [...], 'totalResults': int}")
    good, bad = [], []
    for job in data["results"]:
        if not isinstance(job, dict):
            bad.append(Rejected("reed", "search", "job is not an object", job))
        elif job.get("jobId") in (None, ""):
            bad.append(Rejected("reed", "search", "missing 'jobId'", job))
        elif not str(job.get("jobTitle") or "").strip():
            bad.append(Rejected("reed", "search", "missing 'jobTitle'", job))
        else:
            good.append(RawRecord("reed", "search", str(job["jobId"]), job))
    return good, bad, data["totalResults"]


def parse_detail(job_id: str, data: Any) -> RawRecord:
    if not isinstance(data, dict) or str(data.get("jobId")) != job_id:
        raise SchemaChangedError(f"detail for {job_id}: unexpected shape or jobId mismatch")
    return RawRecord("reed", "detail", job_id, data)


def title_patterns(roles_config: dict) -> list[str]:
    return [p for fam in roles_config["role_families"] for p in fam["title_patterns"]]


def title_matches(title: str, patterns: list[str]) -> bool:
    t = (title or "").lower()
    return any(p in t for p in patterns)
