# Job Market Data Engineering Platform — Phase 1: Requirements & Architecture

Status: draft v1 (13 Oct 2026). Commit this to `docs/` in the repo. Every later phase refers back to it.

---

## 1. Scope (what v1 IS and IS NOT)

**v1 is:** a daily, automated pipeline that pulls UK data/AI job postings from 2 approved sources (Reed + company job boards), keeps every raw observation, builds dbt models on PostgreSQL, tracks each posting's life (first seen → last seen → closed), and shows results on a deployed dashboard with a public link.

**v1 is NOT:** cloud infra, Kafka, Spark, LLM extraction, NLP models, Great Expectations, a 15-page PDF.

**Timebox:** MVP live (public link + README) within 4 weeks of starting Phase 2. If it slips past 6 weeks, cut scope — do not extend.

---

## 2. Data sources (decided)

| # | Source | Access | What it is good for | Known weaknesses |
|---|--------|--------|---------------------|------------------|
| 1 | Greenhouse / Lever / Ashby public job-board APIs | No key, public GET | Full job descriptions; **reliable "closed" detection** (we fetch a company's whole board, so a missing job really is gone) | Only covers companies we pick; Greenhouse gives no salary or employment type |
| 2 | Reed Jobseeker API | Free key, HTTP Basic auth | UK-wide volume; detail endpoint gives full description, salary type, yearly salary, expiry date, contract type | Search + detail = 2 calls per job; rate limit not documented; hidden salaries come back as missing |
| 3 | Adzuna API (**deferred to v2**) | Free app_id + app_key | UK volume; salary histograms and top-company endpoints | ~1,000 calls/month on free tier; description is truncated; salary may be **predicted** (`salary_is_predicted = 1`); Adzuna aggregates other boards, so heavy overlap with Reed |
| 4 | Synthetic data | Generated | Unit/integration tests only | Never mixed into analytics tables |

Rules:
- Every row keeps `source`, `source_job_id`, `source_url`, `ingested_at`, `run_id`.
- Predicted salaries are stored but **excluded** from salary marts.
- Adzuna attribution required in the dashboard ("Jobs by Adzuna").
- Read each provider's terms before Phase 3 and note the date you read them here.

### Decision (14 Oct 2026): Reed only for v1, Adzuna deferred

Reasons: Adzuna descriptions are truncated (bad for skill extraction), salaries are often predicted, it aggregates other boards (heavy overlap with Reed), and the free tier is too small to track closures. Each source has its own connector, so adding Adzuna later is one new file.

New risks accepted:
- **Single aggregator.** If the Reed key is revoked or throttled, most volume is lost. The collector must pace requests and retry with backoff.
- **Recruitment agencies.** Measured: 73% of Reed jobs are agency-posted (Finding 4). Every job is tagged `is_agency` at ingestion via Reed's own filters; company rankings use direct jobs only.
- **2 calls per job.** Fetch Reed detail only for jobs not seen before, never every job every day.

### Adzuna call budget (kept for v2 reference)
- 1,000 calls / month ≈ 33 calls / day.
- 6 role searches × 1 page of 50 newest results per day = 6 calls/day. Leaves headroom for retries and salary-history calls.
- Consequence: we only pull **new** Adzuna postings daily. We cannot see the full Adzuna inventory, so **Adzuna postings never get an "expired" status** — only "last seen".

---

## 3. Business questions — what v1 can honestly answer

| Question | v1? | Notes |
|---|---|---|
| Most requested skills | Yes | Needs dictionary skill extraction in MVP (moved forward from Phase 19) |
| Skills growing fastest | Partly | Needs ≥ 8–12 weeks of data. Dashboard must show "data since <date>" |
| Companies hiring most | Yes | |
| Locations with most demand | Yes | Needs a UK location mapping seed |
| Remote / hybrid / on-site change | Partly | Detected from text; label as estimate |
| Skills that appear together | Yes | From bridge table |
| Salary by role | Yes | Stated salaries only |
| Companies posting graduate roles | Yes | Title/description keywords; noisy |
| How long postings stay active | **ATS sources + Reed only** | Not Adzuna (see call budget) |
| Growing / declining categories | Partly | Needs weeks of data |
| Tech by role family | Yes | |
| Junior vs senior skills | Yes | Seniority parsed from title |

---

## 4. Architecture (v1)

```
Greenhouse/Lever/Ashby   Reed API   Adzuna API
          \                 |          /
           Python extractors (one per source, shared base class)
                            |
                 raw JSON files  data/raw/<source>/<date>/<run_id>.json   (bronze, immutable)
                            |
                 pydantic validation  → rejects logged to raw.rejected_records
                            |
                 PostgreSQL schema `raw`   (append-only, one row per observation)
                            |
                 dbt:  staging → intermediate → marts   (+ dbt tests)
                            |
                 marts exported to Parquet/DuckDB file
                            |
                 Streamlit dashboard (deployed, public link)

Orchestration: cron first (Phase 3), Airflow DAG later (Phase 6).
Every run writes one row to ops.pipeline_run.
```

### Key decisions (and why)

1. **Raw is append-only and stored twice (file + table).** If a parsing bug appears in week 3, we can rebuild everything from files. No destructive changes before dbt.
2. **History comes from observations, not from the source.** Each daily run records "I saw job X today". `first_seen` = min date, `last_seen` = max date. A posting is `closed` when an ATS board or Reed stops returning it for 2 consecutive runs (one missed run is not proof).
3. **Start collecting data in week 1.** History cannot be backfilled. Every day the collector isn't running is lost data.
4. **PostgreSQL is the warehouse. DuckDB only as the dashboard's read file.** One warehouse, not two.
5. **No Great Expectations in v1.** pydantic checks raw shape in Python; dbt tests check modelled data. Two tools cover it.
6. **Streamlit over Power BI for the public version** because recruiters can open a link without a licence. Optional: connect Power BI Desktop to the marts for screenshots of DAX measures.
7. **Airflow arrives after the pipeline works under cron.** Debugging Airflow and pipeline logic at the same time doubles the pain.

---

## 5. Data model v1 (star schema)

Grain of the main fact: **one row per canonical posting** (an "accumulating snapshot": the row is updated as the posting moves through its life).

- `fact_job_postings`: posting_key, company_key, role_key, first_seen_date_key, last_seen_date_key, status (open/closed/unknown), days_active, seniority, work_type, employment_type, salary_min_annual, salary_max_annual, salary_currency, salary_is_stated, source_count, primary_source
- `fact_posting_observations`: posting_key, observed_date_key, source, run_id — one row per posting per day seen (this is what powers history)
- `dim_company`: company_key, company_name_normalized, company_name_raw_examples, ats_slug (nullable)
  - Dropped `industry`, `company_size`: no source provides them. Don't design columns you can't fill.
- `dim_role`: role_key, role_family (Data Analyst, Data Engineer, Data Scientist, ML Engineer, AI Engineer, Analytics Engineer, Other)
  - Seniority moved to the fact: the same family appears at every level.
- `dim_location`: location_key, city, region (UK ITL1 region), country, is_remote_only
- `dim_skill`: skill_key, skill_name (canonical), skill_category
- `dim_date`: standard calendar
- `bridge_job_skills`: posting_key, skill_key
- `bridge_job_locations`: posting_key, location_key (one posting can be open in several locations; see Finding 2)

Slowly Changing Dimensions: not needed in v1. If a posting's salary or title changes, the observation table keeps every version; a dbt snapshot (SCD Type 2) on the staging table is a Phase 2 add-on.

---

## 6. Revised build order

| Milestone | Contents | Done when |
|---|---|---|
| M1 Phase 1 (now) | This doc, API keys, source check script | Script prints sample records from all 3 sources |
| M2 | Docker Compose (Postgres), Greenhouse extractor, raw files + raw table, `ops.pipeline_run`, cron daily, pytest for parser | Data collecting daily, unattended |
| M3 | Reed + Adzuna extractors, pydantic validation, rejected-records table | 3 sources landing daily |
| M4 | dbt staging/intermediate/marts, skill dictionary v1, dedup within source, dbt tests | `dbt build` passes; marts populated |
| M5 | Streamlit dashboard deployed with public link | Link works on a phone |
| M6 | Airflow DAG replaces cron | DAG runs 3 days in a row green |
| M7 | README, architecture diagram, 2-page write-up, resume bullets | Repo is recruiter-ready |
| Later | Cross-source dedup, SCD2 snapshots, salary normalization, NLP skills, cloud, AI analyst, full PDF report | — |

Tests and logging are written **inside** each milestone, not saved for the end.

---

## 7. Non-functional requirements

- Idempotent: running the same day twice must not create duplicate observations (unique key: source, source_job_id, observed_date).
- Re-runnable from raw files.
- Secrets in `.env`, never in Git. `.env.example` committed.
- One command to start locally: `docker compose up`.
- Machine needs ~4 GB free RAM once Airflow is added.

---

## 8. Phase 1 completion checklist

- [x] Repo structure created, this doc committed
- [x] Role families in `config/roles.yaml` (add roles there, no code change)
- [x] All candidate boards checked live on 13 Oct 2026 (65 slug attempts across 3 ATS types)
- [x] 19 boards kept by the rule ">= 1 UK data role" (6 Greenhouse, 10 Ashby, 3 Lever)
- [x] ATS field profile measured from real responses (table below)
- [x] `pytest -q` passes (12 tests, no network)
- [x] Reed API key obtained
- [x] Adzuna deferred to v2 (decision above)
- [x] `check_sources.py --save` ran on your Mac; 5 fixture files saved (greenhouse, ashby, lever, reed_search, reed_detail)
- [x] `profile_reed.py` run; results recorded in Finding 4
- [x] `git init` + first commit

**Phase 1 complete: 14 Oct 2026.**
- [x] Reed column below filled from real samples

### Finding 1: company boards alone are too small

On 13 Oct 2026 the 19 boards held about **103 UK data roles**. Faculty alone was ~28% of them.
- "Companies hiring the most" from boards alone would mostly measure which companies I chose to track.
- **Reed is required for volume, not optional.** Boards stay valuable for full descriptions and reliable "closed" detection.
- The dashboard must label board-based company rankings as "tracked companies only".

Many big UK employers (DeepMind, Skyscanner, Octopus, Checkout.com, Starling, Ocado) are not on Greenhouse, Ashby or Lever. They likely use Workday or similar. Out of scope for v1.

### Finding 2: one posting can have many locations

Real examples: `"Barcelona; Cardiff, London or Remote (UK); London"` (Greenhouse), `secondaryLocations` (Ashby), `categories.allLocations` (Lever).
- **Data model change:** replace the single `location_key` on the fact with `bridge_job_locations (posting_key, location_key)`, the same pattern as skills.
- Location counts then become "postings open to this location", and a posting can count in several places. The dashboard must say so.

### Finding 3: Reed, from real samples (14 Oct 2026)

- **Keyword search is loose.** A "data engineer" search returned "AI Engineer" and "AI Solutions Engineer". Every Reed result must be re-classified by title with `roles.yaml`; never trust the search term as the role.
- **No sort and no date filter.** Results are not newest-first. Daily loading must page through every result for each search term and compare job IDs with what we already have. `scripts/profile_reed.py` measures how many pages that is.
- **The two endpoints disagree.** Same job: search said `applications = 110`, detail said `applicationCount = 0`. Never assume two endpoints from one source agree. Pick one per field and document it.
- **Expiry is probably a fixed 42-day listing period, not a real deadline.** If `profile_reed.py` confirms it, `expirationDate` is not a closing date, and posting lifetime still has to come from first seen / last seen.
- **Agency vs direct employer:** Reed's search has `postedByRecruitmentAgency` and `postedByDirectEmployer` filters, plus `graduate`. Measure the shares with `profile_reed.py`. If needed, the collector can run each search twice to tag every job as agency or direct.

### Finding 4: Reed profile (scripts/profile_reed.py, 14 Oct 2026)

| term | total | agency | direct | graduate | title match* | salary shown* |
|---|---|---|---|---|---|---|
| analytics engineer | 907 | 744 | 163 | 80 | 24% | 70% |
| machine learning engineer | 461 | 334 | 127 | 30 | 64% | 89% |
| mlops engineer | 45 | 39 | 6 | 5 | 64% | 80% |
| ai engineer | 1,380 | 934 | 446 | 137 | 91% | 94% |
| data engineer | 2,875 | 2,195 | 680 | 216 | 35% | 53% |
| data scientist | 4,327 | 3,236 | 1,091 | 379 | 59% | 74% |
| data analyst | 2,055 | 1,376 | 679 | 199 | 41% | 59% |
| **all terms (overlapping)** | **12,050** | **8,858** | **3,192** | **1,046** | | |

*Measured on the first page of 100 results per term only.

What it means:
1. **Agency and direct flags split the data exactly.** 8,858 + 3,192 = 12,050 for every term combined. Each job is one or the other. So the collector pages through `postedByDirectEmployer=true` and `postedByRecruitmentAgency=true` separately: **same number of calls as one search, and every job gets an `is_agency` tag for free.**
2. **73% of Reed jobs are posted by agencies.** The `employerName` on those is the agency. **Company rankings use direct-employer jobs only** (plus the company boards). Skill, salary, location and role analysis can use all jobs.
3. **Raw totals overstate volume.** Only 24-41% of results for analytics/data engineer/data analyst have a matching title, even on the first page, which Reed ranks as most relevant. Later pages are likely worse. Real relevant volume is probably a few thousand unique jobs, not 12,050.
   - **Design rule: classify by title BEFORE the detail call.** Detail calls are the expensive part (1 per job). Skip detail for jobs whose title matches no role family.
4. **Expiry is a fixed listing period.** 493 of ~700 sampled jobs expired exactly 42 days after posting; other common gaps were 294, 24, 28 and 293 days. `expirationDate` is not a real closing date. Posting lifetime comes from first seen / last seen only.
5. **Salary is better than the 3-sample fixture suggested:** 53-94% on first pages. Still measure it on the full data, and only from stated values.
6. **Graduate flag:** 1,046 results (~9%). Enough to answer "which companies advertise graduate roles", using direct-employer jobs only.

Daily cost estimate: 12,050 results / 100 per page = ~121 search calls, plus 1 detail call per new title-matched job. The first run (backfill) could need a few thousand detail calls: spread it over several days, at 1 call per second, and stop on HTTP 429.

### Field availability

ATS columns: measured on 13 Oct 2026 across 643 Greenhouse jobs (5 boards), 462 Ashby jobs (5 boards), 84 Lever jobs (Spotify).
Reed and Adzuna columns: from published docs only. **Confirm with `check_sources.py` once keys are in `.env`.**

| Field | Greenhouse | Lever | Ashby | Reed (real, 14 Oct) | Adzuna (docs, v2) |
|---|---|---|---|---|---|
| job id | `id` (number) | `id` (uuid string) | `id` (uuid string) | `jobId` (number) | `id` |
| title | `title` | `text` | `title` | `jobTitle` | `title` |
| full description | `content`, only with `?content=true`; **HTML-escaped** (`&lt;p&gt;`), so unescape, then strip tags | `descriptionPlain` (80%); `description` HTML (100%) | `descriptionPlain` (100%) | search: short snippet, HTML-escaped. detail: full HTML (~5,800 chars in sample) | truncated |
| salary | no field. £ appears in text of ~11% of jobs | no `salaryRange` seen on Spotify (0/84) | `compensation` with tiers on 13%: `minValue`, `maxValue`, `currencyCode`, `interval` ("1 YEAR"); often USD | `minimumSalary/maximumSalary` = null when hidden (2 of 3 samples). Detail adds `yearlyMinimumSalary/Max`. `salaryType` says 'per annum' even when salary is null | `salary_min/max` + `salary_is_predicted` |
| employment type | no standard field (custom `metadata` differs per company) | `categories.commitment`: Permanent, Short Term, Full Time Contractor | `employmentType`: FullTime, PartTime, Contract, Temporary, Intern | detail only: `contractType` (Permanent...), `fullTime`, `partTime` booleans | `contract_time`, `contract_type` |
| work arrangement | text only (e.g. "Remote (UK)" inside location) | `workplaceType` 100%: hybrid, onsite, remote | `workplaceType` 79%: Hybrid, OnSite, Remote, null; plus `isRemote` | none | text only |
| posted date | `first_published`, ISO with local offset (e.g. `-04:00`) | `createdAt`, epoch milliseconds | `publishedAt`, ISO UTC | search `date`, detail `datePosted`, both dd/mm/yyyy | `created` |
| last changed | `updated_at` | none | none | none | none |
| expiry date | `application_deadline`: **0% filled** | none | none | `expirationDate` dd/mm/yyyy. In both dated samples it was exactly 42 days after posting | none |
| location | `location.name` free text, often several places in one string | `categories.location` + `allLocations` array + `country` code | `location` + `secondaryLocations` + `address` (66%) | `locationName`, one string (e.g. London) | `location.area` array |

What this changes:
- Greenhouse is always called with `?content=true`; Ashby with `?includeCompensation=true`.
- **Dates:** 4 formats (ISO with offset, ISO UTC, epoch ms, dd/mm/yyyy). One tested `parse_date()` that returns UTC. Never store local offsets.
- **Work arrangement:** same values, different spelling (`onsite` vs `OnSite`, `hybrid` vs `Hybrid`). One mapping table to `on_site / hybrid / remote / unknown`.
- **Salary:** structured salary exists only for a minority of ATS jobs, and Ashby salaries are often USD for US roles. The salary mart must come mainly from Reed (stated salaries), never from Adzuna predicted salaries.
- **Expiry:** no ATS gives a closing date. Lifetime comes only from first seen / last seen, which is why the daily collector must start early.
- **Employment type from Greenhouse is unknown.** Expect a large "unknown" bucket and show it on the dashboard rather than hiding it.
