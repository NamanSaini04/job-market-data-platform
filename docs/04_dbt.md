# Phase 4: dbt transformations

Status: built 18 Oct 2026 on 5 days of real data (14-18 Oct). 22 models, 47 tests, full build ~12s.

---

## 1. What this phase builds

```
raw.* / ops.*  (Phase 2-3, append-only)
   |
   v  STAGING (views)        rename, cast, parse JSON, HTML -> text. No business rules.
   |   stg_observations, stg_ats_versions, stg_reed_search_versions, stg_reed_details,
   |   stg_reed_search_hits, stg_board_completeness
   |
   v  INTERMEDIATE (tables)  business rules live here, in one place each
   |   int_job_versions            every content version, all sources, one shape
   |   int_posting_history         first/last seen, closure inputs, real content edits
   |   int_postings                one row per posting: latest content, salary, work type
   |   int_posting_classification  role family, seniority, UK, city, training-course flag
   |
   v  MARTS (tables)
       core       dim_date, dim_role, dim_skill, dim_company,
                  fact_job_postings (1 row/posting), fact_posting_daily (1 row/posting/day),
                  bridge_job_skills (posting x skill)
       analytics  mart_role_demand_daily, mart_skill_demand, mart_company_hiring,
                  mart_salary_by_role, mart_work_type
```

Seeds (small reference tables, in git):
- `role_patterns.csv`, `ats_companies.csv`: **generated** from `config/*.yaml` by
  `scripts/export_seeds.py`. Never edit them by hand; `tests/test_dbt_seeds.py` fails if stale.
- `skills.csv`: 58 skills, each a regex.
- `training_advertisers.csv`: advertisers proven to post paid courses, with the evidence.

## 2. Why it is built this way

### Staging = views, intermediate and marts = tables
Staging is a thin rename layer: a view costs nothing to store and is always current.
Intermediate and marts do joins and regex work, so they are stored once per day.

### One place per rule
| Rule | Lives in |
|---|---|
| Is this posting still open? | `int_posting_history` |
| Which role family? | `int_posting_classification` + `config/roles.yaml` |
| Which salaries count? | `int_postings` |
| What is "in market scope"? | macro `in_market_scope()` |

If a rule is copied into two models, the two copies drift. A dashboard number that
disagrees with another dashboard number is the fastest way to lose trust.

### Keep everything, flag instead of delete
`fact_job_postings` keeps all 11,070 postings, with `is_in_market_scope` (UK, tracked
role, not a training course). Every analytics mart filters on that flag. So an exclusion
is a visible column you can audit, not a row that silently vanished.

## 3. Rules and findings (with evidence)

### 3.1 Closure rule: only trust complete days
```
open     seen on or after the latest day its board was FULLY collected
closed   missing on 2+ fully collected days
unknown  anything else
```
- A board that failed (API down, schema changed) is not "all jobs closed". `ops.source_run`
  says which boards succeeded each day; `stg_board_completeness` turns that into complete days.
- 2 days, not 1: Reed results shift while paging, so a live job can be missed once (docs/03).
- Result on 5 days: 10,073 open, 774 closed, 223 unknown.
- Tested: `assert_closed_needs_two_complete_missed_days`. Changing the rule to 1 day makes
  this test fail with 223 rows (checked on purpose).

### 3.2 Content edits vs label changes
On 17 Oct Faculty moved 62 jobs to new teams. The raw layer correctly stored 62 new
versions (the JSON changed). But a job seeker sees no change. So `content_signature`
hashes only **title, description, salary**. Faculty: 62 new versions -> 3 real edits.

### 3.3 Training-course adverts (the biggest data-quality finding)
343 of 363 "junior" data postings were **paid training courses**, not jobs. ITOL Recruit:
"Please note this is a training course and fees apply". IT Career Switch: "guaranteed job
offer" after a self-paced programme.
- Two detectors: advertiser list (seed, with evidence) and a description rule that needs a
  course signal **and** a payment/guarantee signal. Needing both keeps genuine employer
  traineeships out of the net.
- Result: 606 adverts flagged, all via the advertiser list. The rule flagged 0 genuine employers.
- Without this, "junior data jobs" would be about 30x overstated.
- Open question: Newto Training adverts have no full description, so neither detector
  fires. Titles include "Junior AI Developer". Not added to the list without evidence.

### 3.4 Salaries: only what the advert states
- Used: Reed detail salaries marked "per annum" in GBP; Ashby GBP "1 YEAR" components.
- Kept separate: day rates (contracts).
- Not used: Reed search salaries (no unit), anything outside 10k-500k.
- Each mart row shows `pct_with_salary` and `is_low_sample` (fewer than 20 salaries).
  A median from 8 salaries is shown, but flagged.

### 3.5 New postings: no day-one spike
On a source's first collection day every live job looks "new". `new_postings` excludes
postings that were already live when collection started, and `is_source_complete` is false
on days when any board failed **or had not started yet** (14 Oct: Reed not running yet).

