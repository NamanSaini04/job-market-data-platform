# Phase 2: Daily collector and raw layer

Status: built 14 Oct 2026. Covers the company job boards (Greenhouse, Lever, Ashby).
Reed is Phase 3 (paging, agency/direct split, detail calls).

---

## 1. What this phase builds

```
config/ats_companies.yaml (19 boards)
        |
        v
ingestion/run_ats.py  --- for each board ---------------------------------+
        |  1. fetch           (http_client.py: retries on 429/5xx)        |
        |  2. save raw file   (raw_files.py: data/raw/..., gzip, atomic)  |
        |  3. validate/parse  (extractors/ats.py)                         |
        |  4. load            (loader.py: one transaction per board)      |
        |  5. log             (ops.source_run + data/logs/pipeline.jsonl) |
        +-----------------------------------------------------------------+
        v
Postgres (Docker, port 5433)
  raw.job_payload       full job content, once per distinct version
  raw.job_observation   "job X was live on day Z" (powers history)
  raw.rejected_record   records that failed validation
  ops.pipeline_run      one row per run (status, counts, timing)
  ops.source_run        one row per board per run
  ops.schema_migration  which migrations have run
```

Scheduled daily by macOS launchd (`scripts/run_daily.sh`).

---

## 2. Why it is built this way (concepts to understand, not just copy)

### Raw / bronze layer: store what you received, interpret later
Raw tables hold the source's JSON as-is (JSONB). No renaming, no type casting, no cleaning.
- If a cleaning rule is wrong, fix it in dbt (Phase 4) and re-run. The raw data is untouched.
- If the source adds a field, it is already stored. Nothing breaks. This is called **schema-on-read**.

### Two copies: files and database
Every response is saved as a gzip file **before** parsing.
- Database down? Files are saved; run `python -m ingestion.reload_files` later.
- Parsing bug found in week 3? Rebuild the raw layer from files (a test proves this works).

### Observations vs payloads: store versions, not copies
Storing every job's full text every day is ~25 MB/day of mostly identical data.
Instead:
- `job_observation`: one small row per job per day (job id, date, content hash).
- `job_payload`: the full JSON, stored only when its **hash** changes.

The hash is SHA-256 of the JSON with keys sorted, so key order doesn't matter, only content.
Free side effect: **change detection**. Two different hashes for one job = the posting was edited.

### Idempotency: running twice must be harmless
Primary keys plus `ON CONFLICT DO NOTHING` mean a second run on the same day adds nothing.
Pipelines get re-run all the time (after a crash, or by mistake). This makes that safe.

### Failure isolation
- One board fails: the others still load. Run status = `partial`.
- One bad record: rejected and stored in `raw.rejected_record`; the rest of the board loads.
- Response shape changed (source changed its API): the whole board fails loudly with `SchemaChangedError`. Never load something we don't understand.
- Each board loads in **one transaction**: all of it or none of it. No half-loaded boards.

### Why `ops.source_run` matters for later
Phase 4 will mark a job "closed" when it disappears from its board. A board that **failed** to load
that day must count as *unknown*, not as "every job closed". `ops.source_run` records which boards
succeeded each day, so closure logic can check this. Without it, one network error would
"close" hundreds of jobs.

### Migrations
Schema changes are numbered SQL files in `database/migrations/`. Each runs once and is recorded.
Never edit an old migration; add a new one.

### Why launchd, not cron
If the Mac is asleep at 09:30, launchd runs the job on wake. cron skips it.
If the Mac is off, both miss it. That day becomes a gap, visible in `ops.pipeline_run`.
**A gap is not a closure**: Phase 4 must treat missing days as unknown.

---

## 3. Set it up (one time, ~15 minutes)

Run each command separately, from the project folder, with `.venv` active.

**1. Install the new dependency**
```bash
pip install -r requirements.txt
```

**2. Add database settings to `.env`** (generates a random password)
```bash
printf '\nPGHOST=localhost\nPGPORT=5433\nPGUSER=jobs\nPGPASSWORD=%s\nPGDATABASE=jobmarket\n' "$(python -c 'import secrets; print(secrets.token_urlsafe(24))')" >> .env
```
Check it worked without printing the password:
```bash
grep -c '^PG' .env
```
Expect `5`.

