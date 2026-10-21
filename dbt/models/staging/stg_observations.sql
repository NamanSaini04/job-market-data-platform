-- One row per job, per board, per day seen.
select
    source,
    board,
    source_job_id,
    observed_date,
    payload_hash
from {{ source('raw', 'job_observation') }}
