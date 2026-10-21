-- Grain: one row per day per role family (in market scope only).
--   live_postings   seen live that day
--   new_postings    first seen that day. ZERO on a source's first collection day by design:
--                   everything is "new" on day one, which would be a fake spike.
--   is_source_complete  false when any board failed or did not run that day; live counts may be low.
with daily as (
    select d.date_day, f.role_family, f.source, d.is_first_seen_day, f.was_live_before_collection_started
    from {{ ref('fact_posting_daily') }} d
    join {{ ref('fact_job_postings') }} f using (posting_key)
    where f.is_in_market_scope
),
-- A day is complete only when EVERY board we collect ran successfully. A board with no
-- run that day counts as incomplete (14 Oct: Reed had not started yet).
expected as (
    select distinct source, board_group from {{ ref('stg_board_completeness') }}
),
completeness as (
    select d.date_day,
           bool_and(coalesce(b.is_complete, false)) as all_boards_complete
    from {{ ref('dim_date') }} d
    cross join expected e
    left join {{ ref('stg_board_completeness') }} b
           on b.observed_date = d.date_day and b.source = e.source and b.board_group = e.board_group
    group by d.date_day
)
select
    daily.date_day,
    daily.role_family,
    count(*)                                                                as live_postings,
    count(*) filter (where is_first_seen_day and not was_live_before_collection_started) as new_postings,
    count(*) filter (where source = 'reed')                                 as live_on_reed,
    count(*) filter (where source <> 'reed')                                as live_on_company_boards,
    coalesce(c.all_boards_complete, false)                                  as is_source_complete
from daily
left join completeness c using (date_day)
group by daily.date_day, daily.role_family, c.all_boards_complete
