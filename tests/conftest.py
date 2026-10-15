"""
Shared test setup.

Integration tests need Postgres. They use a SEPARATE database, `jobmarket_test`,
and wipe it before every test. As a safety rule, the reset refuses to run on any
database whose name does not end in `_test`, so tests can never touch real data.
If Postgres isn't reachable, integration tests are skipped, not failed.
"""

import json
from dataclasses import replace
from pathlib import Path

import psycopg
import pytest

from ingestion.db import connect, migrate
from ingestion.settings import get_settings

FIXTURES = Path(__file__).parent / "fixtures"
TEST_DB = "jobmarket_test"


def fixture_records(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def fake_responses() -> dict[str, bytes]:
    """Rebuild full API responses from the saved Phase 1 sample records."""
    return {
        "greenhouse": json.dumps({"jobs": fixture_records("greenhouse"), "meta": {}}).encode(),
        "lever": json.dumps(fixture_records("lever")).encode(),
        "ashby": json.dumps({"jobs": fixture_records("ashby")}).encode(),
    }


@pytest.fixture
def settings(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "ats_companies.yaml").write_text(
        "companies:\n"
        "  - {company: GH Co, ats: greenhouse, slug: ghco, status: verified}\n"
        "  - {company: Lever Co, ats: lever, slug: leverco, status: verified}\n"
        "  - {company: Ashby Co, ats: ashby, slug: ashbyco, status: verified}\n"
        "  - {company: Not Yet, ats: ashby, slug: notyet, status: unverified}\n")
    (cfg / "roles.yaml").write_text(
        "role_families:\n"
        "  - {name: Data Engineer, search_terms: [data engineer], title_patterns: [data engineer]}\n"
        "  - {name: Data Analyst, search_terms: [data analyst], title_patterns: [data analyst]}\n")
    return replace(get_settings(), data_dir=tmp_path / "data", config_dir=cfg,
                   pg_database=TEST_DB, pause_between_requests_s=0)


@pytest.fixture
def responses():
    return fake_responses()


@pytest.fixture
def fetcher(responses):
    """A fake HTTP fetch: returns saved responses by ATS, never touches the network."""
    def fetch(url: str) -> bytes:
        for source, body in responses.items():
            if source in url:
                return body
        raise AssertionError(f"unexpected url {url}")
    return fetch


def _reset(conn: psycopg.Connection) -> None:
    dbname = conn.info.dbname
    assert dbname.endswith("_test"), f"refusing to wipe non-test database {dbname!r}"
    conn.execute("DROP SCHEMA IF EXISTS raw CASCADE")
    conn.execute("DROP SCHEMA IF EXISTS ops CASCADE")
    conn.commit()


@pytest.fixture
def db(settings):
    try:
        admin = connect(settings, dbname="postgres")
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not reachable ({str(e)[:80]}). Start it: docker compose up -d")
    admin.autocommit = True
    if not admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (TEST_DB,)).fetchone():
        admin.execute(f"CREATE DATABASE {TEST_DB}")
    admin.close()

    conn = connect(settings)
    _reset(conn)
    migrate(conn, settings.migrations_dir)
    yield conn
    conn.close()


def count(conn, table: str) -> int:
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
