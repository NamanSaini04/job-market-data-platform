# Phase 6: Airflow orchestration

Status: built 24 Oct 2026. Airflow 3.3.2 in Docker (LocalExecutor) replaces launchd + `run_daily.sh`.

---

## 1. What this phase builds

```
migrate ──┬── collect_boards (retries 2 x 15 min) ──┐
          └── collect_reed   (no retries) ──────────┴── export_seeds ── dbt_seed ── dbt_build ── export_dashboard
                                                       (runs even if a                          (only after a
                                                        collector failed)                        passing build)
collect_boards + collect_reed + export_dashboard ──> pipeline_status  (red if ANY step failed)
```

| File | Purpose |
|---|---|
| `airflow/dags/job_market_daily.py` | The DAG: 8 tasks, schedule 09:30 Asia/Kolkata |
| `airflow/Dockerfile` | Airflow image + separate Python environments for the collectors and dbt |
| `airflow/requirements-pipeline.txt` | Collector libraries inside the image |
| `airflow/tests/test_dag.py` | 11 DAG integrity tests (run inside the image) |
| `tests/test_run_daily_guard.py` | launchd script stands down while Airflow runs |
| `docker-compose.yml` | New `airflow` profile: own metadata DB, init, API server, scheduler, DAG processor |
| `scripts/run_daily.sh` | Kept as a manual fallback; stands down while Airflow's scheduler is running |

## 2. Why it is built this way

### What Airflow adds over launchd (and what it does not)
launchd already ran the job daily and caught up after sleep. Airflow does not make the data
better. It adds:
- **Per-step visibility**: which step failed, when, with its own log, in a UI.
- **Per-step retries**: a network blip on company boards is retried without re-running Reed.
- **Run history**: every day's run and each step's duration, kept and browsable.
- **Explicit dependencies**: the order and the "what may continue after a failure" rules are
  code, tested, not lines in a shell script.

Cost: more RAM while Docker runs (an estimate of 2-3 GB, not measured: check Docker Desktop's
memory graph after a week) and more moving parts that can fail.

### One task per step
A single "run everything" task would hide where it failed and force a full re-run on retry.

### Trigger rules: continue, but stay honest
- `export_seeds` uses `all_done`: transformation runs after both collectors have **finished**,
  success or not. The closure rule treats a failed board as "unknown", so a partial day is
  safe to model and the dashboard still shows what we have.
- `export_dashboard` uses the default `all_success`: a failed dbt test keeps yesterday's
  checked numbers on the public page.
- `pipeline_status` uses `all_success` on both collectors and the export. **Airflow marks a run
  by its last tasks.** Without this gate, a day where Reed failed but dbt passed would show
  green. With it, the run is red, while the useful downstream work still happened.

### Retry policy per step
| Task | Retries | Why |
|---|---|---|
| migrate | 3 x 1 min | the database may still be starting after Docker wakes |
| collect_boards | 2 x 15 min | network blips pass; loads are idempotent, so a re-run adds no duplicates |
| collect_reed | 2 x 20 min | safe since 30 Oct because a run resumes: skips searches done today, no calls after a 429 today, per-day detail cap (section 8). Before that: 0, since a retry was a full second sweep |
| everything else | 0 | dbt failures are data or code problems; retrying does not fix them |

Every task has a timeout, and the whole run has a 4-hour limit.

### Three Python environments in one image
Airflow and dbt pin many of the same libraries to different versions; installing both
together is a well-known source of breakage. Airflow only runs commands; the collectors and
dbt each have their own environment.

### Airflow's metadata database is separate from the job data
Airflow keeps its own bookkeeping (runs, task states). It has its own Postgres container and
volume, so resetting or upgrading Airflow can never touch the job history.

### Security
- UI bound to `127.0.0.1:8080`: reachable from this Mac only, never the Wi-Fi network. That
  is why no login screen is configured.
- The Reed key reaches the collectors through `.env` (never in the image, never in the DAG).
- `AIRFLOW_JWT_SECRET` (in `.env`) signs the tokens Airflow's parts use to talk to each other.

### Inside Docker, the database is `db:5432`, not `localhost:5433`
The compose file sets `PGHOST=db` and `PGPORT=5432` for the Airflow containers. The collectors
load `.env` without overriding variables that are already set, so these win.

### dbt build files stay out of the shared folder
The project folder is shared between the Mac and the container. dbt in the container writes
its build files to `/tmp/dbt-target`, so the Mac's dbt and the container's dbt never
overwrite each other's state.

## 3. Laptop sleep: what happens to the 09:30 run
Checked against Airflow's own scheduling code (`CronTriggerTimetable`, `catchup=False`):

| Laptop wakes at | What Airflow does |
|---|---|
| 14:00, slept through today's 09:30 | runs today's job immediately |
| 08:00, slept through yesterday | runs yesterday's missed job now, then today's at 09:30 |

No day is silently skipped. The second case means two runs in one day: safe (idempotent
loads), but Reed is called twice. `max_active_runs=1` stops them overlapping.

Docker Desktop must be running for any of this: set "Start Docker Desktop when you sign in".

## 4. Verification done (24 Oct 2026)
The real DAG, real collectors and real dbt, against a copy of the data, in an environment
with no internet access to the job APIs, so both collectors fail. That is the scenario worth testing.

| Task | Result | Shows |
|---|---|---|
| migrate | success | |
| collect_boards | failed after **3 tries** | 2 retries, then gives up |
| collect_reed | failed after **1 try** | no blind retries against Reed |
| export_seeds -> dbt_seed -> dbt_build -> export_dashboard | **all success** | a collector failure does not block transformation |
| pipeline_status | upstream_failed | the gate catches it |
| **DAG run** | **failed (red)** | no false green |

