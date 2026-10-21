"""
The contract between the export script and the dashboard: which files exist and which
columns the page reads. tests/test_dashboard.py checks both sides against this, so a renamed
mart column fails a test instead of breaking the public page.
"""

REQUIRED_COLUMNS = {
    "summary.csv": ["first_collection_date", "data_as_of", "days_collected", "company_boards_tracked",
                    "postings_tracked_total", "postings_in_scope", "live_in_scope_latest_day",
                    "excluded_training_courses", "excluded_not_uk", "excluded_not_tracked_role",
                    "employers_hiring", "pct_in_scope_with_salary"],
    "role_demand_daily.csv": ["date_day", "role_family", "live_postings", "new_postings", "is_source_complete"],
    "skill_demand.csv": ["role_family", "skill", "category", "postings_mentioning",
                         "postings_with_description", "pct_of_postings", "is_low_sample"],
    "salary_by_role.csv": ["role_family", "seniority", "postings_with_salary", "postings_in_scope",
                           "pct_with_salary", "p25_gbp", "median_gbp", "p75_gbp", "is_low_sample"],
    "work_type.csv": ["role_family", "work_type", "postings", "pct_of_role"],
    "company_hiring.csv": ["company_name", "postings_in_scope", "open_postings", "role_family_list",
                           "tracked_via_company_board"],
}


