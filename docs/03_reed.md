# Phase 3: Reed collector

Status: built 15 Oct 2026. Runs daily after the company boards, from the same script.

---

## 1. What this phase builds

```
config/roles.yaml (7 search terms)
   x {direct employer, recruitment agency}  = 14 searches
        |
        v  STEP 1: SEARCH (cheap, ~130 calls/day)
   page through ALL results of every search (100 per page)
   save each page raw -> parse -> load
        |
        |   raw.job_observation   1 row per job per day      (board = 'search')
        |   raw.reed_search_hit   which search found it, direct or agency
        |
        v  STEP 2: DETAIL (expensive, 1 call per job)
   queue  = seen today + data-role title + no detail stored yet, newest first
   fetch up to REED_MAX_DETAILS (default 800) per day
        |
            raw.job_payload (board = 'detail')   full description, salary type, contract
```

## 2. Why it is built this way

### Every search, every page, every day
Reed has **no date filter and no sort** (Phase 1, Finding 3). There is no way to ask
"what's new since yesterday". So the collector reads every page and compares job IDs
with what is already stored. ~12,000 results (with overlap) = ~130 calls. Affordable.

### Agency tag for free
`postedByDirectEmployer` and `postedByRecruitmentAgency` split results exactly
(8,858 + 3,192 = 12,050 in Phase 1). Running each search twice costs no extra calls
(same total pages) and tags every job. Phase 4 ranks companies on direct jobs only,
because 73% of Reed jobs name an agency, not the employer.

### Lineage: `raw.reed_search_hit`
A job can come back from several searches ("data engineer" and "data analyst").
It gets **one** observation per day but one hit row **per search**. So you can always
answer "why is this job in the dataset?".

### Title filter before the expensive call
Search is loose: only 24-41% of results for some terms have a matching title.
Details are fetched only for titles matching `roles.yaml`. That skips well over half
of the detail calls. Non-matching jobs are still stored from search (cheap), so
nothing is lost if the role list changes later.

### The queue is a query, not a list
"Needs detail" = seen today, title matches, no detail row yet. Computed from the
database every run. If today's cap stops at 800, tomorrow's run picks up the rest
automatically. Nothing to keep in sync, nothing to corrupt.

### Volatile fields
Reed's `applications` count changes constantly. If it counted as a content change,
every Reed job would look "edited" every day. It is still stored, but ignored when
hashing for change detection. (Test: application counts move, `new_or_changed = 0`.)

### Rate limits: stop, don't push
Reed publishes no limit. The HTTP client retries a 429 three times with growing waits
(and honours `Retry-After`). If it still gets 429, **the run stops calling Reed for the
day**. Pushing harder is how API keys get revoked.

### Closure safety, again
`ops.source_run` gets one row per search plus an overall `search` row, which is
`success` **only if all 14 searches finished**. Phase 4 will only treat a missing job as
closed on days when that row is `success`.

---

## 3. Run it (on your Mac, `.venv` active, Docker running)

**1. Apply the new migration**
```bash
python -m ingestion.db migrate
```
Expect `applied: 002_reed_search_hit.sql`.

**2. Tests**
```bash
pytest -q
```
Expect `47 passed`, **0 skipped**.

**3. Small real run first** (all searches, only 20 detail calls)
```bash
python -m ingestion.run_reed --max-details 20
```
Takes ~3 minutes (searches are 1 second apart).

**4. Look at it**
```bash
docker compose exec db psql -U jobs -d jobmarket
```
```sql
-- How many Reed jobs today, direct vs agency?
SELECT posted_by, count(DISTINCT source_job_id)
FROM raw.reed_search_hit WHERE observed_date = current_date GROUP BY posted_by;

-- Did every search finish? Any errors?
SELECT board, status, records_fetched, error_message
FROM ops.source_run
WHERE run_id = (SELECT run_id FROM ops.pipeline_run WHERE pipeline='reed_daily'
                ORDER BY started_at DESC LIMIT 1)
ORDER BY board;

-- Sample of details fetched
SELECT source_job_id, payload->>'jobTitle', payload->>'employerName',
       payload->>'yearlyMinimumSalary', payload->>'contractType'
FROM raw.job_payload WHERE source='reed' AND board='detail' LIMIT 10;
```

**5. From tomorrow it runs automatically** with the boards (`scripts/run_daily.sh`).
No new schedule to install.

---

## 4. What to expect in the first week

- **Backlog.** Day 1 the detail queue will be a few thousand jobs. At 800/day it clears
  in roughly a week, then drops to new jobs only (likely a few hundred a day).
  Watch `backlog_remaining` shrink in the run output.
- **Run length.** ~3 minutes of search + up to ~15 minutes of detail calls while the
  backlog lasts.
- **Page drift.** Reed results can shift while we page (a new job pushes others down a
  page), so a job can occasionally be missed or seen twice in one run. Duplicates are
  harmless (idempotent). A miss on one day is why closure logic needs more than one
  missing day before calling a job closed (Phase 4).

## 5. Completion criteria

- [ ] Migration 002 applied; `pytest -q` = 47 passed, 0 skipped
- [ ] Small run: `status=success`, all 14 searches `success`, `search` row `success`
- [ ] Direct/agency split close to Phase 1 (~26% / ~74%)
- [ ] 3 consecutive automatic daily runs with Reed included
- [ ] `backlog_remaining` going down day by day

## 6. Known limitations

- Detail is fetched once per job. If an employer edits the full description later,
  we see the change only if it also shows in the search record.
- Jobs with non-matching titles never get a full description. If `roles.yaml` gains
  a role later, its older jobs are only backfilled while they are still live.
- Reed is a single source of volume. If the key is revoked, volume collapses.

## 7. Interview questions this phase prepares you for

- *"How do you do incremental loads when the API has no 'changed since' filter?"*
  Full enumeration of a bounded set (~130 calls), diff by natural key.
- *"How do you keep API costs down?"* Cheap call to discover, filter, expensive call only
  for what matters; daily cap; queue derived from state.
- *"How do you handle rate limits?"* Bounded retries with backoff, honour `Retry-After`,
  then stop for the day and record it. Never hammer.
- *"What's a volatile field and why does it matter?"* A field that changes without the
  entity changing. Hashing it creates fake change history.
