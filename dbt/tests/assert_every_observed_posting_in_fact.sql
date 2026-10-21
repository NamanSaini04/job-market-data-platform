-- Nothing collected is silently dropped: every (source, board, job) observed in raw
-- must have exactly one row in fact_job_postings.
with observed as (
    select distinct md5(source || '|' || board || '|' || source_job_id) as posting_key
    from {{ source('raw', 'job_observation') }}
)
select o.posting_key
from observed o
left join {{ ref('fact_job_postings') }} f using (posting_key)
where f.posting_key is null
