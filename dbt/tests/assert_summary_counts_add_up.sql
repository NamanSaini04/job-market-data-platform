-- Every tracked posting is either in scope or excluded for exactly one stated reason,
-- and the summary is exactly one row. If this fails, the dashboard's "what we excluded"
-- panel would not add up.
with s as (select * from {{ ref('mart_pipeline_summary') }})
select * from s
where postings_in_scope + excluded_training_courses + excluded_not_uk + excluded_not_tracked_role
      <> postings_tracked_total
   or (select count(*) from {{ ref('mart_pipeline_summary') }}) <> 1
