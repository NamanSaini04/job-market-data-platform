-- Agencies, job boards and training providers must never appear as hiring employers.
select h.company_name
from {{ ref('mart_company_hiring') }} h
join {{ ref('dim_company') }} c using (company_key)
where c.is_agency or c.is_job_board or c.is_training_provider
