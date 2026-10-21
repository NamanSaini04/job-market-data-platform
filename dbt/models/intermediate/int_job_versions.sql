-- Every content version of every job, from all sources, in ONE shape.
-- content_signature covers what a job seeker sees (title, description, salary), NOT labels
-- like department/team. So Faculty moving 62 jobs to new teams (17 Oct) is not counted as
-- 62 job edits (see docs/04_dbt.md).
{{ config(post_hook=["create unique index if not exists {{ this.name }}_key_idx on {{ this }} (version_key)", "analyze {{ this }}"]) }}

with ats as (
    select
        v.source, v.board, v.source_job_id, v.payload_hash,
        v.job_title,
        c.company_name                  as company_name_raw,
        v.location_text, v.country_code, v.posted_date,
        v.work_type_raw, v.employment_type_raw, v.department,
        v.description_text, v.job_url,
        v.salary_min, v.salary_max, v.salary_currency, v.salary_interval
    from {{ ref('stg_ats_versions') }} v
    left join {{ ref('ats_companies') }} c using (source, board)
),

reed as (
    select
        source, board, source_job_id, payload_hash,
        job_title,
        employer_name                   as company_name_raw,
        location_text,
        'GB'                            as country_code,
        posted_date,
        null::text                      as work_type_raw,
        null::text                      as employment_type_raw,
        null::text                      as department,
        description_snippet             as description_text,    -- full text comes from stg_reed_details
        job_url,
        null::numeric                   as salary_min,          -- unlabelled: see stg_reed_search_versions
        null::numeric                   as salary_max,
        null::text                      as salary_currency,
        null::text                      as salary_interval
    from {{ ref('stg_reed_search_versions') }}
),

unioned as (
    select * from ats
    union all
    select * from reed
)

-- version_key: ONE join column instead of four. Postgres treats the four columns as
-- independent when estimating joins (guessed 1 row, got 11,070) and chose slow plans.
select
    md5(source || '|' || board || '|' || source_job_id || '|' || payload_hash) as version_key,
    md5(source || '|' || board || '|' || source_job_id)                        as posting_key,
    *,
    md5(concat_ws('|', lower(job_title), description_text, salary_min::text, salary_max::text))
        as content_signature
from unioned
