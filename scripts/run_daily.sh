#!/bin/bash
# Daily pipeline entry point. Called by launchd (see scripts/launchd/), or by hand:
#   bash scripts/run_daily.sh
#
# Steps: make sure Docker and Postgres are up -> apply migrations -> collect -> dbt build.
# Everything is logged to data/logs/daily_<date>.log

set -uo pipefail

# launchd starts jobs with an almost empty PATH, so set it explicitly.
export PATH="/usr/local/bin:/opt/homebrew/bin:$HOME/.docker/bin:/usr/bin:/bin:/usr/sbin:/sbin"

cd "$(dirname "$0")/.." || exit 1
mkdir -p data/logs
exec >>"data/logs/daily_$(date +%F).log" 2>&1
echo "=== $(date '+%F %T') start"

# 1. Docker Desktop running?
if ! docker info >/dev/null 2>&1; then
  echo "Docker not running, starting Docker Desktop"
  open -ga Docker
  for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 5; done
fi

# 2. Database up and healthy? (If not, the collector still saves files: no data lost.)
docker compose up -d db
for _ in $(seq 1 30); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' jobmarket-db 2>/dev/null)" = "healthy" ] && break
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

status=$(( ats != 0 || reed != 0 || dbt_status != 0 ))

echo "=== $(date '+%F %T') end exit=$status (boards=$ats reed=$reed dbt=$dbt_status)"
exit $status
