"""
UK Data & AI Job Market: public dashboard (Phase 5).

Reads ONLY the CSVs in dashboard/data/ (written by scripts/export_dashboard_data.py from
the dbt marts). No database connection, no secrets, no business logic: every rule lives
in dbt, so the page can never disagree with the tested models.

Run locally:  streamlit run dashboard/app.py
"""

import os
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
from contract import REQUIRED_COLUMNS

# DASHBOARD_DATA_DIR lets the tests run the page on small hand-made data.
DATA = Path(os.getenv("DASHBOARD_DATA_DIR", Path(__file__).parent / "data"))
REPO = "https://github.com/NamanSaini04/job-market-data-platform"
MIN_DAYS_FOR_TRENDS = 28

# Categorical slots (validated reference palette, fixed order, never cycled).
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
GRAY = "#9a988f"    # "unknown" is not a category, so it gets a neutral, not a hue
WORK_TYPE_COLORS = {"hybrid": BLUE, "remote": AQUA, "on_site": ORANGE, "unknown": GRAY}
WORK_TYPE_LABELS = {"hybrid": "Hybrid", "remote": "Remote", "on_site": "On-site", "unknown": "Not stated"}

@st.cache_data
def load(data_dir: str, stamp: tuple) -> dict[str, pd.DataFrame]:
    # data_dir and the files' modified times are the cache key: new data = new key. With no
    # arguments, the cache would keep serving the first data it ever read.
    return {name: pd.read_csv(Path(data_dir) / name) for name in REQUIRED_COLUMNS}


def gbp(x: float) -> str:
    return f"£{x / 1000:.0f}k"


st.set_page_config(page_title="UK Data & AI Job Market", page_icon="📊", layout="wide")
d = load(str(DATA), tuple((DATA / n).stat().st_mtime for n in REQUIRED_COLUMNS))
s = d["summary.csv"].iloc[0]

# ---------- header ----------
st.title("UK Data & AI Job Market")
st.markdown(
    f"Data and AI job adverts collected **every day** from the Reed API and "
    f"{s.company_boards_tracked} company job boards, modelled with dbt on PostgreSQL. "
    f"Data as of **{pd.to_datetime(s.data_as_of):%d %b %Y}** · {s.days_collected} days collected · "
    f"[source code and method]({REPO})."
)
if s.days_collected < MIN_DAYS_FOR_TRENDS:
    st.info(
        f"Only {s.days_collected} days of history so far. Counts and shares below are a snapshot; "
        f"day-to-day trends are not meaningful until about {MIN_DAYS_FOR_TRENDS} days.",
        icon="ℹ️",
    )

k1, k2, k3, k4 = st.columns(4)
k1.metric("Live postings (latest day)", f"{s.live_in_scope_latest_day:,}",
          help="UK postings in a tracked data/AI role family, seen on the latest collection day.")
k2.metric("Employers hiring", f"{s.employers_hiring:,}",
          help="Direct employers only. Recruitment agencies, job boards and training providers are excluded.")
k3.metric("Adverts stating a salary", f"{s.pct_in_scope_with_salary:.0f}%",
          help="Stated annual GBP salary. Estimated or unlabelled salaries are never used.")
k4.metric("Training-course ads removed", f"{s.excluded_training_courses:,}",
          help="Paid courses advertised as UK data jobs that would otherwise be counted. See the Method tab.")

demand, skills, salaries, work, employers, method = st.tabs(
    ["Demand", "Skills", "Salaries", "Work arrangement", "Employers", "Method"])

# ---------- demand ----------
with demand:
    rd = d["role_demand_daily.csv"]
    latest = rd[rd.date_day == rd.date_day.max()].sort_values("live_postings", ascending=False)
    st.subheader("Live postings by role")
    st.caption(f"UK postings seen on {pd.to_datetime(rd.date_day.max()):%d %b %Y}.")
    bars = alt.Chart(latest).mark_bar(color=BLUE, cornerRadiusEnd=4).encode(
        x=alt.X("live_postings:Q", title="Live postings", axis=alt.Axis(tickCount=6)),
        y=alt.Y("role_family:N", sort="-x", title=None),
        tooltip=[alt.Tooltip("role_family:N", title="Role"),
                 alt.Tooltip("live_postings:Q", title="Live postings"),
                 alt.Tooltip("new_postings:Q", title="New that day")],
    )
    labels = bars.mark_text(align="left", dx=4).encode(text="live_postings:Q")
    st.altair_chart((bars + labels).properties(height=320), width="stretch")

    st.subheader("Live postings per day")
    complete = rd[rd.is_source_complete.astype(str).str.lower() == "true"]
    roles_by_size = latest.role_family.tolist()
    picked = st.multiselect("Roles (up to 3)", roles_by_size, default=roles_by_size[:3], max_selections=3)
    st.caption("Only days when every source was fully collected. A day with a failed source would "
               "look like a drop in demand.")
    if picked:
        trend = complete[complete.role_family.isin(picked)]
        palette = [BLUE, ORANGE, AQUA][: len(picked)]
        line = alt.Chart(trend).mark_line(point=alt.OverlayMarkDef(size=60), strokeWidth=2).encode(
            # utc: dates are calendar days; without it the browser's timezone shifts them
            x=alt.X("utcyearmonthdate(date_day):T", title=None,
                    axis=alt.Axis(format="%d %b", tickCount="day")),
            y=alt.Y("live_postings:Q", title="Live postings"),
            color=alt.Color("role_family:N", title="Role",
                            scale=alt.Scale(domain=picked, range=palette)),
            tooltip=[alt.Tooltip("utcyearmonthdate(date_day):T", title="Date", format="%d %b %Y"),
                     alt.Tooltip("role_family:N", title="Role"),
                     alt.Tooltip("live_postings:Q", title="Live"),
                     alt.Tooltip("new_postings:Q", title="New")],
        )
        st.altair_chart(line.properties(height=300), width="stretch")

