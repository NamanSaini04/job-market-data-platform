-- Role family, seniority and UK flag per posting.
--
-- Role family: first matching regex from config/roles.yaml (via seed role_patterns),
-- lowest priority number wins. NULL = not a tracked data role.
-- Seniority: keywords in the title. Crude but transparent; "unspecified" when no keyword.
--
-- Training-course adverts (found 18 Oct 2026: 343 of 363 "junior" data postings were paid
-- courses, e.g. ITOL: "Please note this is a training course and fees apply"):
--   rule   description has a course signal AND a payment/guarantee signal (both needed, so
--          genuine employer traineeships without fees are not flagged)
--   list   seed training_advertisers, for adverts without a full description
-- Flagged postings stay in the data but are excluded from every mart (in_market_scope).

with roles as (
    select distinct on (p.posting_key)
        p.posting_key, r.role_family, r.priority as role_priority
    from {{ ref('int_postings') }} p
    join {{ ref('role_patterns') }} r
      on lower(p.job_title) ~ r.pattern          -- regex; same rule as ingestion/roles.py
    order by p.posting_key, r.priority
)

select
    p.posting_key,
    r.role_family,
    r.role_priority,
    case
        when p.job_title ~* '\m(intern|internship|apprentice|apprenticeship|trainee|graduate|grad|entry[- ]level|junior|jnr)\M'
            then 'junior'
        when p.job_title ~* '\m(head|director|chief|vp|vice president)\M'
            then 'leadership'
        when p.job_title ~* '\m(senior|snr|sr|lead|principal|staff|manager)\M'
            then 'senior'
        else 'unspecified'
    end as seniority,
    (p.source = 'reed'
        or p.country_code = 'GB'
        or coalesce(p.location_text, '') ~* {{ uk_location_regex() }}) as is_uk,
    case
        when p.source = 'reed' then initcap(trim(p.location_text))
        else initcap(substring(p.location_text from '(?i)(london|manchester|edinburgh|glasgow|bristol|leeds|cardiff|birmingham|cambridge|oxford|belfast|newcastle|nottingham|sheffield|liverpool|reading|brighton)'))
    end as city,
    (t.advertiser_normalized is not null
        or (p.description_text ~* '(training course|traineeship|self[- ]paced|online course|course fee)'
            and p.description_text ~* '(fees? apply|money back|job guarantee|guaranteed job|refund|pay(ment)? (plan|options)|finance options)'))
        as is_training_course,
    case
        when t.advertiser_normalized is not null then 'advertiser_list'
        when p.description_text ~* '(training course|traineeship|self[- ]paced|online course|course fee)'
         and p.description_text ~* '(fees? apply|money back|job guarantee|guaranteed job|refund|pay(ment)? (plan|options)|finance options)'
            then 'description_rule'
    end as training_flag_basis
from {{ ref('int_postings') }} p
left join roles r using (posting_key)
left join {{ ref('training_advertisers') }} t on t.advertiser_normalized = p.company_name_normalized
