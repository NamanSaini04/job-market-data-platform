-- Which Reed search found each job each day, and whether it was posted by the employer or an agency.
select
    source_job_id,
    observed_date,
    search_term,
    posted_by
from {{ source('raw', 'reed_search_hit') }}
