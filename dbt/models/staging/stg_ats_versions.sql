-- Company-board job content, one row per content version. Three ATS formats -> one shape.
-- Field names and formats per source: see docs/01 (field availability table).

with p as (
    select * from {{ source('raw', 'job_payload') }}
    where source in ('greenhouse', 'lever', 'ashby')
),

ashby_salary as (
    -- Ashby: first 'Salary' component. Kept with its currency and interval; filtered later.
    select distinct on (p.source, p.board, p.source_job_id, p.payload_hash)
        p.source, p.board, p.source_job_id, p.payload_hash,
        (c->>'minValue')::numeric   as salary_min,
        (c->>'maxValue')::numeric   as salary_max,
        c->>'currencyCode'          as salary_currency,
        c->>'interval'              as salary_interval
    from p, jsonb_array_elements(p.payload->'compensation'->'summaryComponents') c
    where p.source = 'ashby' and c->>'compensationType' = 'Salary'
    order by p.source, p.board, p.source_job_id, p.payload_hash
)

select
    p.source,
    p.board,
    p.source_job_id,
    p.payload_hash,
    p.first_seen_at                                       as version_first_seen_at,

    trim(case p.source when 'lever' then p.payload->>'text' else p.payload->>'title' end)
                                                          as job_title,

    case p.source
        when 'greenhouse' then p.payload->'location'->>'name'
        when 'lever' then concat_ws('; ', p.payload->'categories'->>'location',
            (select string_agg(x, '; ') from jsonb_array_elements_text(p.payload->'categories'->'allLocations') x))
        when 'ashby' then concat_ws('; ', p.payload->>'location',
            (select string_agg(s->>'location', '; ') from jsonb_array_elements(p.payload->'secondaryLocations') s),
            p.payload->'address'->'postalAddress'->>'addressCountry')
    end                                                   as location_text,
    case when p.source = 'lever' then p.payload->>'country' end as country_code,

    case p.source
        when 'greenhouse' then (p.payload->>'first_published')::timestamptz
        when 'lever'      then to_timestamp((p.payload->>'createdAt')::bigint / 1000.0)
        when 'ashby'      then (p.payload->>'publishedAt')::timestamptz
    end::date                                             as posted_date,

    case p.source
        when 'lever' then lower(p.payload->>'workplaceType')
        when 'ashby' then case when (p.payload->>'isRemote')::boolean then 'remote'
                               else lower(p.payload->>'workplaceType') end
    end                                                   as work_type_raw,

    case p.source
        when 'lever' then p.payload->'categories'->>'commitment'
        when 'ashby' then p.payload->>'employmentType'
    end                                                   as employment_type_raw,

    case p.source
        when 'greenhouse' then p.payload->'departments'->0->>'name'
        when 'lever'      then p.payload->'categories'->>'department'
        when 'ashby'      then p.payload->>'department'
    end                                                   as department,

    case p.source
        when 'greenhouse' then {{ html_to_text("p.payload->>'content'") }}
        when 'lever' then {{ html_to_text("concat_ws(' ', p.payload->>'descriptionPlain',
            (select string_agg(l->>'text' || ' ' || coalesce(l->>'content', ''), ' ')
               from jsonb_array_elements(p.payload->'lists') l),
            p.payload->>'additionalPlain')") }}
        when 'ashby' then p.payload->>'descriptionPlain'
    end                                                   as description_text,

    case p.source
        when 'greenhouse' then p.payload->>'absolute_url'
        when 'lever'      then p.payload->>'hostedUrl'
        when 'ashby'      then p.payload->>'jobUrl'
    end                                                   as job_url,

    s.salary_min,
    s.salary_max,
    s.salary_currency,
    s.salary_interval
from p
left join ashby_salary s using (source, board, source_job_id, payload_hash)
