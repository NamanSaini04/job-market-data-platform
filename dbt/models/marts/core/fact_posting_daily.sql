-- Grain: one row per posting per day it was seen live. Answers "how many X jobs were live
-- on day D" without guessing. Days a posting was NOT seen have no row (absence is handled
-- by the closure rule in int_posting_history, not here).
select
    md5(o.source || '|' || o.board || '|' || o.source_job_id) as posting_key,
    o.observed_date                                           as date_day,
    o.observed_date = f.first_seen_date                       as is_first_seen_day
from {{ ref('stg_observations') }} o
join {{ ref('fact_job_postings') }} f
  on f.posting_key = md5(o.source || '|' || o.board || '|' || o.source_job_id)
