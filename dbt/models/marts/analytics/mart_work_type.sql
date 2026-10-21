-- Grain: one row per role family per work type. 'unknown' is kept, not hidden: most adverts
-- do not state a work arrangement in a field, and the share of unknown is itself a finding.
select
    role_family,
    work_type,
    count(*)                                                              as postings,
    round(100.0 * count(*) / sum(count(*)) over (partition by role_family), 1) as pct_of_role,
    count(*) filter (where work_type_basis = 'source_field')              as from_source_field
from {{ ref('fact_job_postings') }}
where is_in_market_scope
group by role_family, work_type
