-- Grain: one row per (posting, skill) where the skill appears in the title or full description.
-- Only in-scope postings with a full description are searched; mart_skill_demand uses that same set
-- as its denominator, so a missing description never counts as "skill not required".
-- Word boundaries: "(^|[^a-z0-9])" + pattern + "([^a-z0-9]|$)" so "R" does not match "spark".
select
    f.posting_key,
    s.skill_key,
    s.skill
from {{ ref('fact_job_postings') }} f
join {{ ref('int_postings') }} p using (posting_key)
join {{ ref('dim_skill') }} s
  on (p.job_title || ' ' || p.description_text) ~* ('(^|[^a-z0-9])(' || s.pattern || ')([^a-z0-9]|$)')
where p.has_full_description
  and f.is_in_market_scope    -- regex over every description is the slowest step; marts only use scope
