-- Pipeline health check. Read-only: changes nothing.
-- Run:  docker compose exec -T db psql -U jobs -d jobmarket < scripts/health_check.sql
--
-- Note: section 4 copies the title patterns from config/roles.yaml.
-- If you change roles.yaml, update the list here too.

\pset footer off
\echo '\n=== 1. Runs, last 7 days ==='
SELECT pipeline, observed_date, status, boards_failed AS failed,
       records_fetched AS fetched, payload_versions_new AS new_or_changed,
       to_char(finished_at - started_at, 'MI:SS') AS took
FROM ops.pipeline_run
WHERE observed_date >= current_date - 7
ORDER BY started_at;

\echo '\n=== 2. Days with complete data (closure logic will only trust these) ==='
SELECT r.observed_date,
       bool_and(s.status = 'success') FILTER (WHERE r.pipeline = 'ats_daily')        AS boards_all_ok,
       bool_and(s.status = 'success') FILTER (WHERE s.board = 'search')              AS reed_search_complete
FROM ops.pipeline_run r JOIN ops.source_run s USING (run_id)
GROUP BY r.observed_date ORDER BY r.observed_date;

\echo '\n=== 3. Reed: direct vs agency (latest day) ==='
SELECT posted_by, count(DISTINCT source_job_id) AS jobs,
       round(100.0 * count(DISTINCT source_job_id)
             / sum(count(DISTINCT source_job_id)) OVER (), 1) AS pct
FROM raw.reed_search_hit
WHERE observed_date = (SELECT max(observed_date) FROM raw.reed_search_hit)
GROUP BY posted_by;

\echo '\n=== 4. Reed: most common titles the role filter REJECTS (latest day) ==='
\echo 'Look for real data roles in this list. If you see them, the filter is too strict.'
SELECT p.payload->>'jobTitle' AS title, count(*) AS jobs
FROM raw.job_observation o
JOIN raw.job_payload p USING (source, board, source_job_id, payload_hash)
WHERE o.source = 'reed' AND o.board = 'search'
  AND o.observed_date = (SELECT max(observed_date) FROM raw.job_observation WHERE source = 'reed')
  AND NOT lower(p.payload->>'jobTitle') LIKE ANY (ARRAY[
      '%analytics engineer%', '%machine learning engineer%', '%ml engineer%', '%mlops%',
      '%ml ops%', '%ai engineer%', '%llm engineer%', '%genai engineer%', '%data engineer%',
      '%data platform engineer%', '%etl developer%', '%data scientist%',
      '%machine learning scientist%', '%ml scientist%', '%data analyst%', '%bi analyst%',
      '%business intelligence analyst%', '%insight analyst%', '%insights analyst%',
      '%analytics analyst%', '%reporting analyst%', '%bi developer%',
      '%business intelligence developer%', '%power bi%', '%data architect%', '%head of data%',
      '%director of data%', '%chief data officer%', '%data lead%', '%head of analytics%',
      '%analytics manager%', '%analytics lead%'])
GROUP BY 1 ORDER BY 2 DESC LIMIT 30;

\echo '\n=== 5. Content changes on the latest day: new jobs vs real edits, per board ==='
\echo 'A board where most live jobs are "edited" every day probably has a volatile field.'
WITH latest AS (SELECT source, max(observed_date) AS d FROM raw.job_observation GROUP BY source),
today AS (
    SELECT o.* FROM raw.job_observation o JOIN latest l
      ON l.source = o.source AND o.observed_date = l.d),
prev AS (
    SELECT DISTINCT ON (o.source, o.board, o.source_job_id)
           o.source, o.board, o.source_job_id, o.payload_hash
    FROM raw.job_observation o JOIN latest l
      ON l.source = o.source AND o.observed_date < l.d
    ORDER BY o.source, o.board, o.source_job_id, o.observed_date DESC)
SELECT t.source, t.board, count(*) AS live,
       count(*) FILTER (WHERE p.source_job_id IS NULL)          AS new_jobs,
       count(*) FILTER (WHERE p.payload_hash <> t.payload_hash) AS edited,
       round(100.0 * count(*) FILTER (WHERE p.payload_hash <> t.payload_hash) / count(*), 1) AS edited_pct
FROM today t LEFT JOIN prev p USING (source, board, source_job_id)
GROUP BY 1, 2
HAVING count(*) FILTER (WHERE p.payload_hash <> t.payload_hash) > 0
ORDER BY edited DESC LIMIT 15;

\echo '\n=== 6. Reed detail coverage (title-matched jobs live on the latest day) ==='
SELECT count(*) AS matched_jobs,
       count(*) FILTER (WHERE EXISTS (
           SELECT 1 FROM raw.job_payload d
           WHERE d.source = 'reed' AND d.board = 'detail' AND d.source_job_id = o.source_job_id))
         AS with_detail
FROM raw.job_observation o
JOIN raw.job_payload p USING (source, board, source_job_id, payload_hash)
WHERE o.source = 'reed' AND o.board = 'search'
  AND o.observed_date = (SELECT max(observed_date) FROM raw.job_observation WHERE source = 'reed')
  AND lower(p.payload->>'jobTitle') LIKE ANY (ARRAY[
      '%analytics engineer%', '%machine learning engineer%', '%ml engineer%', '%mlops%',
      '%ml ops%', '%ai engineer%', '%llm engineer%', '%genai engineer%', '%data engineer%',
      '%data platform engineer%', '%etl developer%', '%data scientist%',
      '%machine learning scientist%', '%ml scientist%', '%data analyst%', '%bi analyst%',
      '%business intelligence analyst%', '%insight analyst%', '%insights analyst%',
      '%analytics analyst%', '%reporting analyst%', '%bi developer%',
      '%business intelligence developer%', '%power bi%', '%data architect%', '%head of data%',
      '%director of data%', '%chief data officer%', '%data lead%', '%head of analytics%',
      '%analytics manager%', '%analytics lead%']);
