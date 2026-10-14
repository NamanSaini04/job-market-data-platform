"""
Extractors for company job boards: Greenhouse, Lever, Ashby.

Each ATS differs in only four ways: URL, where the job list sits in the response,
the name of the id field, and the name of the title field. So one class with a
small spec per ATS, instead of three near-identical classes.

Two levels of validation:
  * Response level: is the response the shape we expect at all?
    If not, the source changed its API (a schema change). The whole board fails
    loudly. We never silently load garbage.
  * Record level: does each job have an id and a title?
    Bad records are rejected one by one and kept for inspection; good records load.
"""

from dataclasses import dataclass
from typing import Any, Callable


class SchemaChangedError(Exception):
    """The response is not the shape we expect. The source may have changed its API."""


@dataclass(frozen=True)
class AtsSpec:
    url: str                               # with {slug}
    get_jobs: Callable[[Any], list]        # response JSON -> list of job dicts
    id_field: str
    title_field: str


def _jobs_key(data: Any) -> list:
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise SchemaChangedError("expected an object with a 'jobs' list")
    return data["jobs"]


def _bare_list(data: Any) -> list:
    if not isinstance(data, list):
        raise SchemaChangedError("expected a JSON list")
    return data


SPECS: dict[str, AtsSpec] = {
    # content=true: without it Greenhouse sends no description (Phase 1 finding).
    "greenhouse": AtsSpec("https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
                          _jobs_key, "id", "title"),
    "lever":      AtsSpec("https://api.lever.co/v0/postings/{slug}?mode=json",
                          _bare_list, "id", "text"),
    # includeCompensation=true: without it Ashby sends no salary (Phase 1 finding).
    "ashby":      AtsSpec("https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true",
                          _jobs_key, "id", "title"),
}


@dataclass
class RawRecord:
    source: str
    board: str
    source_job_id: str
    payload: dict


@dataclass
class Rejected:
    source: str
    board: str
    reason: str
    payload: Any


def board_url(source: str, slug: str) -> str:
    return SPECS[source].url.format(slug=slug)


def parse_board(source: str, board: str, data: Any) -> tuple[list[RawRecord], list[Rejected]]:
    """Turn one board's response JSON into valid records and rejected records."""
    spec = SPECS[source]
    jobs = spec.get_jobs(data)   # may raise SchemaChangedError

    good: list[RawRecord] = []
    bad: list[Rejected] = []
    seen: set[str] = set()
    for job in jobs:
        if not isinstance(job, dict):
            bad.append(Rejected(source, board, "job is not an object", job))
            continue
        job_id = job.get(spec.id_field)
        if job_id in (None, ""):
            bad.append(Rejected(source, board, f"missing '{spec.id_field}'", job))
            continue
        if not str(job.get(spec.title_field) or "").strip():
            bad.append(Rejected(source, board, f"missing '{spec.title_field}'", job))
            continue
        job_id = str(job_id)
        if job_id in seen:   # same job listed twice in one response
            bad.append(Rejected(source, board, "duplicate id in response", job))
            continue
        seen.add(job_id)
        good.append(RawRecord(source, board, job_id, job))
    return good, bad
