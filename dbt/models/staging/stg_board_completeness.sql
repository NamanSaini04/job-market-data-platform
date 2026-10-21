-- Was each board fully collected on each day?
--   Company boards: one row per board (status from ops.source_run).
--   Reed: only the 'search' summary row counts. It is 'success' only when all searches finished.
-- Several runs on one day (manual re-runs): complete if ANY of them succeeded.
-- Closure logic (int_postings) only trusts complete days.
select
    r.observed_date,
    s.source,
    s.board                         as board_group,
    bool_or(s.status = 'success')   as is_complete
from {{ source('ops', 'source_run') }} s
join {{ source('ops', 'pipeline_run') }} r using (run_id)
where not (s.source = 'reed' and s.board <> 'search')     -- skip per-query and detail rows
group by 1, 2, 3
