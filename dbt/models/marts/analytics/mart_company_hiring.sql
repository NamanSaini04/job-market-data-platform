-- Grain: one row per EMPLOYER (agencies and training providers excluded: agency adverts hide
-- the real employer, so counting them would credit recruiters with demand).
select
    co.company_key,
    co.company_name,
    count(*)                                                  as postings_in_scope,
    count(*) filter (where f.status = 'open')                 as open_postings,
    count(*) filter (where f.status = 'closed')               as closed_postings,
    count(distinct f.role_family)                             as role_families,
    string_agg(distinct f.role_family, ', ' order by f.role_family) as role_family_list,
    count(*) filter (where f.seniority = 'senior')            as senior_postings,
    min(f.first_seen_date)                                    as first_seen_date,
    max(f.last_seen_date)                                     as last_seen_date,
    bool_or(f.source <> 'reed')                               as tracked_via_company_board
from {{ ref('fact_job_postings') }} f
join {{ ref('dim_company') }} co using (company_key)
where f.is_in_market_scope
  and not co.is_agency
  and not co.is_training_provider
group by co.company_key, co.company_name
