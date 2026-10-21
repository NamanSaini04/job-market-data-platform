"""
Generate dbt seed CSVs from config/*.yaml, so config stays the ONE place to edit.

  config/roles.yaml          -> dbt/seeds/role_patterns.csv  (role_family, pattern, priority)
  config/ats_companies.yaml  -> dbt/seeds/ats_companies.csv  (source, board, company_name)

priority = order of the family in roles.yaml. First matching family wins, so put
specific families before general ones.

Run:  python scripts/export_seeds.py         (also run by scripts/run_daily.sh)
Test: tests/test_dbt_seeds.py fails if the CSVs are out of date.
"""

import csv
import io
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SEEDS = ROOT / "dbt" / "seeds"


def role_patterns_csv() -> str:
    roles = yaml.safe_load((ROOT / "config/roles.yaml").read_text())["role_families"]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["role_family", "pattern", "priority"])
    for priority, fam in enumerate(roles, start=1):
        for pattern in fam["title_patterns"]:
            w.writerow([fam["name"], pattern.lower(), priority])
    return buf.getvalue()


def ats_companies_csv() -> str:
    companies = yaml.safe_load((ROOT / "config/ats_companies.yaml").read_text())["companies"]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["source", "board", "company_name"])
    for c in companies:
        w.writerow([c["ats"], c["slug"], c["company"]])
    return buf.getvalue()


EXPORTS = {"role_patterns.csv": role_patterns_csv, "ats_companies.csv": ats_companies_csv}


def main() -> int:
    SEEDS.mkdir(parents=True, exist_ok=True)
    for name, build in EXPORTS.items():
        (SEEDS / name).write_text(build())
        print(f"wrote dbt/seeds/{name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
