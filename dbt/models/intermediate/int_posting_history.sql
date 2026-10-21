-- One row per posting: when it was seen, which version is the latest, how often its
-- content really changed, and how many FULLY collected days it has been missing.
--
-- Why a separate table (not CTEs inside int_postings): Postgres guesses row counts badly
-- for GROUP BY on three text keys (it guessed 40 rows, got 11,070) and picked a nested
-- loop that took 55-73s on 5 days of data. As a real table with fresh statistics
-- (the post_hook ANALYZE) the planner sees the true size and uses hash joins.
--
-- CLOSURE RULE (the reason ops.source_run exists):
--   open     seen on or after the latest day its board was fully collected
--   closed   missing on 2+ days when its board WAS fully collected
--   unknown  anything else (missed 1 day, or its board failed on the days it was missing)
--   Why 2 days: Reed results shift while paging, so a live job can be missed once (docs/03).
{{ config(post_hook=[
    "create unique index if not exists {{ this.name }}_key_idx on {{ this }} (posting_key)",
    "analyze {{ this }}"
]) }}

with obs as (
    select source, board, source_job_id,
           min(observed_date) as first_seen_date,
           max(observed_date) as last_seen_date,
           count(*)           as days_seen,
           (array_agg(payload_hash order by observed_date desc))[1] as latest_payload_hash
    from {{ ref('stg_observations') }}
    group by 1, 2, 3
),

edits as (
    -- content versions actually observed, minus one = number of real content edits
    select o.source, o.board, o.source_job_id,
           count(distinct v.content_signature) - 1 as content_edits
    from (select distinct source, board, source_job_id,
                 md5(source || '|' || board || '|' || source_job_id || '|' || payload_hash) as version_key
          from {{ ref('stg_observations') }}) o
    join {{ ref('int_job_versions') }} v on v.version_key = o.version_key
    group by 1, 2, 3
),

complete_days as (
    select source, board_group, observed_date
    from {{ ref('stg_board_completeness') }}
    where is_complete
),

with_group as (
    select o.*,
           case when o.source = 'reed' then 'search' else o.board end as board_group
    from obs o
),

closure as (
    select g.source, g.board, g.source_job_id,
           max(c.observed_date)                                                     as latest_complete_date,
           count(c.observed_date) filter (where c.observed_date > g.last_seen_date) as complete_days_missed
    from with_group g
    left join complete_days c on c.source = g.source and c.board_group = g.board_group
    group by 1, 2, 3
)

select
    md5(g.source || '|' || g.board || '|' || g.source_job_id) as posting_key,
    md5(g.source || '|' || g.board || '|' || g.source_job_id || '|' || g.latest_payload_hash) as latest_version_key,
    g.source, g.board, g.source_job_id, g.board_group,
    g.first_seen_date, g.last_seen_date, g.days_seen, g.latest_payload_hash,
    min(g.first_seen_date) over (partition by g.source) as source_first_date,
    cl.latest_complete_date,
    cl.complete_days_missed,
    coalesce(e.content_edits, 0) as content_edits
from with_group g
join closure cl using (source, board, source_job_id)
left join edits e using (source, board, source_job_id)
