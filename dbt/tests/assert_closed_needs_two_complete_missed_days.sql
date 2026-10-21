-- The closure rule: a posting may only be 'closed' if its board was fully collected on
-- at least 2 days after it was last seen. Anything else must be 'open' or 'unknown'.
select posting_key, status, complete_days_missed
from {{ ref('fact_job_postings') }}
where status = 'closed' and complete_days_missed < 2
