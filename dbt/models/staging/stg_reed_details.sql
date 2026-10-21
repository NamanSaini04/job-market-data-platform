-- Reed detail records: full description and LABELLED salary. Latest version per job.
-- Only fetched for title-matched data roles (see ingestion/run_reed.py).
select distinct on (source_job_id)
    source_job_id,
    {{ html_to_text("payload->>'jobDescription'") }} as description_text,
    lower(payload->>'salaryType')                    as salary_type,      -- per annum / per day / per hour
    payload->>'currency'                             as salary_currency,
    (payload->>'minimumSalary')::numeric             as salary_min,
    (payload->>'maximumSalary')::numeric             as salary_max,
    (payload->>'yearlyMinimumSalary')::numeric       as yearly_salary_min,
    (payload->>'yearlyMaximumSalary')::numeric       as yearly_salary_max,
    payload->>'contractType'                         as contract_type,
    (payload->>'fullTime')::boolean                  as is_full_time,
    (payload->>'partTime')::boolean                  as is_part_time
from {{ source('raw', 'job_payload') }}
where source = 'reed' and board = 'detail'
order by source_job_id, first_seen_at desc
