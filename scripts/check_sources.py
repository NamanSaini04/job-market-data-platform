"""
Phase 1 source check.

Purpose: before writing any pipeline, confirm that
  1. each API key works,
  2. each source returns the fields we think it returns.

Run:
    python scripts/check_sources.py           # print a profile of each source
    python scripts/check_sources.py --save    # also save 3 sample records per source
                                              # to tests/fixtures/ (used by Phase 2 tests)

Needs: pip install -r requirements.txt
       a .env file (copy .env.example) with REED_API_KEY (Adzuna keys optional)
"""

import argparse
import json
import os
import sys
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
TIMEOUT = 20

ATS_URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true",
}


# ---------- small pure helpers (unit-tested in tests/test_phase1.py) ----------

def extract_records(data) -> list:
    """Lever returns a bare list; Greenhouse and Ashby return {"jobs": [...]}."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("jobs", [])
    return []


def trim(record: dict, max_len: int = 120) -> dict:
    """Shorten long values so the printout stays readable."""
    return {k: (str(v)[:max_len] + "...") if len(str(v)) > max_len else v for k, v in record.items()}


def first_verified_per_ats(companies: list[dict]) -> list[tuple[str, str]]:
    """Pick one verified board per ATS type to profile."""
    seen, picks = set(), []
    for c in companies:
        if c.get("status") == "verified" and c["ats"] not in seen:
            seen.add(c["ats"])
            picks.append((c["ats"], c["slug"]))
    return picks


# ---------- output ----------

def show(name: str, records: list, total=None) -> None:
    print(f"\n=== {name} ===")
    print(f"records returned: {len(records)}" + (f" (source says total={total})" if total is not None else ""))
    if not records:
        print("NO RECORDS: check slug / key / query")
        return
    print("fields:", sorted(records[0].keys()))
    print(json.dumps(trim(records[0]), indent=2, default=str))


def save(name: str, records: list, enabled: bool) -> None:
    if not enabled or not records:
        return
    FIXTURES.mkdir(parents=True, exist_ok=True)
    path = FIXTURES / f"{name.replace(':', '_')}.json"
    path.write_text(json.dumps(records[:3], indent=2, default=str))
    print(f"saved 3 sample records -> {path.relative_to(ROOT)}")


# ---------- source checks ----------

def check_ats(save_samples: bool) -> bool:
    companies = yaml.safe_load((ROOT / "config/ats_companies.yaml").read_text())["companies"]
    ok = True
    for ats, slug in first_verified_per_ats(companies):
        url = ATS_URLS[ats].format(slug=slug)
        try:
            r = requests.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            records = extract_records(r.json())
            show(f"{ats}:{slug}", records)
            save(ats, records, save_samples)
        except Exception as e:  # Phase 1 only: report and continue
            print(f"\n=== {ats}:{slug} === FAILED: {e}")
            ok = False
    return ok


def check_reed(save_samples: bool) -> bool:
    key = os.getenv("REED_API_KEY")
    if not key:
        print("\n=== reed === SKIPPED: REED_API_KEY missing")
        return False
    try:
        # Reed uses HTTP Basic auth: key as username, empty password.
        r = requests.get(
            "https://www.reed.co.uk/api/1.0/search",
            params={"keywords": "data engineer", "resultsToTake": 5},
            auth=(key, ""),
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])
        show("reed:search", results, data.get("totalResults"))
        save("reed_search", results, save_samples)

        # Second call: detail endpoint (full description, expiry, salary type).
        if results:
            job_id = results[0]["jobId"]
            d = requests.get(f"https://www.reed.co.uk/api/1.0/jobs/{job_id}", auth=(key, ""), timeout=TIMEOUT)
            d.raise_for_status()
            show("reed:detail", [d.json()])
            save("reed_detail", [d.json()], save_samples)
        return True
    except Exception as e:
        print(f"\n=== reed === FAILED: {e}")
        return False


def check_adzuna(save_samples: bool) -> bool:
    app_id, app_key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        print("\n=== adzuna === SKIPPED: ADZUNA_APP_ID / ADZUNA_APP_KEY missing")
        return False
    try:
        r = requests.get(
            "https://api.adzuna.com/v1/api/jobs/gb/search/1",
            params={
                "app_id": app_id,
                "app_key": app_key,
                "what": "data engineer",
                "results_per_page": 5,
                "sort_by": "date",
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])
        show("adzuna:search", results, data.get("count"))
        predicted = sum(1 for j in results if str(j.get("salary_is_predicted")) == "1")
        print(f"salary_is_predicted=1 in {predicted}/{len(results)} records")
        save("adzuna_search", results, save_samples)
        return True
    except requests.HTTPError as e:
        # Never print the exception text: it contains the URL, which contains app_key.
        print(f"\n=== adzuna === FAILED: HTTP {e.response.status_code} (check app_id / app_key)")
        return False
    except Exception as e:
        print(f"\n=== adzuna === FAILED: {type(e).__name__} (details hidden: URL contains app_key)")
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--save", action="store_true", help="save sample records to tests/fixtures/")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    results = {
        "ats": check_ats(args.save),
        "reed": check_reed(args.save),
    }
    # Adzuna is optional (deferred to v2). Only checked if keys are present.
    if os.getenv("ADZUNA_APP_ID") and os.getenv("ADZUNA_APP_KEY"):
        results["adzuna"] = check_adzuna(args.save)
    else:
        print("\n=== adzuna === not configured (optional, deferred to v2)")
    print("\nSUMMARY:", results)
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
