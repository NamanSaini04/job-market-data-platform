# Phase 5: Public dashboard

Status: built 21 Oct 2026. Streamlit app reading exported CSVs; hosted on Streamlit Community Cloud.

---

## 1. What this phase builds

```
dbt marts (Postgres, on the laptop)
   |  scripts/export_dashboard_data.py   (daily, only after a fully passing dbt build)
   v
dashboard/data/*.csv     6 small files, aggregates only (~40 KB)
   |  git push (manual, from PyCharm)
   v
GitHub  ->  Streamlit Community Cloud  ->  public link
             runs dashboard/app.py
```

| File | Purpose |
|---|---|
| `dashboard/app.py` | The page: 4 headline numbers, 6 tabs (Demand, Skills, Salaries, Work arrangement, Employers, Method) |
| `dashboard/contract.py` | Which files and columns the page reads. Both the export and the page are tested against it |
| `dashboard/requirements.txt` | What the host installs (pinned) |
| `scripts/export_dashboard_data.py` | Marts -> CSV. Explicit column lists, written to `.tmp` then renamed |
| `dbt/.../mart_pipeline_summary.sql` | One row of headline facts and exclusion counts |

## 2. Why it is built this way

### Files, not a live database connection
- The database runs in Docker on a laptop. A public website cannot reach it, and should not.
- A hosted database would mean a new account and a password stored on a hosting site.
- The marts are tiny. CSVs in git are free, readable on GitHub, and every change shows in a diff.
- Trade-off: the page is only as fresh as the last push. The header shows "Data as of",
  so staleness is visible, never hidden.

### Aggregates only
Reed's developer pages state no display terms (checked 21 Oct 2026). Publishing their
listings could breach terms we have not seen, so the page shows counts, shares and salary
percentiles. No advert text, no job links, no individual postings. A test fails if a
column like `description`, `job_title` or `*_url` is ever exported.

### No business logic in the page
Every rule (scope, closure, salary, exclusions) lives in dbt, where it is tested. The page
only filters and draws. So the page can never disagree with the tested models.

### Export only after a passing build
If any dbt test fails, the export is skipped and the dashboard keeps yesterday's checked
numbers. Stale but correct beats fresh but wrong.

### Chart choices
- One colour for single-series bars; fixed colour order for multi-series charts.
- Line chart limited to 3 roles at once, and only days when every source was fully collected:
  a failed source would otherwise look like a fall in demand.
- Salaries as a range (25th to 75th percentile) with the median, sample size in the label,
  hollow dot when fewer than 20 salaries.
- "Not stated" work arrangement is shown, not hidden: most adverts never say.
- A "short history" banner shows until 28 days of data exist.

## 3. Findings while building this phase (each fixed and tested)

### 3.1 NULL flags silently dropped 150 real jobs (Phase 4 bug)
`is_training_course` was `advertiser_on_list OR (description matches ...)`. With no
description, that is `false OR NULL` = **NULL**, and a NULL flag fails every filter. 150 UK
data-role postings (Reed jobs waiting for their detail call) were missing from all marts.
`is_uk` had the same problem for company-board jobs with no location data.
- Found by a new test: in-scope + exclusions must equal all postings. It was off by 8,740.
- Fix: `coalesce(..., false)`, plus `not_null` tests on every flag in the fact table.
- Lesson: in SQL, a boolean built from nullable inputs needs an explicit default.

### 3.2 Newto Training and Qualify Nation: course advertisers
Fixing 3.1 surfaced Newto's adverts (they have no descriptions). Their titles carry the
course signals themselves: "Trainee Data Analyst - job guarantee", "Data Scientist No
experience necessary - job guarantee". Qualify Nation: 156 adverts, all "Trainee <role> |
No experience needed". Both added to the training list with that evidence. Without Newto,
junior data roles would have roughly doubled.

