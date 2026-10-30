"""Phase 6 fix (30 Oct 2026): settings that keep Airflow from killing work when the laptop
sleeps. Plain YAML checks, so they run in the normal test suite without Airflow installed."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def airflow_env() -> dict:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    return compose["services"]["airflow-scheduler"]["environment"]


def test_heartbeat_timeout_survives_a_laptop_sleep():
    # default 300 s: every sleep longer than 5 min killed the running task (25-30 Oct)
    assert int(airflow_env()["AIRFLOW__SCHEDULER__TASK_INSTANCE_HEARTBEAT_TIMEOUT"]) >= 3600


def test_ui_only_reachable_from_this_mac():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    assert compose["services"]["airflow-apiserver"]["ports"] == ["127.0.0.1:8080:8080"]
