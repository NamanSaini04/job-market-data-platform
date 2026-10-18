-- 003: Correct Reed run totals.
--
-- Bug (found 17 Oct 2026 by scripts/health_check.sql): ops.pipeline_run totals for
-- reed_daily runs were the sum of ALL ops.source_run rows, including the 'search'
-- summary row, which itself repeats the sum of the 14 query rows. Totals were doubled.
-- The code is fixed in loader.finish_run. This migration recomputes the stored history.
--
-- Data-only migration: no schema change. Safe to run once (the runner guarantees that).

UPDATE ops.pipeline_run r
   SET records_fetched  = s.fetched,
       records_loaded   = s.loaded,
       records_rejected = s.rejected
  FROM (SELECT run_id,
               coalesce(sum(records_fetched), 0)  AS fetched,
               coalesce(sum(records_loaded), 0)   AS loaded,
               coalesce(sum(records_rejected), 0) AS rejected
          FROM ops.source_run
         WHERE NOT (source = 'reed' AND board = 'search')
         GROUP BY run_id) s
 WHERE r.run_id = s.run_id
   AND r.pipeline = 'reed_daily';
