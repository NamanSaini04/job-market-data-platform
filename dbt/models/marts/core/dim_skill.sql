-- Skills dictionary (seed skills.csv). pattern is a regex core; bridge_job_skills wraps it in
-- word boundaries so "r" does not match inside "spark".
select
    md5(skill)  as skill_key,
    skill,
    category,
    pattern
from {{ ref('skills') }}
