-- Reed search results, one row per content version.
-- Search salaries are NOT labelled annual/daily/hourly (only detail records are), so they are
-- kept here for reference but salary analysis uses stg_reed_details only.
select
    source,
    board,
    source_job_id,
    payload_hash,
    first_seen_at                                     as version_first_seen_at,
    trim(payload->>'jobTitle')                        as job_title,
    trim(payload->>'employerName')                    as employer_name,
    payload->>'locationName'                          as location_text,
    to_date(payload->>'date', 'DD/MM/YYYY')           as posted_date,
    to_date(payload->>'expirationDate', 'DD/MM/YYYY') as expiration_date,
    {{ html_to_text("payload->>'jobDescription'") }}  as description_snippet,
    payload->>'jobUrl'                                as job_url,
    (payload->>'minimumSalary')::numeric              as search_salary_min,
    (payload->>'maximumSalary')::numeric              as search_salary_max,
    payload->>'currency'                              as search_salary_currency
from {{ source('raw', 'job_payload') }}
where source = 'reed' and board = 'search'
