-- fact_posting_daily grain: exactly one row per posting per day.
select posting_key, date_day, count(*)
from {{ ref('fact_posting_daily') }}
group by 1, 2
having count(*) > 1
