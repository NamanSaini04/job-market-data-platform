-- One row per posting (source + board + job id): its life, latest content, and normalised fields.
--
-- Lifecycle (first/last seen, closure, edits) comes from int_posting_history.
-- The status rule is explained there.

with hits as (
    select source_job_id,
           bool_or(posted_by = 'direct') as any_direct,
           bool_or(posted_by = 'agency') as any_agency
    from {{ ref('stg_reed_search_hits') }}
    group by 1
),

base as (
    select
        h.*,
        v.job_title, v.company_name_raw, v.location_text, v.country_code, v.posted_date,
        v.work_type_raw, v.employment_type_raw, v.department,
        v.description_text as version_description, v.job_url,
        v.salary_min, v.salary_max, v.salary_currency, v.salary_interval
    from {{ ref('int_posting_history') }} h
    join {{ ref('int_job_versions') }} v on v.version_key = h.latest_version_key
),

joined as (
    select
        b.*,
        d.description_text   as detail_description,
        d.salary_type        as reed_salary_type,
        d.salary_currency    as reed_salary_currency,
        coalesce(d.yearly_salary_min, d.salary_min) as reed_annual_min,
        coalesce(d.yearly_salary_max, d.salary_max) as reed_annual_max,
        d.salary_min         as reed_rate_min,
        d.salary_max         as reed_rate_max,
        d.contract_type      as reed_contract_type,
        d.is_part_time       as reed_is_part_time,
        d.source_job_id is not null as has_reed_detail,
        h.any_direct, h.any_agency
    from base b
    left join {{ ref('stg_reed_details') }} d
           on b.source = 'reed' and d.source_job_id = b.source_job_id
    left join hits h
           on b.source = 'reed' and h.source_job_id = b.source_job_id
),

normalised as (
    select
        *,
        case when source = 'reed' then detail_description else version_description end
            as description_text,

        -- annual salary in GBP, only when the source STATES it (never Reed search, never predicted)
        case
            when source = 'reed' and reed_salary_type = 'per annum' and reed_salary_currency = 'GBP'
                then reed_annual_min
            when source <> 'reed' and salary_currency = 'GBP' and salary_interval = '1 YEAR'
                then salary_min
        end as raw_annual_min,
        case
            when source = 'reed' and reed_salary_type = 'per annum' and reed_salary_currency = 'GBP'
                then reed_annual_max
            when source <> 'reed' and salary_currency = 'GBP' and salary_interval = '1 YEAR'
                then salary_max
        end as raw_annual_max
    from joined
)

select
    posting_key,
    source,
    board,
    source_job_id,
    job_title,
    company_name_raw,
    {{ normalize_company('company_name_raw') }}            as company_name_normalized,
    case
        when source <> 'reed' then 'company_board'
        when any_direct then 'direct'
        when any_agency then 'agency'
        else 'unknown'
    end                                                     as posted_by,
    location_text,
    country_code,
    department,
    job_url,

    -- work arrangement: the field when the source has one, otherwise clear phrases only
    case
        when work_type_raw = 'hybrid' then 'hybrid'
        when work_type_raw = 'remote' then 'remote'
        when work_type_raw in ('onsite', 'on-site', 'on site') then 'on_site'
        when concat_ws(' ', job_title, location_text) ~* '\mremote\M' then 'remote'
        when concat_ws(' ', job_title, location_text) ~* '\mhybrid\M' then 'hybrid'
        when description_text ~* '(hybrid working|hybrid role|hybrid model|hybrid pattern|days? (a|per) week in (the|our) office)' then 'hybrid'
        when description_text ~* '(fully remote|remote[- ]first|100% remote|work from home)' then 'remote'
        else 'unknown'
    end                                                     as work_type,
    case
        when work_type_raw in ('hybrid', 'remote', 'onsite', 'on-site', 'on site') then 'source_field'
        else 'inferred_from_text'
    end                                                     as work_type_basis,

    case
        when source = 'reed' then case
            when reed_contract_type = 'Permanent' and reed_is_part_time then 'part_time'
            when reed_contract_type = 'Permanent'  then 'permanent'
            when reed_contract_type = 'Contract'   then 'contract'
            when reed_contract_type = 'Temporary'  then 'temporary'
            else 'unknown' end
        when employment_type_raw ~* 'intern'        then 'internship'
        when employment_type_raw ~* 'contract'      then 'contract'
        when employment_type_raw ~* 'temp|short term' then 'temporary'
        when employment_type_raw ~* 'part'          then 'part_time'
        when employment_type_raw ~* 'perm|full'     then 'permanent'
        else 'unknown'
    end                                                     as employment_type,

    -- salary: stated, GBP, annual, and plausible (10k-500k). Anything else -> not used.
    case when raw_annual_min between 10000 and 500000
          and coalesce(raw_annual_max, raw_annual_min) between raw_annual_min and 500000
         then raw_annual_min end                            as salary_min_gbp,
    case when raw_annual_min between 10000 and 500000
          and coalesce(raw_annual_max, raw_annual_min) between raw_annual_min and 500000
         then coalesce(raw_annual_max, raw_annual_min) end  as salary_max_gbp,
    case when source = 'reed' and reed_salary_type = 'per day' and reed_salary_currency = 'GBP'
         then reed_rate_min end                             as day_rate_min_gbp,
    case when source = 'reed' and reed_salary_type = 'per day' and reed_salary_currency = 'GBP'
         then reed_rate_max end                             as day_rate_max_gbp,

    description_text,
    description_text is not null                            as has_full_description,

    posted_date,
    first_seen_date,
    last_seen_date,
    days_seen,
    first_seen_date = source_first_date                     as was_live_before_collection_started,
    latest_complete_date,
    complete_days_missed,
    case
        when last_seen_date >= coalesce(latest_complete_date, last_seen_date) then 'open'
        when complete_days_missed >= 2 then 'closed'
        else 'unknown'
    end                                                     as status,
    content_edits
from normalised
