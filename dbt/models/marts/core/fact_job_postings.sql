-- Grain: one row per posting (source + board + job id). ALL postings are kept;
-- is_in_market_scope marks the ones every analytics mart uses (UK, tracked role, not a
-- training course). Keeping the rest makes exclusions auditable instead of invisible.
{{ config(post_hook=["create unique index if not exists {{ this.name }}_pk on {{ this }} (posting_key)", "analyze {{ this }}"]) }}
select
    p.posting_key,
    md5(p.company_name_normalized)          as company_key,
    md5(c.role_family)                      as role_key,
    p.source,
    p.board,
    p.source_job_id,
    p.job_title,
    p.company_name_raw                      as company_name,
    c.role_family,
    c.seniority,
    c.is_uk,
    c.city,
    c.is_training_course,
    c.training_flag_basis,
    {{ in_market_scope('c') }}              as is_in_market_scope,
    p.posted_by,
    p.work_type,
    p.work_type_basis,
    p.employment_type,
    p.salary_min_gbp,
    p.salary_max_gbp,
    (p.salary_min_gbp + p.salary_max_gbp) / 2.0 as salary_mid_gbp,
    p.day_rate_min_gbp,
    p.day_rate_max_gbp,
    p.has_full_description,
    p.posted_date,
    p.first_seen_date,
    p.last_seen_date,
    p.days_seen,
    p.was_live_before_collection_started,
    p.latest_complete_date,
    p.complete_days_missed,
    p.status,
    p.content_edits,
    p.job_url
from {{ ref('int_postings') }} p
join {{ ref('int_posting_classification') }} c using (posting_key)
