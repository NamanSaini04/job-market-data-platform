-- Fails (returns rows) if any stated salary range is backwards or outside 10k-500k.
select posting_key, salary_min_gbp, salary_max_gbp
from {{ ref('fact_job_postings') }}
where salary_min_gbp > salary_max_gbp
   or salary_min_gbp < 10000
   or salary_max_gbp > 500000
   or (salary_min_gbp is null) <> (salary_max_gbp is null)
