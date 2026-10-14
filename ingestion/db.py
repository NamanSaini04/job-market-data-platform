"""
Database connection and a minimal migration runner.

Why migrations: the schema lives in numbered SQL files in database/migrations/.
Each file runs once, in order, and is recorded in ops.schema_migration.
Changing the schema = adding a new file, never editing an old one.
That way any copy of the database (yours, a test one, a cloud one later)
can be brought to the same state with one command.

Run:  python -m ingestion.db migrate
"""

import sys

import psycopg

from ingestion.settings import Settings, get_settings


def connect(settings: Settings | None = None, dbname: str | None = None) -> psycopg.Connection:
    s = settings or get_settings()
    return psycopg.connect(
        host=s.pg_host, port=s.pg_port, user=s.pg_user,
        password=s.pg_password or None, dbname=dbname or s.pg_database,
        connect_timeout=5,
    )


def migrate(conn: psycopg.Connection, migrations_dir=None) -> list[str]:
    """Apply any migration files not yet applied. Returns the names applied now."""
    migrations_dir = migrations_dir or get_settings().migrations_dir
    with conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS ops")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ops.schema_migration (
                version    text PRIMARY KEY,
                applied_at timestamptz NOT NULL DEFAULT now()
            )""")
        cur.execute("SELECT version FROM ops.schema_migration")
        done = {r[0] for r in cur.fetchall()}
    conn.commit()

    applied = []
    for path in sorted(migrations_dir.glob("*.sql")):
        if path.name in done:
            continue
        # Each migration runs in its own transaction: it fully applies or not at all.
        with conn.transaction():
            conn.execute(path.read_text())
            conn.execute("INSERT INTO ops.schema_migration (version) VALUES (%s)", (path.name,))
        applied.append(path.name)
    return applied


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] != "migrate":
        print("usage: python -m ingestion.db migrate")
        return 2
    with connect() as conn:
        applied = migrate(conn)
    print("applied:", ", ".join(applied) if applied else "nothing (already up to date)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