# ---------- skills ----------
with skills:
    sk = d["skill_demand.csv"]
    roles = ["All data roles"] + sorted(r for r in sk.role_family.unique() if r != "All data roles")
    role = st.selectbox("Role", roles)
    sel = sk[sk.role_family == role].sort_values("pct_of_postings", ascending=False).head(15)
    n = int(sel.postings_with_description.iloc[0]) if len(sel) else 0
    st.subheader(f"Most requested skills: {role}")
    if sel.empty:
        st.info("No adverts with a full description for this role yet.", icon="ℹ️")
    st.caption(f"Share of {n:,} adverts with a full description that mention each skill "
               f"(title or description, whole-word match).")
    if len(sel) and bool(sel.is_low_sample.iloc[0]):
        st.warning(f"Small sample ({n} adverts). Treat these shares as rough.", icon="⚠️")
    sbars = alt.Chart(sel).mark_bar(color=BLUE, cornerRadiusEnd=4).encode(
        x=alt.X("pct_of_postings:Q", title="% of adverts", scale=alt.Scale(domain=[0, 100])),
        y=alt.Y("skill:N", sort="-x", title=None),
        tooltip=[alt.Tooltip("skill:N", title="Skill"), alt.Tooltip("category:N", title="Category"),
                 alt.Tooltip("pct_of_postings:Q", title="% of adverts"),
                 alt.Tooltip("postings_mentioning:Q", title="Adverts mentioning")],
    )
    slabels = sbars.mark_text(align="left", dx=4).encode(text=alt.Text("pct_of_postings:Q", format=".0f"))
    st.altair_chart((sbars + slabels).properties(height=420), width="stretch")

# ---------- salaries ----------
with salaries:
    sal = d["salary_by_role.csv"]
    allsen = sal[sal.seniority == "all"].sort_values("median_gbp", ascending=False).copy()
    allsen["label"] = allsen.apply(
        lambda r: f"{r.role_family}  (n={r.postings_with_salary}{', low sample' if r.is_low_sample else ''})",
        axis=1)
    st.subheader("Advertised annual salary by role")
    st.caption("Line = middle 50% of adverts (25th to 75th percentile); dot = median. Midpoint of each "
               "advert's stated range, GBP per year, stated salaries only. Hollow dot = fewer than 20 "
               "salaries.")
    order = allsen.label.tolist()
    base = alt.Chart(allsen).encode(y=alt.Y("label:N", sort=order, title=None,
                                             axis=alt.Axis(labelLimit=320)))
    rng = base.mark_rule(color=BLUE, strokeWidth=2).encode(
        x=alt.X("p25_gbp:Q", title="GBP per year", scale=alt.Scale(zero=False, nice=True, padding=16),
                axis=alt.Axis(format="~s", tickCount=8)), x2="p75_gbp:Q")
    tip = [alt.Tooltip("role_family:N", title="Role"),
           alt.Tooltip("median_gbp:Q", title="Median", format=",.0f"),
           alt.Tooltip("p25_gbp:Q", title="25th pct", format=",.0f"),
           alt.Tooltip("p75_gbp:Q", title="75th pct", format=",.0f"),
           alt.Tooltip("postings_with_salary:Q", title="Salaries"),
           alt.Tooltip("pct_with_salary:Q", title="% of adverts with salary")]
    solid = base.transform_filter("datum.is_low_sample == false").mark_point(
        filled=True, size=110, color=BLUE).encode(x="median_gbp:Q", tooltip=tip)
    hollow = base.transform_filter("datum.is_low_sample == true").mark_point(
        filled=False, size=110, color=BLUE, strokeWidth=2).encode(x="median_gbp:Q", tooltip=tip)
    st.altair_chart((rng + solid + hollow).properties(height=60 + 40 * len(allsen)),
                    width="stretch")

    with st.expander("Table: by role and seniority"):
        t = sal.copy()
        for c in ("p25_gbp", "median_gbp", "p75_gbp"):
            t[c] = t[c].map(gbp)
        st.dataframe(t.rename(columns={
            "role_family": "Role", "seniority": "Seniority", "postings_with_salary": "Salaries",
            "postings_in_scope": "Adverts", "pct_with_salary": "% with salary", "p25_gbp": "25th pct",
            "median_gbp": "Median", "p75_gbp": "75th pct", "is_low_sample": "Low sample"}),
            hide_index=True, width="stretch")