### 3.3 Intermediaries counted as employers
- eFinancialCareers is a job board. Reed tags its 487 adverts "direct", but the text is
  written by others ("McCabe & Barton is supporting...", Citi, "Our client..."). New seed
  `job_board_intermediaries.csv` with that evidence.
- Some recruiters also post as "direct" (360 Resourcing Solutions, RecruitmentRevolution.com).
  Rule added: a name containing "recruit" or "resourcing" is an agency, whatever Reed says.
- Test: no agency, job board or training provider may appear in `mart_company_hiring`
  (mutation-checked: removing the job-board filter makes it fail).

### 3.4 Exclusion counts must not double count
The first version counted training courses that were never data roles, so the headline
number grew when an unrelated advertiser was added. Now each posting is counted once, under
its first reason: not a tracked role -> not UK -> training course. The headline therefore
means "course adverts that would otherwise have reached the charts" (354 on 18 Oct).

### 3.5 Dashboard bugs caught by tests, not users
- Empty skills data crashed the Skills tab -> found by the thin-data test.
- `st.cache_data` on a function with no arguments kept serving the first data it read
  -> found when tests ran in sequence. Cache key is now the data folder plus file timestamps.
- `.gitignore` had `data/`, which also matches `dashboard/data/`: the files would never reach
  GitHub and the public page would crash. Now `/data/`, with a test.
- Dates shifted half a day in the browser (timezone). Plotted as UTC calendar days.

## 4. Tests
| Test | Catches |
|---|---|
| export files == contract files; export SQL selects every contract column | a renamed mart column breaking the page |
| published CSV headers contain the contract columns | stale or hand-edited files |
| no forbidden column (`description`, `job_title`, `*_url`, keys); no `select *` | publishing something private by accident |
| summary is 1 row and exclusions add up | numbers on the page that don't reconcile |
| AppTest renders all 6 tabs; every role in the Skills picker | crashes on the live page |
| thin data (1 role, no skills, no employers, low samples) | a fresh deployment crashing |
| `.gitignore` keeps `dashboard/data` | the page deploying without data |
| dbt: `not_null` on `is_uk`, `is_training_course`, `is_in_market_scope`; no intermediary in employer ranking; summary adds up | 3.1, 3.3, 3.4 coming back |

Totals: 94 Python tests, 53 dbt tests.

## 5. Run, verify, deploy

Local:
```bash
python scripts/export_dashboard_data.py
streamlit run dashboard/app.py          # opens http://localhost:8501
```

Deploy once (Streamlit Community Cloud, free):
1. share.streamlit.io -> sign in with GitHub.
2. Create app -> repository `NamanSaini04/job-market-data-platform`, branch `main`,
   main file `dashboard/app.py`. Advanced settings: Python 3.12.
3. Deploy. Pushes to `main` redeploy automatically.

Update the public data (weekly is enough while history is short):
```bash
git add dashboard/data && git commit -m "Dashboard data as of <date>"
```
then push from PyCharm.

## 6. Known limitations
- **Freshness depends on a manual push.** Automating it needs GitHub credentials stored on
  the laptop for the scheduled job; deliberately not done yet.
- **Short history.** 5-7 days: snapshot, not trends. The page says so.
- **Company boards are complete, Reed is a sample.** The 19 tracked employers can rank above
  employers seen only through Reed search. Stated under the Employers chart.
- **New role families have thin data.** BI Developer and Data Architect: no salaries yet,
  and work arrangement mostly "not stated" until their Reed detail calls catch up.
- **Name rule for agencies is a heuristic.** A real employer with "recruit" in its name
  would be excluded. None seen so far.

## 7. Completion criteria
- [x] Page renders every tab on real data and on thin data (tested)
- [x] Every number traces to a tested dbt mart; exclusions reconcile to the total
- [x] Nothing private or per-advert is published (tested)
- [x] Daily run writes the files only after a passing build
- [ ] User: deploy on Streamlit Community Cloud, link opens on a phone
- [ ] User: add the public link to the README
