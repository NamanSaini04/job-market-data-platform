# Job Market Data Engineering Platform

A daily, automated pipeline that collects UK data and AI job postings from approved APIs,
keeps every raw observation, tracks each posting over time, and models the data with dbt
on PostgreSQL for analysis of skills, salaries, locations and hiring trends.

> **Status: Phase 2.** Daily collection from 19 company job boards into a PostgreSQL raw layer.
> Reed (UK job board) is next. dbt models and dashboard follow.

## Architecture (so far)

```
Company job boards (Greenhouse, Lever, Ashby)
        |  daily, launchd
        v
Python collector ──> raw JSON files (gzip, immutable)
        |
        v
PostgreSQL (Docker)
  raw.job_observation   one row per job per day   -> history: first/last seen
  raw.job_payload       full JSON per content version -> change detection
  raw.rejected_record   failed validation, kept for inspection
  ops.pipeline_run      run log: status, counts, timing
  ops.source_run        per-board result, so a failed fetch is never read as "jobs closed"
```

## Key design decisions
- **Profile sources before designing.** 73% of Reed postings come from recruitment agencies, and keyword search returns unrelated titles more than half the time. Both shaped the design ([findings](docs/01_requirements_and_architecture.md)).
- **Raw layer is append-only JSONB.** Interpretation happens later in dbt, so cleaning rules can change without losing data.
- **Idempotent loads.** Re-running a day adds nothing.
- **Files first, database second.** A database outage loses no data; the raw layer can be rebuilt from files (tested).
- **Failure isolation.** One failing board or bad record never stops the run.

## Sources
| Source | Access | Status |
|---|---|---|
| Greenhouse / Lever / Ashby public job boards (19 companies) | No key | Collected daily |
| Reed Jobseeker API | Free key | Profiled, collector next |
| Adzuna API | Free key | Deferred |

## Run it
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # add Reed key and a Postgres password
docker compose up -d              # Postgres on localhost:5433
python -m ingestion.db migrate
pytest -q                         # 31 tests, incl. integration tests on real Postgres
python -m ingestion.run_ats       # collect all boards
```
Full setup, verification queries and scheduling: [docs/02_ingestion.md](docs/02_ingestion.md).

## Project layout
```
config/            boards to collect, role families
database/migrations/  numbered SQL schema changes
docs/              requirements, findings, phase guides
ingestion/         collector: extractors, raw files, loader, run log
scripts/           source profiling, daily runner, launchd schedule
tests/             unit + integration tests, real API sample responses
```
