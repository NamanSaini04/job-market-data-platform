"""
A fake Reed API for tests. Behaves like the real one where it matters:
paging with resultsToTake/resultsToSkip, the direct/agency split, 404 for
removed jobs, and 429 rate limiting. Job records are copies of REAL Reed
responses (tests/fixtures), with only id, title and date changed.
"""

import copy
import json

from conftest import fixture_records
from ingestion.run_reed import FetchError, RateLimited

SEARCH_TEMPLATE = fixture_records("reed_search")[1]     # a real 'Data Engineer' result
DETAIL_TEMPLATE = fixture_records("reed_detail")[0]


def job(job_id: int, title: str, date: str = "10/10/2026", applications: int = 5) -> dict:
    j = copy.deepcopy(SEARCH_TEMPLATE)
    j.update(jobId=job_id, jobTitle=title, date=date, applications=applications)
    return j


class FakeReed:
    def __init__(self, jobs: dict[tuple[str, str], list[dict]], *, missing_details=(),
                 rate_limit_after: int | None = None, fail_queries=()):
        self.jobs = jobs                        # (keywords, 'direct'|'agency') -> list of jobs
        self.missing_details = set(missing_details)
        self.rate_limit_after = rate_limit_after
        self.fail_queries = set(fail_queries)
        self.calls: list[tuple[str, dict | None]] = []

    def __call__(self, path: str, params: dict | None) -> bytes:
        self.calls.append((path, params))
        if self.rate_limit_after is not None and len(self.calls) > self.rate_limit_after:
            raise RateLimited("HTTP 429")
        if path == "/search":
            posted_by = "direct" if "postedByDirectEmployer" in params else "agency"
            key = (params["keywords"], posted_by)
            if key in self.fail_queries:
                raise FetchError(503)
            all_jobs = self.jobs.get(key, [])
            skip, take = params["resultsToSkip"], params["resultsToTake"]
            return json.dumps({"results": all_jobs[skip:skip + take],
                               "totalResults": len(all_jobs)}).encode()
        if path.startswith("/jobs/"):
            job_id = path.rsplit("/", 1)[1]
            if job_id in self.missing_details:
                raise FetchError(404)
            d = copy.deepcopy(DETAIL_TEMPLATE)
            d["jobId"] = int(job_id)
            return json.dumps(d).encode()
        raise AssertionError(f"unexpected path {path}")

    def detail_calls(self) -> int:
        return sum(1 for p, _ in self.calls if p.startswith("/jobs/"))
