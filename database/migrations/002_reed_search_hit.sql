-- 002: Reed search lineage.
--
-- Reed has no "all jobs" endpoint. We find jobs by running searches:
-- 7 role search terms x 2 poster types (direct employer / recruitment agency).
-- One job can be returned by several searches on the same day.
--
-- raw.job_observation still holds ONE row per job per day (board = 'search').
-- This table records WHICH searches returned it. Two uses:
--   * posted_by gives every job an agency/direct tag (Reed's own flags; Phase 1
--     showed the two filters split the results exactly).
--   * lineage: we can always explain why a job is in the dataset.

CREATE TABLE raw.reed_search_hit (
    source_job_id  text        NOT NULL,
    observed_date  date        NOT NULL,
    search_term    text        NOT NULL,     -- slug, e.g. 'data-engineer'
    posted_by      text        NOT NULL CHECK (posted_by IN ('direct', 'agency')),
    run_id         uuid        NOT NULL REFERENCES ops.pipeline_run (run_id),
    PRIMARY KEY (source_job_id, observed_date, search_term, posted_by)
);

CREATE INDEX reed_search_hit_date_idx ON raw.reed_search_hit (observed_date);
