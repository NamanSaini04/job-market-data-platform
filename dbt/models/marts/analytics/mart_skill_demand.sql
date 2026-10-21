-- Grain: one row per role family per skill (plus role_family = 'All data roles').
-- share = postings mentioning the skill / postings WITH a full description (not all postings).
with scope as (
    select posting_key, role_family
    from {{ ref('fact_job_postings') }}
    where is_in_market_scope and has_full_description
),
scope_all as (
    select posting_key, role_family from scope
    union all
    select posting_key, 'All data roles' from scope
),
denominators as (
    select role_family, count(*) as postings_with_description
    from scope_all group by 1
)
select
    s.role_family,
    b.skill,
    k.category,
    count(*)                                            as postings_mentioning,
    d.postings_with_description,
    round(100.0 * count(*) / d.postings_with_description, 1) as pct_of_postings,
    d.postings_with_description < 30                    as is_low_sample
from scope_all s
join {{ ref('bridge_job_skills') }} b using (posting_key)
join {{ ref('dim_skill') }} k using (skill_key)
join denominators d using (role_family)
group by s.role_family, b.skill, k.category, d.postings_with_description