- Run 1 used the real 15-minute retry wait (retries at 08:50 and 09:09 confirmed). It was
  stopped by the test harness's own time limit before finishing.
- Run 2 (the table) loaded the same DAG file and shortened only the retry WAIT in memory to
  5 seconds; retry counts, trigger rules and commands were unchanged.
- Not run: a failing dbt build. That `export_dashboard` is skipped then is covered by the
  integrity test on its trigger rule, not by a run.
- Mutation check: making the gate `all_done`, or giving Reed retries, fails the DAG tests.
- Missed-schedule behaviour: section 3.
- launchd guard: 3 tests with a fake `docker` (skips when Airflow runs, runs otherwise, FORCE=1).

## 5. Switch over from launchd
1. Add the shared secret to `.env` (once):
   `echo "AIRFLOW_JWT_SECRET=$(openssl rand -hex 32)" >> .env`
2. Build and start: `docker compose --profile airflow up -d --build`
3. DAG tests inside the image: `docker compose --profile airflow run --rm airflow-cli pytest airflow/tests -q`
4. Open http://localhost:8080, unpause `job_market_daily`, trigger one run, watch it finish.
5. Turn launchd off: `launchctl unload ~/Library/LaunchAgents/com.namansaini.jobmarket.daily.plist`
   (the guard in `run_daily.sh` already stands down while Airflow runs; this removes the timer).
6. Docker Desktop -> Settings -> General -> "Start Docker Desktop when you sign in".

Rollback: `docker compose --profile airflow down`, then
`launchctl load ~/Library/LaunchAgents/com.namansaini.jobmarket.daily.plist`.

## 6. Known limitations
- **Two runs on some days** after the laptop sleeps through a whole day (section 3).
  Cheap since 30 Oct: the second run resumes, and the Reed detail cap is per day.
- **Docker must be running.** If Docker Desktop is quit, nothing runs, and nothing alerts.
  Airflow cannot report its own absence; the dashboard's "Data as of" date is the backstop.
- **No alerting yet.** A red run is visible in the UI, not sent anywhere.
- **The image was not built in the development environment** (Docker Hub blocked there). The
  base tag `apache/airflow:3.3.2-python3.12` is confirmed in the official docs; the first
  real build is on the Mac. The first two builds there failed while pip was fetching
  packages: one reported a version conflict, the next "no versions found" for PyYAML.
  - My first diagnosis was wrong: I blamed the image's Airflow version list (constraints)
    leaking into our venvs, and "fixed" that. Checking the image showed it sets no
    constraints, no package index and no pip config at all.
  - The decisive test: the exact failing install, run with `docker run` on the same Mac,
    succeeded. So the versions are fine and the downloads failed during the build.
  - Fix: pip retries (10) and a 60 s timeout in the Dockerfile.
  - Lesson: reproduce the failing step on its own before changing anything. My first
    "reproduction" added the very condition I suspected, so it proved nothing.

## 8. Incident: Airflow killed Reed every day the laptop slept (25-30 Oct 2026)

**What happened.** After the switch to Airflow, company boards succeeded every day, but Reed
never finished a run: 1-7 of 14 searches, then nothing. No Reed details for six days.

**How it was found.** Not from the Airflow UI, from the pipeline's own files: raw Reed search
pages dropped from ~153/day to 4-29/day, and no Reed run summary was written after 24 Oct.
The Airflow task log then showed the timeline:
```
04:21:49  reed run started
05:35:37  first query done        <- 74 minutes later: the Mac was asleep in between
05:35:47  Sending SIGTERM         <- 10 s after waking
```
Reed itself was healthy (HTTP 200 in 0.45 s).

**Root cause.** Airflow's scheduler declares a task dead if it has not heartbeated for
`task_instance_heartbeat_timeout` (default 300 s), then kills it. A sleeping Mac freezes
Docker, so on waking every running task looked dead. Company boards (~30 s) usually finished
before the Mac slept; Reed (4+ min) did not. launchd never had this problem: nothing
supervises a launchd job, it simply pauses and resumes.

**Lesson.** Airflow assumes always-on infrastructure. Its safety mechanism killed healthy work
on a laptop. The data quality design held: Reed's incomplete days were "unknown", not
"closed", so no job was wrongly closed. The six days of Reed data are lost for good: Reed has
no date filter, so past days cannot be re-collected.

**Fix, in three layers.**
1. `AIRFLOW__SCHEDULER__TASK_INSTANCE_HEARTBEAT_TIMEOUT=10800` (3 h): ordinary sleeps pass.
   Trade-off: a genuinely crashed task is noticed after 3 h, not 5 min. Fine for a daily batch.
2. Reed resumes. A run reads what earlier runs did today: skips searches already done, makes no
   calls if Reed returned 429 earlier today, and treats the detail cap as per day. A retry
   now costs only what is missing, so `collect_reed` gets 2 retries, 20 min apart.
3. Time limits count wall-clock time, sleep included: Reed 4 h, other steps 2 h, whole run 12 h.

**Tests.** Retry fetches only the failed search; no calls after an earlier 429; detail cap is
per day; `--no-resume` still sweeps everything; compose keeps the 3 h heartbeat timeout.
Mutation-checked: disabling the resume or the per-day cap fails 3 tests.

## 7. Completion criteria
- [x] DAG integrity tests pass (11)
- [x] Real DAG run with failing collectors behaves as designed (section 4)
- [x] Missed-schedule behaviour checked against Airflow's code
- [x] launchd script cannot double-run while Airflow is active (3 tests)
- [ ] User: image builds and the stack starts on the Mac
- [ ] User: DAG runs green 3 days in a row (restarted 30 Oct after the sleep fix, section 8)
