"""
Check every company board in config/ats_companies.yaml.

For each board it reports:
  - HTTP status (200 = board exists, 404 = wrong slug)
  - total jobs on the board
  - how many titles match one of our role families (config/roles.yaml)

Run:  python scripts/verify_ats_slugs.py
Then update `status:` in config/ats_companies.yaml by hand
(verified if OK, delete the line if 404). We don't auto-edit the config:
a human should decide what goes into the pipeline.
"""

import sys
import time
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.check_sources import ATS_URLS, extract_records  # noqa: E402
from ingestion.roles import title_matches  # noqa: E402

TIMEOUT = 20
PAUSE_SECONDS = 1  # be polite: one request per second


def title_of(job: dict) -> str:
    # Greenhouse/Ashby use "title", Lever uses "text"
    return (job.get("title") or job.get("text") or "").lower()


def load_patterns() -> list[str]:
    roles = yaml.safe_load((ROOT / "config/roles.yaml").read_text())
    return [p for fam in roles["role_families"] for p in fam["title_patterns"]]


def main() -> int:
    companies = yaml.safe_load((ROOT / "config/ats_companies.yaml").read_text())["companies"]
    patterns = load_patterns()

    print(f"{'company':<20}{'ats':<12}{'slug':<18}{'http':<6}{'jobs':<6}{'data roles':<10}")
    print("-" * 72)
    for c in companies:
        url = ATS_URLS[c["ats"]].format(slug=c["slug"])
        try:
            r = requests.get(url, timeout=TIMEOUT)
            if r.status_code != 200:
                print(f"{c['company']:<20}{c['ats']:<12}{c['slug']:<18}{r.status_code:<6}{'-':<6}{'-':<10}")
                continue
            jobs = extract_records(r.json())
            matches = sum(1 for j in jobs if title_matches(title_of(j), patterns))
            print(f"{c['company']:<20}{c['ats']:<12}{c['slug']:<18}{200:<6}{len(jobs):<6}{matches:<10}")
        except Exception as e:
            print(f"{c['company']:<20}{c['ats']:<12}{c['slug']:<18}ERROR {e}")
        time.sleep(PAUSE_SECONDS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
