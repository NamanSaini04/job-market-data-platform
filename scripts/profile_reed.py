"""
Profile Reed before designing the pipeline around it.

Answers, per role search term (from config/roles.yaml):
  1. How many results does Reed have in total?            -> volume and paging cost
  2. What share is posted by recruitment agencies?         -> can we trust "employer"?
  3. How many are flagged graduate?                        -> graduate-roles question
  4. How many results actually have a matching job title?  -> how loose Reed's keyword search is
  5. Salary fill rate, and the gap between posted and expiry dates

Uses search calls only (no detail calls). About 35 calls (5 per search term), 1 second apart.
Prints counts only, never the API key.

Run:  python scripts/profile_reed.py
"""

import os
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ingestion.roles import title_matches  # noqa: E402
URL = "https://www.reed.co.uk/api/1.0/search"
PAUSE = 1.0


def search(key: str, **params) -> dict:
    r = requests.get(URL, params=params, auth=(key, ""), timeout=30)
    r.raise_for_status()
    time.sleep(PAUSE)
    return r.json()


def total(key: str, term: str, **flags) -> int:
    """Only need the count, so ask for 1 result."""
    return search(key, keywords=term, resultsToTake=1, **flags).get("totalResults", 0)


def days_between(a: str, b: str):
    try:
        return (datetime.strptime(b, "%d/%m/%Y") - datetime.strptime(a, "%d/%m/%Y")).days
    except (TypeError, ValueError):
        return None


def main() -> int:
    load_dotenv(ROOT / ".env")
    key = os.getenv("REED_API_KEY")
    if not key:
        print("REED_API_KEY missing in .env")
        return 1

    roles = yaml.safe_load((ROOT / "config/roles.yaml").read_text())["role_families"]
    all_patterns = [p for f in roles for p in f["title_patterns"]]

    print(f"{'term':<28}{'total':>7}{'agency':>8}{'direct':>8}{'grad':>6}"
          f"{'title_ok':>10}{'salary':>8}")
    print("-" * 75)

    grand = Counter()
    gaps = Counter()
    try:
        for fam in roles:
            for term in fam["search_terms"]:
                t = total(key, term)
                ag = total(key, term, postedByRecruitmentAgency="true")
                di = total(key, term, postedByDirectEmployer="true")
                gr = total(key, term, graduate="true")

                # one page of 100 to measure quality
                page = search(key, keywords=term, resultsToTake=100).get("results", [])
                n = len(page) or 1
                title_ok = sum(title_matches(j.get("jobTitle"), all_patterns) for j in page)
                salary = sum(j.get("minimumSalary") is not None for j in page)
                for j in page:
                    g = days_between(j.get("date"), j.get("expirationDate"))
                    if g is not None:
                        gaps[g] += 1

                print(f"{term:<28}{t:>7}{ag:>8}{di:>8}{gr:>6}"
                      f"{title_ok * 100 // n:>9}%{salary * 100 // n:>7}%")
                grand.update(total=t, agency=ag, direct=di, grad=gr)
    except requests.HTTPError as e:
        print(f"\nStopped: HTTP {e.response.status_code}. If 429, Reed is rate-limiting: wait and retry.")
        return 1

    print("-" * 75)
    print(f"{'ALL TERMS (overlapping)':<28}{grand['total']:>7}{grand['agency']:>8}{grand['direct']:>8}{grand['grad']:>6}")
    if grand["total"]:
        print(f"\nagency share: {grand['agency'] * 100 // grand['total']}%   "
              f"direct share: {grand['direct'] * 100 // grand['total']}%   "
              f"(if these don't add to ~100%, the flags overlap or some jobs have neither)")
    print("\ndays from posted to expiry (top 5):",
          ", ".join(f"{d} days x{c}" for d, c in gaps.most_common(5)))
    print("If one value dominates, expiry is a fixed listing period, not a real closing date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
