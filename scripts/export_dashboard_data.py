"""
Export the dbt marts the public dashboard needs to small CSV files in dashboard/data/.

Why files, not a live database connection:
  * The database runs on a laptop (Docker). A public website cannot, and should not, reach it.
  * No cloud database account, no password stored on a hosting site.
  * CSVs are a few KB, readable on GitHub, and every change shows up in the git diff.

What is published: AGGREGATES ONLY (counts, shares, salary percentiles, employer names with
counts). No advert text, no job URLs, no individual postings. Reed's developer pages state
no display terms (checked 21 Oct 2026), so we publish summaries, not their listings.

Each file is an explicit column list (never select *): a new column in a mart is not
published until someone adds it here on purpose.

Run:  python scripts/export_dashboard_data.py      (also run by scripts/run_daily.sh)
"""

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ingestion.db import connect  # noqa: E402
from ingestion.settings import get_settings  # noqa: E402

OUT = ROOT / "dashboard" / "data"

EXPORTS = {
    "summary.csv": """
        select first_collection_date, data_as_of, days_collected, company_boards_tracked,
               postings_tracked_total, postings_in_scope, live_in_scope_latest_day,
               excluded_training_courses, excluded_not_uk, excluded_not_tracked_role,
               employers_hiring, pct_in_scope_with_salary
        from marts.mart_pipeline_summary""",
    "role_demand_daily.csv": """
        select date_day, role_family, live_postings, new_postings,
               live_on_reed, live_on_company_boards, is_source_complete
        from marts.mart_role_demand_daily order by date_day, role_family""",
    "skill_demand.csv": """
        select role_family, skill, category, postings_mentioning, postings_with_description,
               pct_of_postings, is_low_sample
        from marts.mart_skill_demand order by role_family, postings_mentioning desc, skill""",
    "salary_by_role.csv": """
        select role_family, seniority, postings_with_salary, postings_in_scope, pct_with_salary,
               p25_gbp, median_gbp, p75_gbp, is_low_sample
        from marts.mart_salary_by_role order by role_family, seniority""",
    "work_type.csv": """
        select role_family, work_type, postings, pct_of_role, from_source_field
        from marts.mart_work_type order by role_family, work_type""",
    "company_hiring.csv": """
        select company_name, postings_in_scope, open_postings, role_families, role_family_list,
               senior_postings, tracked_via_company_board
        from marts.mart_company_hiring order by postings_in_scope desc, company_name""",
}


def export(conn, out_dir: Path = OUT) -> dict[str, int]:
    """Write every export; return rows written per file. Writes to .tmp then renames,
    so a failure half-way never leaves a half-written file for the dashboard to read."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows_written = {}
    for name, sql in EXPORTS.items():
        cur = conn.execute(sql)
        cols = [d.name for d in cur.description]
        rows = cur.fetchall()
        tmp = out_dir / (name + ".tmp")
        with tmp.open("w", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(cols)
            w.writerows(rows)
        tmp.replace(out_dir / name)
        rows_written[name] = len(rows)
    return rows_written


def main() -> int:
    with connect(get_settings()) as conn:
        written = export(conn)
    for name, n in written.items():
        print(f"wrote dashboard/data/{name}: {n} rows")
    if written["summary.csv"] != 1:
        print("ERROR: summary should have exactly 1 row", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
