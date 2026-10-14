"""All configuration in one place, read from environment variables (.env)."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    pg_host: str = os.getenv("PGHOST", "localhost")
    pg_port: int = int(os.getenv("PGPORT", "5433"))
    pg_user: str = os.getenv("PGUSER", "jobs")
    pg_password: str = os.getenv("PGPASSWORD", "")
    pg_database: str = os.getenv("PGDATABASE", "jobmarket")

    data_dir: Path = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
    config_dir: Path = ROOT / "config"
    migrations_dir: Path = ROOT / "database" / "migrations"

    http_timeout_s: int = 30
    pause_between_requests_s: float = float(os.getenv("PAUSE_BETWEEN_REQUESTS_S", "1.0"))
    user_agent: str = (
        "job-market-data-platform/0.2 (portfolio project; "
        "https://github.com/NamanSaini04/job-market-data-platform)"
    )


def get_settings() -> Settings:
    return Settings()