# ---------- work arrangement ----------
with work:
    wt = d["work_type.csv"].copy()
    wt["Work type"] = wt.work_type.map(WORK_TYPE_LABELS)
    st.subheader("Work arrangement by role")
    st.caption("From the source's own field where it has one; otherwise only clear phrases in the "
               "title or description. Most adverts never say, so 'Not stated' is shown, not hidden.")
    order_wt = [WORK_TYPE_LABELS[k] for k in WORK_TYPE_COLORS]
    stack = alt.Chart(wt).mark_bar(stroke="white", strokeWidth=2).encode(
        x=alt.X("pct_of_role:Q", title="% of adverts", stack="normalize", axis=alt.Axis(format="%")),
        y=alt.Y("role_family:N", title=None, axis=alt.Axis(labelOverlap=False, labelLimit=240)),
        color=alt.Color("Work type:N", sort=order_wt,
                        scale=alt.Scale(domain=order_wt, range=list(WORK_TYPE_COLORS.values())),
                        legend=alt.Legend(orient="top", title=None)),
        order=alt.Order("order:Q"),
        tooltip=[alt.Tooltip("role_family:N", title="Role"), alt.Tooltip("Work type:N"),
                 alt.Tooltip("pct_of_role:Q", title="% of role"), alt.Tooltip("postings:Q", title="Adverts")],
    ).transform_calculate(order=f"indexof({order_wt}, datum['Work type'])")
    st.altair_chart(stack.properties(height=40 * wt.role_family.nunique() + 60), width="stretch")

# ---------- employers ----------
with employers:
    ch = d["company_hiring.csv"]
    top = ch.head(15)
    if ch.empty:
        st.info("No direct employers in scope yet.", icon="ℹ️")
    st.subheader("Employers with the most data/AI postings")
    st.caption("Direct employers only. Recruitment agencies and job boards hide the real employer, "
               "and training providers are not hiring, so all three are excluded.")
    ebars = alt.Chart(top).mark_bar(color=BLUE, cornerRadiusEnd=4).encode(
        x=alt.X("postings_in_scope:Q", title="Postings", axis=alt.Axis(tickMinStep=1)),
        y=alt.Y("company_name:N", sort="-x", title=None, axis=alt.Axis(labelLimit=260)),
        tooltip=[alt.Tooltip("company_name:N", title="Employer"),
                 alt.Tooltip("postings_in_scope:Q", title="Postings"),
                 alt.Tooltip("open_postings:Q", title="Still open"),
                 alt.Tooltip("role_family_list:N", title="Roles")],
    )
    st.altair_chart((ebars + ebars.mark_text(align="left", dx=4).encode(text="postings_in_scope:Q"))
                    .properties(height=420), width="stretch")
    st.caption("Company job boards are tracked completely for 19 chosen employers, so they can rank "
               "higher than employers seen only through Reed search.")
    st.dataframe(ch.rename(columns={
        "company_name": "Employer", "postings_in_scope": "Postings", "open_postings": "Open",
        "role_families": "Role families", "role_family_list": "Roles", "senior_postings": "Senior",
        "tracked_via_company_board": "Own job board tracked"}),
        hide_index=True, width="stretch")

# ---------- method ----------
with method:
    st.subheader("What is counted, and what is not")
    st.markdown(f"""
**In scope:** UK postings whose title matches a tracked role family, excluding paid training
courses. **{s.postings_in_scope:,}** of **{s.postings_tracked_total:,}** postings collected.

Each excluded posting is counted once, under the first reason that applies:

| Excluded | Postings | Why |
|---|---:|---|
| Not a tracked data/AI role | {s.excluded_not_tracked_role:,} | Search results with unrelated titles |
| Not in the UK | {s.excluded_not_uk:,} | Company boards list jobs worldwide |
| Paid training course | {s.excluded_training_courses:,} | Courses advertised as UK data jobs ("fees apply", "job guarantee") |

**Rules, each tested in the pipeline:**
- **Closed** = missing on 2+ days when its source was fully collected. A failed collection is never read as jobs closing.
- **Salaries** = only salaries the advert states, in GBP per year. No estimates. Every figure shows its sample size.
- **Skills** = share of adverts *with a full description*. A missing description is not evidence a skill is not needed.
- **Employers** = direct employers only.

**Sources:** Reed Jobseeker API (UK-wide) and the public Greenhouse, Lever and Ashby job boards of
{s.company_boards_tracked} employers. Collected daily since {pd.to_datetime(s.first_collection_date):%d %b %Y}.

Full method, findings and tests: [GitHub repository]({REPO}).
""")
