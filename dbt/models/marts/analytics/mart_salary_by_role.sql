-- Grain: one row per role family per seniority (plus seniority = 'all').
-- Uses ONLY salaries the advert states as GBP per year (never Reed search estimates).
-- Midpoint of min/max per posting. is_low_sample when fewer than 20 salaries: show, but warn.
with s as (
    select role_family, seniority, salary_mid_gbp
    from {{ ref('fact_job_postings') }}
    where is_in_market_scope and salary_mid_gbp is not null
    union all
    select role_family, 'all', salary_mid_gbp
    from {{ ref('fact_job_postings') }}
    where is_in_market_scope and salary_mid_gbp is not null
),
totals as (
    select role_family, seniority, count(*) as postings_in_scope
    from (select role_family, seniority from {{ ref('fact_job_postings') }} where is_in_market_scope
          union all
          select role_family, 'all' from {{ ref('fact_job_postings') }} where is_in_market_scope) t
    group by 1, 2
)
select
    s.role_family,
    s.seniority,
    count(*)                                                                         as postings_with_salary,
    t.postings_in_scope,
    round(100.0 * count(*) / t.postings_in_scope, 1)                                 as pct_with_salary,
    round(percentile_cont(0.25) within group (order by salary_mid_gbp)::numeric, -2) as p25_gbp,
    round(percentile_cont(0.50) within group (order by salary_mid_gbp)::numeric, -2) as median_gbp,
    round(percentile_cont(0.75) within group (order by salary_mid_gbp)::numeric, -2) as p75_gbp,
    count(*) < 20                                                                    as is_low_sample
from s
join totals t using (role_family, seniority)
group by s.role_family, s.seniority, t.postings_in_scope