**3. Start Postgres**
```bash
docker compose up -d
```
```bash
docker compose ps
```
Expect `jobmarket-db` with status `healthy` (may take ~10 seconds).

**4. Create the tables**
```bash
python -m ingestion.db migrate
```
Expect `applied: 001_raw_and_ops.sql`. Run it again: expect `nothing (already up to date)`.

**5. Run all tests** (unit + integration against a separate `jobmarket_test` database)
```bash
pytest -q
```
Expect `31 passed`. If integration tests show as **skipped**, Postgres isn't reachable: check step 3.

**6. First real run on one board**
```bash
python -m ingestion.run_ats --only monzo
```

**7. Full run**
```bash
python -m ingestion.run_ats
```
Takes ~1 minute (19 boards, 1 second apart).

---

## 4. Verify it works (look at the data yourself)

Open a SQL prompt inside the database container:
```bash
docker compose exec db psql -U jobs -d jobmarket
```

Then run these one at a time (type `\q` to exit):

```sql
-- Did the run succeed?
SELECT status, boards_total, boards_failed, records_fetched, records_loaded,
       finished_at - started_at AS duration
FROM ops.pipeline_run ORDER BY started_at DESC LIMIT 3;

-- Which boards, how many jobs, any errors?
SELECT source, board, status, records_fetched, duration_ms, error_message
FROM ops.source_run
WHERE run_id = (SELECT run_id FROM ops.pipeline_run ORDER BY started_at DESC LIMIT 1)
ORDER BY records_fetched DESC;

-- What does a raw job look like? (title differs by source: 'title' or 'text')
SELECT source, board, source_job_id,
       coalesce(payload->>'title', payload->>'text') AS title
FROM raw.job_payload LIMIT 10;
```

**Idempotency check:** run `python -m ingestion.run_ats` a second time today. Expect
`new_observations=0` and `re-run_duplicates` equal to the first run's total.

---

## 5. Schedule it

```bash
bash scripts/run_daily.sh && tail -5 data/logs/daily_$(date +%F).log
```
Manual test first. Expect `exit=0` on the last line.
 
Install the schedule:
```bash
cp scripts/launchd/com.namansaini.jobmarket.daily.plist ~/Library/LaunchAgents/
```
```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.namansaini.jobmarket.daily.plist
```
Trigger it once now to prove launchd can run it:
```bash
launchctl kickstart -k gui/$(id -u)/com.namansaini.jobmarket.daily
```
Wait ~1 minute, then check the log has a new run:
```bash
tail -3 data/logs/daily_$(date +%F).log
```

Also set **Docker Desktop → Settings → General → Start Docker Desktop when you sign in**.

To remove the schedule later:
```bash
launchctl bootout gui/$(id -u)/com.namansaini.jobmarket.daily
```

---

## 6. Completion criteria

- [ ] `pytest -q` shows 31 passed, 0 skipped
- [ ] Full run finishes with `status=success` (or `partial` with every failure explained)
- [ ] Second run same day: `new_observations=0`
- [ ] launchd kickstart produces a new run in `ops.pipeline_run`
- [ ] **3 consecutive days** of scheduled runs in `ops.pipeline_run` without you touching anything

Phase 2 is only done when the last box is ticked. A pipeline that needs you to run it is not automated.

---

## 7. Known limitations (deliberate, for later phases)

- Reed not collected yet (Phase 3). Its history starts later than the boards'.
- If a job's content changes twice in one day, only the first version that day is kept.
- Runs only while the Mac is on. Gaps are recorded, not filled. A cloud scheduler fixes this (Phase 3 of the original plan, optional).
- No alerting yet: a failure is visible in the logs and `ops.pipeline_run`, but nothing notifies you.

---

## 8. Interview questions this phase prepares you for

- *"How do you make a pipeline idempotent?"* Natural keys (source, board, job id, date) plus `ON CONFLICT DO NOTHING`. Re-runs are safe.
- *"How do you handle schema changes from a source?"* Response-level validation fails loudly; raw JSONB stores new fields automatically; parsing happens later in dbt.
- *"What happens if the database is down?"* Raw files are written first; `reload_files` loads them later. Tested.
- *"How do you track history without storing duplicates?"* Daily observations + content-hashed payload versions.
- *"How do you avoid false 'job closed' signals?"* Per-board run status: a failed fetch is unknown, not closed.
