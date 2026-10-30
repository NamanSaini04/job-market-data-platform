"""DAG integrity tests. Run INSIDE the Airflow image (they need Airflow installed):

    docker compose --profile airflow run --rm airflow-cli pytest airflow/tests -q

Each test pins one design decision from docs/06_airflow.md, so changing it is deliberate.
"""

import time
from datetime import timedelta
from pathlib import Path

import pytest
from airflow.dag_processing.dagbag import DagBag

DAGS = Path(__file__).resolve().parents[1] / "dags"


@pytest.fixture(scope="module")
def dagbag():
    return DagBag(dag_folder=str(DAGS))


@pytest.fixture(scope="module")
def dag(dagbag):
    return dagbag.dags["job_market_daily"]


def test_no_import_errors(dagbag):
    assert dagbag.import_errors == {}, dagbag.import_errors


def test_dag_file_parses_fast():
    # The scheduler re-parses DAG files constantly. No API calls or DB queries at import time.
    t = time.perf_counter()
    DagBag(dag_folder=str(DAGS))
    assert time.perf_counter() - t < 5


def test_expected_tasks(dag):
    assert set(dag.task_ids) == {"migrate", "collect_boards", "collect_reed", "export_seeds",
                                 "dbt_seed", "dbt_build", "export_dashboard", "pipeline_status"}


def edges(dag):
    return {(up, t.task_id) for t in dag.tasks for up in t.upstream_task_ids}


def test_dependencies(dag):
    assert edges(dag) == {
        ("migrate", "collect_boards"), ("migrate", "collect_reed"),
        ("collect_boards", "export_seeds"), ("collect_reed", "export_seeds"),
        ("export_seeds", "dbt_seed"), ("dbt_seed", "dbt_build"),
        ("dbt_build", "export_dashboard"),
        ("collect_boards", "pipeline_status"), ("collect_reed", "pipeline_status"),
        ("export_dashboard", "pipeline_status"),
    }


def test_collector_failure_does_not_block_transformation(dag):
    assert dag.get_task("export_seeds").trigger_rule == "all_done"


def test_dashboard_export_needs_a_fully_passing_build(dag):
    assert dag.get_task("export_dashboard").trigger_rule == "all_success"
    assert dag.get_task("dbt_build").trigger_rule == "all_success"


def test_run_is_red_if_any_step_failed(dag):
    gate = dag.get_task("pipeline_status")
    assert gate.trigger_rule == "all_success"
    assert {"collect_boards", "collect_reed", "export_dashboard"} <= gate.upstream_task_ids
    assert not gate.downstream_task_ids, "the gate must be the last task"


def test_retry_policy(dag):
    assert dag.get_task("collect_boards").retries == 2
    # Reed retries are safe only because run_reed resumes (tests/test_reed.py: retry fetches
    # only failed searches, makes no calls after a 429, detail cap is per day).
    reed = dag.get_task("collect_reed")
    assert reed.retries == 2 and reed.retry_delay >= timedelta(minutes=15)
    assert "--no-resume" not in reed.bash_command, "retries must resume, not re-sweep"
    assert dag.get_task("migrate").retries >= 1


def test_every_task_has_a_timeout(dag):
    for t in dag.tasks:
        assert t.execution_timeout and t.execution_timeout <= timedelta(hours=4), t.task_id


def test_schedule_and_concurrency(dag):
    assert dag.catchup is False
    assert dag.max_active_runs == 1
    assert dag.dagrun_timeout <= timedelta(hours=24), "must end before tomorrow's run"
    # time limits count wall-clock time INCLUDING laptop sleep: leave room for one
    assert dag.get_task("collect_reed").execution_timeout >= timedelta(hours=3)


def test_dbt_keeps_its_build_files_out_of_the_shared_folder(dag):
    for tid in ("dbt_seed", "dbt_build"):
        assert "--target-path /tmp/" in dag.get_task(tid).bash_command
