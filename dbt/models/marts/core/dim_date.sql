-- One row per calendar day from the first collection day to the latest.
with bounds as (
    select min(observed_date) as d0, max(observed_date) as d1
    from {{ ref('stg_observations') }}
)
select
    d::date                                 as date_day,
    extract(isodow from d)::int             as iso_day_of_week,
    to_char(d, 'Dy')                        as day_name,
    extract(isodow from d) in (6, 7)        as is_weekend,
    date_trunc('week', d)::date             as week_start,
    date_trunc('month', d)::date            as month_start
from bounds, generate_series(d0, d1, interval '1 day') as d
