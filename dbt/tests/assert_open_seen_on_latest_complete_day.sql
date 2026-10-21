-- 'open' means seen on (or after) the latest day its board was fully collected.
select posting_key, last_seen_date, latest_complete_date
from {{ ref('fact_job_postings') }}
where status = 'open' and last_seen_date < latest_complete_date
