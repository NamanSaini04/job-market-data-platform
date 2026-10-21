select * from {{ ref('mart_skill_demand') }}
where postings_mentioning > postings_with_description or pct_of_postings > 100
