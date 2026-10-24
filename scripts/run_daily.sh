#!/bin/bash
# Daily pipeline entry point. Called by launchd (see scripts/launchd/), or by hand:
#   bash scripts/run_daily.sh
#
# Steps: Docker + Postgres up -> migrations -> collect -> dbt build -> dashboard export.
# Everything is logged to data/logs/daily_<date>.log

set -uo pipefail

# launchd starts jobs with an almost empty PATH, so set it explicitly.
export PATH="/usr/local/bin:/opt/homebrew/bin:$HOME/.docker/bin:/usr/bin:/bin:/usr/sbin:/sbin"
DOCKER="${DOCKER:-docker}"           # tests point this at a fake docker

cd "$(dirname "$0")/.." || exit 1
mkdir -p data/logs
exec >>"data/logs/daily_$(date +%F).log" 2>&1
echo "=== $(date '+%F %T') start"

# 1. Docker Desktop running?
if ! $DOCKER info >/dev/null 2>&1; then
  echo "Docker not running, starting Docker Desktop"
  open -ga Docker
  for _ in $(seq 1 60); do $DOCKER info >/dev/null 2>&1 && break; sleep 5; done
fi

# 1b. Phase 6: Airflow schedules the pipeline now. If its scheduler is running, do nothing,
#     so the pipeline can never run twice in a day (double Reed calls). Override by hand:
#       FORCE=1 bash scripts/run_daily.sh
if [ "${FORCE:-0}" != "1" ] && $DOCKER ps --format '{{.Names}}' | grep -qx jobmarket-airflow-scheduler; then
  echo "Airflow scheduler is running: skipping (it runs the pipeline). FORCE=1 to run anyway."
  echo "=== $(date '+%F %T') end exit=0 (skipped: Airflow active)"
  exit 0
fi

# 2. Database up and healthy? (If not, the collector still saves files: no data lost.)
$DOCKER compose up -d db
for _ in $(seq 1 30); do
  [ "$($DOCKER inspect -f '{{.State.Health.Status}}' jobmarket-db 2>/dev/null)" = "healthy" ] && break
  sleep 2
done

# 3. Migrations (does nothing if already up to date), then collect.
#    Both collectors always run: one failing must not stop the other.
.venv/bin/python -m ingestion.db migrate
.venv/bin/python -m ingestion.run_ats
ats=$?
.venv/bin/python -m ingestion.run_reed
reed=$?

# 4. Transform (Phase 4). Runs even if a collector failed: the closure rule already
#    treats failed boards as "unknown", and dashboards should show what we do have.
#    Seeds are regenerated from config/ first, so config is the one place to edit.
.venv/bin/python scripts/export_seeds.py
bash scripts/dbt.sh seed
bash scripts/dbt.sh build
dbt_status=$?

# 5. Dashboard files (Phase 5). Only after a fully passing build: if a dbt test failed,
#    the dashboard keeps yesterday's checked numbers instead of today's suspect ones.
#    Publishing is a separate, manual step (git push): see docs/05_dashboard.md.
if [ "$dbt_status" -eq 0 ]; then
  .venv/bin/python scripts/export_dashboard_data.py
fi

status=$(( ats != 0 || reed != 0 || dbt_status != 0 ))

echo "=== $(date '+%F %T') end exit=$status (boards=$ats reed=$reed dbt=$dbt_status)"
exit $status
