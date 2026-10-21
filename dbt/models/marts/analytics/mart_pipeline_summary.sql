-- Grain: ONE row. Headline facts for the dashboard, including what was EXCLUDED and why,
-- so every number on the page can be traced back to a rule.
with f as (select * from {{ ref('fact_job_postings') }}),
days as (
    select min(date_day) as first_collection_date,
           max(date_day) as data_as_of,
           count(*)      as days_collected
    from {{ ref('dim_date') }}
),
latest_live as (
    select sum(live_postings) as live_in_scope_latest_day
    from {{ ref('mart_role_demand_daily') }}
    where date_day = (select max(date_day) from {{ ref('mart_role_demand_daily') }})
)
select
    days.first_collection_date,
    days.data_as_of,
    days.days_collected,
    (select count(distinct board) from f where source <> 'reed')               as company_boards_tracked,
    (select count(*) from f)                                                   as postings_tracked_total,
    (select count(*) from f where is_in_market_scope)                          as postings_in_scope,
    latest_live.live_in_scope_latest_day,
    -- Each excluded posting is counted ONCE, under the first reason in this order:
    -- not a tracked role -> not UK -> training course. So excluded_training_courses means
    -- "course adverts that would otherwise have reached the charts".
    (select count(*) from f where role_family is null)                         as excluded_not_tracked_role,
    (select count(*) from f where role_family is not null and not is_uk)       as excluded_not_uk,
    (select count(*) from f where role_family is not null and is_uk and is_training_course)
                                                                               as excluded_training_courses,
    (select count(*) from {{ ref('mart_company_hiring') }})                    as employers_hiring,
    (select round(100.0 * count(salary_mid_gbp) / nullif(count(*), 0), 1)
       from f where is_in_market_scope)                                        as pct_in_scope_with_salary
from days, latest_live
