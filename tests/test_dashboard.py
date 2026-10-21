"""Phase 5: the public dashboard and the files it reads.

What can go wrong once the page is public, and the test that catches it:
  * a mart column is renamed -> page crashes            -> contract tests
  * a private/raw column gets exported by accident      -> privacy test
  * the page breaks on a tab, or on unusual data        -> AppTest renders every tab
"""

import csv
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "dashboard" / "data"
sys.path.insert(0, str(ROOT / "dashboard"))
from contract import REQUIRED_COLUMNS  # noqa: E402

spec = importlib.util.spec_from_file_location("export_dashboard_data",
                                              ROOT / "scripts/export_dashboard_data.py")
export_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export_mod)

# Never publish these: advert text, links to individual adverts, source ids, internal keys.
# (Counts ABOUT them, like postings_with_description, are fine.)
FORBIDDEN = {"description", "description_text", "version_description", "job_url", "url",
             "source_job_id", "posting_key", "company_key", "payload", "email", "job_title"}


def header(path: Path) -> list[str]:
    with path.open() as f:
        return next(csv.reader(f))


def test_export_script_writes_exactly_the_files_the_page_reads():
    assert set(export_mod.EXPORTS) == set(REQUIRED_COLUMNS)


@pytest.mark.parametrize("name", sorted(REQUIRED_COLUMNS))
def test_export_sql_selects_every_column_the_page_needs(name):
    sql = export_mod.EXPORTS[name].lower()
    for col in REQUIRED_COLUMNS[name]:
        assert col in sql, f"{name}: page needs '{col}' but the export does not select it"


@pytest.mark.parametrize("name", sorted(REQUIRED_COLUMNS))
def test_published_files_have_the_contract_columns(name):
    cols = header(DATA / name)
    missing = set(REQUIRED_COLUMNS[name]) - set(cols)
    assert not missing, f"{name} is missing {missing}: re-run scripts/export_dashboard_data.py"


@pytest.mark.parametrize("name", sorted(REQUIRED_COLUMNS))
def test_nothing_private_is_published(name):
    for col in header(DATA / name):
        c = col.lower()
        assert c not in FORBIDDEN and not c.endswith("_url"), f"{name}: '{col}' must not be public"
    assert "select *" not in export_mod.EXPORTS[name].lower(), "explicit column lists only"


def test_summary_is_one_row_and_exclusions_add_up():
    rows = list(csv.DictReader((DATA / "summary.csv").open()))
    assert len(rows) == 1
    r = {k: v for k, v in rows[0].items()}
    parts = sum(int(r[k]) for k in ("postings_in_scope", "excluded_not_tracked_role",
                                    "excluded_not_uk", "excluded_training_courses"))
    assert parts == int(r["postings_tracked_total"])


# ---------- the page itself ----------

TABS = ["Demand", "Skills", "Salaries", "Work arrangement", "Employers", "Method"]


def run_app(data_dir: Path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("DASHBOARD_DATA_DIR", str(data_dir))
    at = AppTest.from_file(str(ROOT / "dashboard/app.py"), default_timeout=30)
    at.run()
    return at


def test_page_renders_on_published_data(monkeypatch):
    at = run_app(DATA, monkeypatch)
    assert not at.exception, at.exception
    assert [t.label for t in at.tabs] == TABS
    assert at.title[0].value == "UK Data & AI Job Market"


def test_every_skills_role_renders(monkeypatch):
    at = run_app(DATA, monkeypatch)
    box = at.selectbox[0]
    for role in box.options:
        box.select(role).run()
        assert not at.exception, f"{role}: {at.exception}"


def write(dir_: Path, name: str, rows: list[dict]):
    with (dir_ / name).open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else REQUIRED_COLUMNS[name])
        w.writeheader()
        w.writerows(rows)


def test_page_survives_thin_data(tmp_path, monkeypatch):
    """One role, one day, no skills, no employers, every salary low-sample, 40 days of
    history (so no 'short history' banner). Thin data is what a new deployment looks like."""
    write(tmp_path, "summary.csv", [dict(
        first_collection_date="2026-09-01", data_as_of="2026-10-10", days_collected=40,
        company_boards_tracked=19, postings_tracked_total=10, postings_in_scope=4,
        live_in_scope_latest_day=3, excluded_training_courses=1, excluded_not_uk=2,
        excluded_not_tracked_role=3, employers_hiring=0, pct_in_scope_with_salary=25.0)])
    write(tmp_path, "role_demand_daily.csv", [dict(
        date_day="2026-10-10", role_family="Data Engineer", live_postings=3, new_postings=0,
        live_on_reed=3, live_on_company_boards=0, is_source_complete=False)])
    write(tmp_path, "skill_demand.csv", [])
    write(tmp_path, "salary_by_role.csv", [dict(
        role_family="Data Engineer", seniority="all", postings_with_salary=1, postings_in_scope=4,
        pct_with_salary=25.0, p25_gbp=60000, median_gbp=60000, p75_gbp=60000, is_low_sample=True)])
    write(tmp_path, "work_type.csv", [dict(
        role_family="Data Engineer", work_type="unknown", postings=4, pct_of_role=100.0,
        from_source_field=0)])
    write(tmp_path, "company_hiring.csv", [])
    at = run_app(tmp_path, monkeypatch)
    assert not at.exception, at.exception
    infos = [i.value for i in at.info]
    assert not any("days of history" in v for v in infos), "no short-history banner after 28+ days"
    assert any("No adverts with a full description" in v for v in infos), "empty skills explained"
    assert any("No direct employers" in v for v in infos), "empty employers explained"


def test_gitignore_does_not_hide_dashboard_data():
    # "data/" (no leading slash) ignores EVERY folder named data, including dashboard/data:
    # the files would never reach GitHub and the public page would crash.
    lines = [l.strip() for l in (ROOT / ".gitignore").read_text().splitlines()]
    assert "data/" not in lines and "data" not in lines
    assert "/data/" in lines, "raw pipeline data must still be ignored"
