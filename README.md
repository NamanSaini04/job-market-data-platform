# Job Market Data Engineering Platform

A daily, automated pipeline that collects UK data and AI job postings from approved APIs,
keeps every raw observation, tracks each posting over time, and models the data with dbt
on PostgreSQL for analysis of skills, salaries, locations and hiring trends.

> Status: **Phase 1, requirements and source profiling.** Pipeline not built yet.

## Sources
| Source | Access |
|---|---|
| Greenhouse / Lever / Ashby public job boards | No key |
| Reed Jobseeker API | Free key |
| Adzuna API | Deferred to v2 |

## Project layout (so far)
```
config/
  ats_companies.yaml   company career boards to collect
  roles.yaml           role families + title patterns (add roles here)
docs/
  01_requirements_and_architecture.md
scripts/
  check_sources.py     profiles each source, can save test fixtures
  verify_ats_slugs.py  checks every company board in the config
tests/
  test_phase1.py
```

## Run Phase 1
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then add your Reed key
pytest -q                   # config + helper tests, no network
python scripts/verify_ats_slugs.py
python scripts/check_sources.py --save
```

## Design
See [docs/01_requirements_and_architecture.md](docs/01_requirements_and_architecture.md).
