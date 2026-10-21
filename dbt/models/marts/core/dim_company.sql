-- One row per normalised company name ("Monzo Bank Ltd" = "monzo bank").
--
-- is_agency: Reed tells us, per advert, whether the poster is the employer or an agency.
-- A company is an agency when most of its Reed adverts are agency adverts. Company boards
-- (Greenhouse/Ashby/Lever) are always the employer itself.
-- is_job_board: job boards that repost other organisations' jobs under their own name
-- (seed job_board_intermediaries, with evidence). Reed calls them "direct", wrongly.
-- Limitation: agency adverts hide the real employer, so agency "hiring" is not employer demand.
with p as (
    select p.company_name_normalized, p.company_name_raw, p.posted_by, p.source, c.is_training_course
    from {{ ref('int_postings') }} p
    join {{ ref('int_posting_classification') }} c using (posting_key)
    where p.company_name_normalized is not null
)
select
    md5(company_name_normalized)                              as company_key,
    company_name_normalized,
    mode() within group (order by company_name_raw)           as company_name,
    count(*)                                                  as postings_total,
    count(*) filter (where posted_by = 'agency')              as agency_postings,
    count(*) filter (where posted_by in ('direct', 'company_board')) as employer_postings,
    -- Agency if most of its Reed adverts are agency adverts, OR its name says so. Name rule
    -- added 21 Oct 2026: "360 Resourcing Solutions" and "RecruitmentRevolution.com" post as
    -- "direct" on Reed and ranked as employers. Reed's flag is the poster's own claim.
    (count(*) filter (where posted_by = 'agency')
        > count(*) filter (where posted_by in ('direct', 'company_board'))
     or company_name_normalized ~ '(recruit|resourcing)')                 as is_agency,
    bool_or(source <> 'reed')                                 as has_company_board,
    bool_or(is_training_course)                               as is_training_provider,
    company_name_normalized in (select advertiser_normalized from {{ ref('job_board_intermediaries') }})
                                                              as is_job_board
from p
group by company_name_normalized
