"""
Daily pipeline DAG (Phase 6). Replaces scripts/run_daily.sh + launchd.

    migrate ──┬── collect_boards ──┐
              └── collect_reed ────┴── export_seeds ── dbt_seed ── dbt_build ── export_dashboard ──┐
                                                                                                    ├── pipeline_status
              collect_boards, collect_reed ─────────────────────────────────────────────────────────┘

Design (docs/06_airflow.md):
  * One task per step: a failure shows WHERE, and only that step is retried.
  * Collectors never block transformation: export_seeds runs when both collectors have
    FINISHED, success or not ("all_done"). The closure rule already treats a failed board as
    "unknown", so a partial day is safe to model.
  * The dashboard export runs only after a fully passing dbt build (default "all_success"):
    a failed data test keeps yesterday's checked numbers on the public page.
  * pipeline_status exists because Airflow marks a run by its LAST tasks. Without it, a run
    where Reed failed but dbt passed would show green. It succeeds only if everything did.
  * Retries: company boards 2x, 15 min apart (network blips pass; loads are idempotent).
    Reed 0x: its HTTP client already retries, and re-running after a rate-limit block
    would spend ~130 more calls against an undocumented limit.
  * max_active_runs=1: two runs at once would double the Reed calls and race on the same day.

Paths come from environment variables so the same file runs in Docker and in tests.
"""

import os
from datetime import timedelta

import pendulum
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG

PROJECT = os.getenv("PROJECT_DIR", "/opt/project")
PY = os.getenv("PIPELINE_PYTHON", "/home/airflow/.venvs/pipeline/bin/python")
DBT = os.getenv("DBT_BIN", "/home/airflow/.venvs/dbt/bin/dbt")
# dbt's own build files go outside the shared project folder, so the Mac's dbt and the
# container's dbt never overwrite each other's state.
DBT_ARGS = "--project-dir dbt --profiles-dir dbt --target-path /tmp/dbt-target"
SCHEDULE_TZ = "Asia/Kolkata"           # same wall-clock time launchd used (09:30)

default_args = {
    "owner": "naman",
    "retries": 0,
    "execution_timeout": timedelta(minutes=30),
}

with DAG(
    dag_id="job_market_daily",
    description="Collect UK data/AI job postings, model with dbt, export dashboard data",
    schedule="30 9 * * *",
    start_date=pendulum.datetime(2026, 10, 24, tz=SCHEDULE_TZ),
    catchup=False,                     # a laptop asleep for 3 days runs once on waking, not 3x
    max_active_runs=1,
    dagrun_timeout=timedelta(hours=4),
    default_args=default_args,
    tags=["job-market"],
    doc_md=__doc__,
) as dag:

    def step(task_id: str, command: str, **kwargs) -> BashOperator:
        return BashOperator(task_id=task_id, bash_command=command, cwd=PROJECT, **kwargs)

    migrate = step("migrate", f"{PY} -m ingestion.db migrate",
                   retries=3, retry_delay=timedelta(minutes=1))     # DB may still be starting

    collect_boards = step("collect_boards", f"{PY} -m ingestion.run_ats",
                          retries=2, retry_delay=timedelta(minutes=15))

    collect_reed = step("collect_reed", f"{PY} -m ingestion.run_reed",
                        retries=0, execution_timeout=timedelta(minutes=120))

    export_seeds = step("export_seeds", f"{PY} scripts/export_seeds.py",
                        trigger_rule="all_done")

    dbt_seed = step("dbt_seed", f"{DBT} seed {DBT_ARGS}")

    dbt_build = step("dbt_build", f"{DBT} build {DBT_ARGS}",
                     execution_timeout=timedelta(minutes=60))

    export_dashboard = step("export_dashboard", f"{PY} scripts/export_dashboard_data.py")

    pipeline_status = step(
        "pipeline_status",
        "echo 'every step succeeded'",
        trigger_rule="all_success",
        doc_md="Fails (upstream_failed) if ANY collector or the dbt chain failed, so the run "
               "is red even though downstream steps were allowed to continue.",
    )

    migrate >> [collect_boards, collect_reed] >> export_seeds
    export_seeds >> dbt_seed >> dbt_build >> export_dashboard
    [collect_boards, collect_reed, export_dashboard] >> pipeline_status
