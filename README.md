# Job Market Data Engineering Platform

A daily, automated pipeline that collects UK data and AI job postings from approved APIs,
keeps every raw observation, tracks each posting over time, and models the data with dbt
on PostgreSQL for analysis of skills, salaries, locations and hiring trends.

> **Status: Phase 6.** Daily collection from 19 company job boards and Reed (UK), orchestrated by
> Airflow, modelled with dbt into a star schema and analytics marts, published as a public dashboard.
>
> **Live dashboard:** https://job-market-data-platform-jqpk7xwwv9dtgkrmfzxkn7.streamlit.app/

## Architecture (so far)

```
Company job boards (Greenhouse, Lever, Ashby)   Reed API (14 searches, all pages)
        |  daily, Airflow DAG (Docker)            |  + detail calls, capped, queue from DB
        v                                         v
Python collectors ──> raw JSON files (gzip, immutable)
        |
        v
PostgreSQL (Docker)
  raw.job_observation   one row per job per day   -> history: first/last seen
  raw.job_payload       full JSON per content version -> change detection
  raw.reed_search_hit   which Reed search found each job, direct or agency
  raw.rejected_record   failed validation, kept for inspection
  ops.pipeline_run      run log: status, counts, timing
  ops.source_run        per-board result, so a failed fetch is never read as "jobs closed"
        |
        v  dbt (daily, after collection)
  staging       views: rename, cast, parse JSON, HTML -> text
  intermediate  rules: closure, content edits, role, seniority, salary, training-course flag
  marts         dim_date, dim_role, dim_skill, dim_company, fact_job_postings,
                fact_posting_daily, bridge_job_skills + 6 analytics marts
        |
        v  export (only after a fully passing dbt build): aggregates only, ~40 KB of CSV
  dashboard/data/  ->  GitHub  ->  Streamlit Community Cloud (public link)
```

## Key design decisions
- **Profile sources before designing.** 73% of Reed postings come from recruitment agencies, and keyword search returns unrelated titles more than half the time. Both shaped the design ([findings](docs/01_requirements_and_architecture.md)).
- **Raw layer is append-only JSONB.** Interpretation happens later in dbt, so cleaning rules can change without losing data.
- **Idempotent loads.** Re-running a day adds nothing.
- **Files first, database second.** A database outage loses no data; the raw layer can be rebuilt from files (tested).
- **Failure isolation.** One failing board or bad record never stops the run.
- **Cheap call to discover, expensive call only when it matters.** Reed detail calls are title-filtered, capped daily, and the queue is derived from database state.
- **Rate limits: stop, don't push.** A 429 after retries ends Reed calls for the day.
- **Orchestration that stays honest.** Airflow keeps transforming when a collector fails, but the run still ends red; retries differ per step (company boards yes, rate-limited Reed no) ([details](docs/06_airflow.md)).
- **A missing job is not a closed job.** Closed = missing on 2+ days its board was fully collected.
- **Flag, don't delete.** 606 paid training-course adverts posing as junior jobs are flagged and kept out of every mart ([details](docs/04_dbt.md)).
- **Only stated salaries.** No estimates; every salary figure carries its sample size.
- **Middlemen are not employers.** Recruitment agencies, job boards and training providers are kept out of employer rankings, each with recorded evidence.
- **Publish aggregates, not listings.** The public page shows counts and percentiles only; a test blocks advert text or links from being exported ([details](docs/05_dashboard.md)).

## Sources
| Source | Access | Status |
|---|---|---|
| Greenhouse / Lever / Ashby public job boards (19 companies) | No key | Collected daily |
| Reed Jobseeker API | Free key | Collected daily (search + capped detail) |
| Adzuna API | Free key | Deferred |

## Run it
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # add Reed key and a Postgres password
docker compose up -d              # Postgres on localhost:5433
python -m ingestion.db migrate
pytest -q                         # 97 tests, incl. integration tests on real Postgres
python -m ingestion.run_ats       # collect all boards
python -m ingestion.run_reed      # collect Reed
bash scripts/dbt.sh seed && bash scripts/dbt.sh build   # transform + 53 data tests
python scripts/export_dashboard_data.py && streamlit run dashboard/app.py

# or let Airflow run all of it daily (UI on http://localhost:8080):
docker compose --profile airflow up -d --build
```
Full setup, verification queries and scheduling: [docs/02_ingestion.md](docs/02_ingestion.md), [docs/03_reed.md](docs/03_reed.md), [docs/04_dbt.md](docs/04_dbt.md), [docs/05_dashboard.md](docs/05_dashboard.md), [docs/06_airflow.md](docs/06_airflow.md).

## Project layout
```
airflow/           DAG, image, DAG tests
config/            boards to collect, role families
dashboard/         public Streamlit page and the CSVs it reads
database/migrations/  numbered SQL schema changes
dbt/               staging, intermediate, marts models; seeds; data tests
docs/              requirements, findings, phase guides
ingestion/         collector: extractors, raw files, loader, run log
scripts/           source profiling, daily runner, launchd schedule
tests/             unit + integration tests, real API sample responses
```
