-- 001: raw (bronze) layer + operations tables.
--
-- Design rules:
--   * raw.* is append-only. Nothing here is ever updated or deleted by the pipeline.
--   * We store what the source sent (JSONB), not our interpretation of it.
--     Parsing into columns happens later, in dbt staging models.
--   * Daily snapshots are cheap: one small row per job per day (job_observation).
--     The full payload is stored only when it changes (job_payload).

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS ops;

-- One row per pipeline run. The "control panel" for monitoring.
CREATE TABLE ops.pipeline_run (
    run_id            uuid PRIMARY KEY,
    pipeline          text        NOT NULL,              -- e.g. 'ats_daily'
    observed_date     date        NOT NULL,              -- the UTC day this run snapshots
    started_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz,
    status            text        NOT NULL DEFAULT 'running'
                      CHECK (status IN ('running', 'success', 'partial', 'failed', 'reloaded')),
    boards_total      int,
    boards_failed     int,
    records_fetched   int NOT NULL DEFAULT 0,
    records_loaded    int NOT NULL DEFAULT 0,             -- new observations written
    records_duplicate int NOT NULL DEFAULT 0,             -- already observed today (re-run)
    records_rejected  int NOT NULL DEFAULT 0,
    payload_versions_new int NOT NULL DEFAULT 0,          -- new or changed job content
    error_message     text
);

-- One row per board per run. Tells us WHICH board failed, not just "something failed".
-- Later, closure logic uses this: a job only counts as closed if its board
-- was fetched successfully that day. A failed fetch is "unknown", never "closed".
CREATE TABLE ops.source_run (
    run_id           uuid NOT NULL REFERENCES ops.pipeline_run (run_id),
    source           text NOT NULL,                       -- greenhouse / lever / ashby / reed
    board            text NOT NULL,                       -- company slug (or search term for Reed)
    status           text NOT NULL CHECK (status IN ('success', 'failed')),
    records_fetched  int  NOT NULL DEFAULT 0,
    records_loaded   int  NOT NULL DEFAULT 0,
    records_rejected int  NOT NULL DEFAULT 0,
    duration_ms      int,
    raw_file         text,                                -- where the untouched response is saved
    error_message    text,
    PRIMARY KEY (run_id, source, board)
);

-- Full job content, stored once per distinct version.
-- payload_hash = SHA-256 of the canonical JSON. Same content -> same hash -> stored once.
CREATE TABLE raw.job_payload (
    source          text        NOT NULL,
    board           text        NOT NULL,
    source_job_id   text        NOT NULL,
    payload_hash    text        NOT NULL,
    payload         jsonb       NOT NULL,
    first_seen_run  uuid        NOT NULL REFERENCES ops.pipeline_run (run_id),
    first_seen_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, board, source_job_id, payload_hash)
);

-- "Job X was live on board Y on day Z, with content version H."
-- This is the table that powers history: first seen, last seen, lifetime, changes.
-- The primary key makes re-running a day harmless (idempotent).
CREATE TABLE raw.job_observation (
    source          text        NOT NULL,
    board           text        NOT NULL,
    source_job_id   text        NOT NULL,
    observed_date   date        NOT NULL,
    payload_hash    text        NOT NULL,
    run_id          uuid        NOT NULL REFERENCES ops.pipeline_run (run_id),
    ingested_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, board, source_job_id, observed_date),
    FOREIGN KEY (source, board, source_job_id, payload_hash)
        REFERENCES raw.job_payload (source, board, source_job_id, payload_hash)
);

CREATE INDEX job_observation_date_idx ON raw.job_observation (observed_date);

-- Records that failed validation. Kept, not thrown away, so we can see what went wrong.
CREATE TABLE raw.rejected_record (
    rejected_id  bigserial   PRIMARY KEY,
    run_id       uuid        NOT NULL REFERENCES ops.pipeline_run (run_id),
    source       text        NOT NULL,
    board        text        NOT NULL,
    reason       text        NOT NULL,
    payload      jsonb,
    rejected_at  timestamptz NOT NULL DEFAULT now()
);
