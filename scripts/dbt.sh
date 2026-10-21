#!/bin/bash
# Run dbt with the project's settings and the Postgres login from .env.
#   bash scripts/dbt.sh build              build + test everything
#   bash scripts/dbt.sh run -s marts       one layer
#   bash scripts/dbt.sh source freshness   is raw data recent?
# .env is read here and never printed.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a
exec .venv/bin/dbt "$@" --project-dir dbt --profiles-dir dbt