### 3.6 Skill matching
- Regex per skill, wrapped in non-alphanumeric boundaries so "R" never matches inside "Spark".
- Denominator = postings **with a full description**, never all postings. A missing
  description is not evidence that a skill is not required.
- Bug found by a seed test: the RAG pattern only matched "retrieval augmented generation",
  so the acronym was invisible. Fixed; RAG now appears in 51% of AI Engineer adverts.
  "RAG status" (red/amber/green reporting) is excluded on purpose.

### 3.7 Role patterns are regex (changed 21 Oct 2026)
Review of the first BI Developer results (47 postings): about 6 were not BI jobs. The
plain phrase `power bi` matched any title that **mentions the tool**: "Finance Business
Partner (Power BI dashboards)", "Systems Accountant (Power BI)", "Business Analyst | Power BI".
Finance partners and accountants are paid on a different scale, so this would also have
bent the BI salary figures.
- Fix: patterns in `config/roles.yaml` are now regular expressions. Plain phrases still work
  as "contains". BI now needs `power bi` **plus** developer / engineer / modeller.
- One rule, two engines: Python (`ingestion/roles.py`, decides which Reed jobs get a detail
  call) and dbt (Postgres `~`). Checked: both give the same family for all 11,070 titles.
- Patterns are limited to syntax both engines read the same way; a test rejects `\b`, `\d`
  and lookarounds.
- `tests/test_phase1.py` holds 17 real titles with the family each must get. Putting the
  bare `power bi` back makes 4 of them fail (checked).
- Data Leadership (3 -> 76 after "head of data" became a search term): reviewed, nearly
  all genuine (Chief Data Officer, Head of Data, Director of Data). It also includes
  analytics managers, so read it as "leads a data or analytics team", not only executives.

## 4. Performance: a planner misestimate (56s -> 0.8s)
`int_postings` took 56-73s on 5 days of data and would grow daily. `EXPLAIN ANALYZE`:
```
Nested Loop  (rows=40 estimated)  (actual rows=11070)  ~72s
```
- Cause: joins on **4 text columns** (source, board, job id, payload hash). Postgres
  assumes the columns are independent, multiplies their selectivities, and estimates 1-40
  rows. It then chose a nested loop that re-ran the Reed details view (with HTML cleaning)
  11,070 times. `ANALYZE` alone did not fix it.
- Fix: one surrogate key per grain (`version_key`, `posting_key` = md5 of the natural key),
  lifecycle moved to its own table, `ANALYZE` in a post-hook so stats are fresh.
- Result: whole intermediate layer ~6s, full `dbt build` ~12s.

## 5. Tests
| Kind | Examples |
|---|---|
| Schema (YAML) | unique + not_null on every key; accepted_values on status, work_type, seniority; relationships fact -> dims |
| Singular (`dbt/tests/`) | salary min <= max and 10k-500k; closed needs 2 complete missed days; open = seen on latest complete day; one daily row per posting-day; **every observed posting reaches the fact table**; no training course in scope; skill share <= 100% |
| Python (`tests/test_dbt_seeds.py`, `tests/test_phase1.py`) | seeds match config; role priority order; skill regex valid, never matches an empty string, matches its own name; 17 real titles get the right role family |
| Freshness | `raw.job_observation.ingested_at`: warn after 30h, error after 54h |

Mutation check done on 18 Oct: closure rule changed to 1 day -> test fails (223 rows);
training filter removed from scope -> test fails (344 rows). Both restored.

## 6. Run and verify
```bash
pip install -r requirements.txt
bash scripts/dbt.sh debug              # connection OK?
python scripts/export_seeds.py
bash scripts/dbt.sh seed
bash scripts/dbt.sh build              # models + tests; expect PASS, 0 ERROR
bash scripts/dbt.sh source freshness
```
Spot checks (`docker exec -it jobmarket-db psql -U jobs -d jobmarket`):
```sql
select status, count(*) from marts.fact_job_postings group by 1;
select * from marts.mart_salary_by_role where seniority = 'all' order by postings_with_salary desc;
select * from marts.mart_skill_demand where role_family = 'All data roles' order by postings_mentioning desc limit 15;
```
`scripts/run_daily.sh` now runs export_seeds -> seed -> build after collection.

## 7. Known limitations
- **Job boards counted as employers.** eFinancialCareers (a job board) is tagged "direct"
  by Reed on all 487 of its adverts, so it ranks #1 in `mart_company_hiring`. Needs an
  intermediary list, same evidence-first approach as training advertisers.
- **Agency adverts hide the employer**, so they are excluded from company rankings, not
  re-attributed.
- **Seniority from titles only**: 410 of 666 in-scope postings say no level.
- **Work type** is unknown for about half of postings: most adverts never state it in a field.
- 5 days of data: trends are not meaningful yet. The marts are built for when they are.

## 8. Completion criteria
- [x] `dbt build` passes on real data with 0 errors
- [x] Every observed posting reaches `fact_job_postings` (tested)
- [x] Closure, scope and salary rules each in one place, each tested
- [x] Tests proven to fail when the rule is broken (mutation check)
- [x] Daily run includes dbt
- [ ] User: build + test pass on the Mac, then commit and push
