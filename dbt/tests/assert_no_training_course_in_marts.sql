-- Training-course adverts must never reach the analytics marts.
select f.posting_key
from {{ ref('fact_job_postings') }} f
where f.is_in_market_scope and f.is_training_course
