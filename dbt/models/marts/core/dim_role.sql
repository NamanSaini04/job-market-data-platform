-- Tracked role families, from config/roles.yaml (via seed role_patterns).
select
    md5(role_family)    as role_key,
    role_family,
    min(priority)       as priority,
    count(*)            as pattern_count
from {{ ref('role_patterns') }}
group by role_family
