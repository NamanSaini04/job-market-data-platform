"""Phase 6: launchd's script must stand down while Airflow is scheduling, so the pipeline
never runs twice in a day. Uses a fake `docker` command; nothing real is started."""

import os
import shutil
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def setup(tmp_path: Path, running: str) -> Path:
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts/run_daily.sh", tmp_path / "scripts/run_daily.sh")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    fake = bin_ / "docker"
    # "docker info" succeeds; "docker ps" lists the containers we say are running;
    # anything else (compose up, inspect) is recorded so the test can see it happened.
    fake.write_text(f"""#!/bin/bash
case "$1" in
  info) exit 0 ;;
  ps)   printf '%s\\n' {running!r} ;;
  inspect) echo healthy ;;
  *)    echo "$@" >> "{tmp_path}/docker_calls" ;;
esac
""")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return bin_


def run(tmp_path, bin_, **env):
    e = {**os.environ, "DOCKER": str(bin_ / "docker"), **env}
    r = subprocess.run(["bash", str(tmp_path / "scripts/run_daily.sh")], env=e, timeout=60)
    log = next((tmp_path / "data/logs").glob("daily_*.log")).read_text()
    return r.returncode, log


def test_skips_when_airflow_scheduler_is_running(tmp_path):
    bin_ = setup(tmp_path, "jobmarket-airflow-scheduler")
    code, log = run(tmp_path, bin_)
    assert code == 0
    assert "skipping" in log
    assert not (tmp_path / "docker_calls").exists(), "must not even start the database"


def test_runs_when_airflow_is_not_running(tmp_path):
    bin_ = setup(tmp_path, "jobmarket-db")
    code, log = run(tmp_path, bin_)
    assert "skipping" not in log
    assert "compose up -d db" in (tmp_path / "docker_calls").read_text()


def test_force_overrides_the_guard(tmp_path):
    bin_ = setup(tmp_path, "jobmarket-airflow-scheduler")
    code, log = run(tmp_path, bin_, FORCE="1")
    assert "skipping" not in log
