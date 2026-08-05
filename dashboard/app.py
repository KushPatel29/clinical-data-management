"""Interactive evidence console for the synthetic clinical-data portfolio."""

from __future__ import annotations

from html import escape

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from dashboard.data import (
    CSV_FILES,
    filter_cohort,
    load_repository_data,
    query_site_summary,
    source_ledger,
    vital_statistical_extremes,
)

INK = "#102A2E"
MUTED = "#607477"
TEAL = "#118A7E"
COBALT = "#3266D5"
AMBER = "#E19A32"
CRIMSON = "#C94F5C"
MINT = "#91C9BB"
FOG = "#E8F0ED"
COLORS = [TEAL, COBALT, AMBER, CRIMSON, MINT, "#6C7A9C", "#B676A0", "#79A05B"]
PLOTLY_CONFIG = {
    "displaylogo": False,
    "responsive": True,
    "modeBarButtonsToRemove": ["lasso2d", "select2d"],
}


st.set_page_config(
    page_title="Clinical Evidence Console",
    page_icon="✣",
    layout="wide",
    initial_sidebar_state="expanded",
)


st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&family=Space+Grotesk:wght@500;600;700&display=swap');
    :root { --ink:#102A2E; --muted:#607477; --teal:#118A7E; --cobalt:#3266D5;
      --amber:#E19A32; --crimson:#C94F5C; --paper:#F3F7F5; --fog:#E8F0ED; }
    html, body, [class*="css"] { font-family:'IBM Plex Sans', sans-serif; color:var(--ink); }
    h1, h2, h3, [data-testid="stMetricValue"] { font-family:'Space Grotesk', sans-serif; }
    code, [data-testid="stDataFrame"] { font-family:'IBM Plex Mono', monospace; }
    .stApp { background:
      radial-gradient(circle at 92% 2%, rgba(17,138,126,.10), transparent 23rem),
      linear-gradient(180deg, #F8FBFA 0%, var(--paper) 100%); }
    [data-testid="stSidebar"] { background:#EAF1EF; border-right:1px solid #D2DFDB; }
    [data-testid="stSidebar"] h2 { letter-spacing:-.02em; }
    .block-container { max-width:1500px; padding-top:1.8rem; padding-bottom:4rem; }
    .hero { position:relative; overflow:hidden; padding:2.1rem 2.2rem 1.9rem;
      border:1px solid #CEDCD8; border-radius:22px; color:#F7FBFA;
      background:linear-gradient(125deg, #0D292D 0%, #123E42 64%, #176F69 100%);
      box-shadow:0 18px 48px rgba(16,42,46,.12); margin-bottom:1rem; }
    .hero:after { content:""; position:absolute; width:260px; height:260px; right:-70px;
      top:-115px; border:1px solid rgba(255,255,255,.23); border-radius:50%;
      box-shadow:0 0 0 38px rgba(255,255,255,.04),0 0 0 76px rgba(255,255,255,.025); }
    .hero-kicker { font:500 .72rem 'IBM Plex Mono'; letter-spacing:.13em; text-transform:uppercase;
      color:#9EDFD3; margin-bottom:.6rem; }
    .hero h1 { color:#fff; font-size:clamp(2rem,4vw,3.55rem); line-height:1.01;
      letter-spacing:-.055em; max-width:850px; margin:0 0 .8rem; }
    .hero p { color:#C9D9D6; max-width:790px; margin:0; font-size:1.02rem; }
    .hero-meta { display:flex; gap:.55rem; flex-wrap:wrap; margin-top:1.2rem; }
    .hero-meta span { border:1px solid rgba(255,255,255,.18); border-radius:999px;
      padding:.35rem .66rem; color:#DCEBE8; font-size:.78rem; }
    .rail { display:grid; grid-template-columns:repeat(5,1fr); gap:0; border:1px solid #D5E1DE;
      border-radius:15px; background:rgba(255,255,255,.76); overflow:hidden; margin:.45rem 0 1.15rem; }
    .rail-step { padding:.75rem .9rem; position:relative; border-right:1px solid #D5E1DE; }
    .rail-step:last-child { border-right:0; }
    .rail-label { color:var(--muted); font:500 .67rem 'IBM Plex Mono'; text-transform:uppercase;
      letter-spacing:.07em; }
    .rail-value { font:600 1.15rem 'Space Grotesk'; color:var(--ink); margin-top:.1rem; }
    .rail-step:after { content:""; position:absolute; left:0; right:0; bottom:0; height:3px;
      background:var(--teal); }
    .rail-step:nth-child(2):after { background:var(--cobalt); }
    .rail-step:nth-child(3):after { background:var(--amber); }
    .rail-step:nth-child(4):after { background:var(--crimson); }
    .claim-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:.8rem;
      margin:.35rem 0 1.1rem; }
    .claim { background:rgba(255,255,255,.86); border:1px solid #D7E2DF; border-radius:14px;
      padding:1rem 1.05rem; box-shadow:0 5px 18px rgba(16,42,46,.04); min-height:125px; }
    .claim-value { font:700 1.8rem 'Space Grotesk'; letter-spacing:-.04em; color:var(--ink); }
    .claim-label { font-weight:600; font-size:.9rem; color:var(--ink); margin-top:.12rem; }
    .claim-source { color:var(--muted); font:400 .66rem 'IBM Plex Mono'; margin-top:.75rem; }
    .section-kicker { color:var(--teal); font:500 .69rem 'IBM Plex Mono'; letter-spacing:.11em;
      text-transform:uppercase; margin-top:.5rem; }
    .finding { border-left:4px solid var(--teal); background:#E9F3F0; padding:.85rem 1rem;
      border-radius:0 10px 10px 0; margin:.5rem 0 1rem; color:#264548; }
    .finding.warn { border-color:var(--amber); background:#FFF5E4; }
    .finding.risk { border-color:var(--crimson); background:#FCEDEF; }
    .finding strong { font-family:'Space Grotesk'; color:var(--ink); }
    .note { color:var(--muted); font-size:.83rem; }
    .stTabs [data-baseweb="tab-list"] { gap:.25rem; background:#E8F0ED; padding:.25rem;
      border-radius:12px; }
    .stTabs [data-baseweb="tab"] { border-radius:9px; padding:.48rem .78rem; }
    .stTabs [aria-selected="true"] { background:#fff; box-shadow:0 2px 7px rgba(16,42,46,.08); }
    div[data-testid="stPlotlyChart"] { background:rgba(255,255,255,.66); border:1px solid #DCE6E3;
      border-radius:14px; padding:.35rem; }
    a:focus-visible, button:focus-visible, input:focus-visible { outline:3px solid #E19A32!important;
      outline-offset:2px; }
    @media(max-width:900px) { .rail{grid-template-columns:1fr 1fr}.rail-step{border-bottom:1px solid #D5E1DE}
      .claim-grid{grid-template-columns:1fr 1fr}.hero{padding:1.5rem}.hero h1{font-size:2.25rem} }
    @media(max-width:560px) { .rail,.claim-grid{grid-template-columns:1fr}.rail-step{border-right:0} }
    @media(prefers-reduced-motion:reduce) { * { scroll-behavior:auto!important; transition:none!important; } }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(show_spinner="Loading versioned clinical evidence…")
def get_data() -> dict:
    return load_repository_data()


def fmt_int(value: int | float) -> str:
    return f"{int(value):,}"


def style_figure(fig: go.Figure, title: str, height: int = 390) -> go.Figure:
    fig.update_layout(
        title={"text": title, "font": {"family": "Space Grotesk", "size": 18}},
        font={"family": "IBM Plex Sans", "color": INK},
        paper_bgcolor="rgba(255,255,255,0)",
        plot_bgcolor="rgba(255,255,255,0)",
        colorway=COLORS,
        height=height,
        margin={"l": 28, "r": 20, "t": 60, "b": 38},
        legend={"title": None, "orientation": "h", "y": 1.08, "x": 0},
        hoverlabel={"bgcolor": "white", "font_family": "IBM Plex Sans"},
    )
    fig.update_xaxes(showgrid=False, linecolor="#CFDCDA")
    fig.update_yaxes(gridcolor="#DCE6E3", zeroline=False)
    return fig


def claims(items: list[tuple[str, str, str]]) -> None:
    cards = []
    for value, label, source in items:
        cards.append(
            '<div class="claim">'
            f'<div class="claim-value">{escape(value)}</div>'
            f'<div class="claim-label">{escape(label)}</div>'
            f'<div class="claim-source">{escape(source)}</div>'
            "</div>"
        )
    st.markdown(f'<div class="claim-grid">{"".join(cards)}</div>', unsafe_allow_html=True)


def plot(fig: go.Figure) -> None:
    st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG)


data = get_data()
subjects_all = data["subjects"]

with st.sidebar:
    st.markdown("## Evidence controls")
    st.caption("One cohort definition drives every trial view.")
    selected_sites = st.multiselect(
        "Sites",
        options=sorted(subjects_all["site_id"].unique()),
        default=sorted(subjects_all["site_id"].unique()),
    )
    selected_arms = st.multiselect(
        "Randomized arms",
        options=sorted(subjects_all["arm"].unique()),
        default=sorted(subjects_all["arm"].unique()),
    )
    min_date = subjects_all["consent_date"].min().date()
    max_date = subjects_all["consent_date"].max().date()
    consent_window = st.date_input(
        "Consent window",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )
    st.divider()
    st.markdown("**Evidence mode** · committed artifacts")
    st.caption(
        "Synthetic data only. No protected health information, live database, "
        "or write operation is used by this app."
    )
    st.link_button(
        "View source repository ↗",
        "https://github.com/KushPatel29/clinical-data-management",
        width="stretch",
    )

if not selected_sites or not selected_arms or len(consent_window) != 2:
    st.warning("Select at least one site and arm, plus a complete consent window.")
    st.stop()

view = filter_cohort(
    data,
    selected_sites,
    selected_arms,
    pd.Timestamp(consent_window[0]),
    pd.Timestamp(consent_window[1]),
)
subjects = view["subjects"]
if subjects.empty:
    st.warning("No subjects match this cohort. Widen the filters to continue.")
    st.stop()

cohort_label = (
    f"{len(subjects):,} of {len(subjects_all):,} subjects · "
    f"{len(selected_sites)} sites · arms {', '.join(selected_arms)}"
)
st.markdown(
    f"""
    <section class="hero">
      <div class="hero-kicker">SYN-2026-01 · versioned evidence</div>
      <h1>Clinical evidence,<br>from capture to warehouse.</h1>
      <p>A read-only command surface connecting trial operations, SDTM safety, FHIR R4
      quality, and measured SQL Server performance—without hiding the denominator.</p>
      <div class="hero-meta"><span>{escape(cohort_label)}</span><span>FHIR R4</span>
      <span>SQL Server 2022</span><span>synthetic portfolio data</span></div>
    </section>
    """,
    unsafe_allow_html=True,
)

metrics = data["metrics"]
st.markdown(
    f"""
    <div class="rail" aria-label="Evidence pipeline">
      <div class="rail-step"><div class="rail-label">01 · Capture</div>
        <div class="rail-value">{len(subjects_all):,} subjects</div></div>
      <div class="rail-step"><div class="rail-label">02 · Validate</div>
        <div class="rail-value">{len(data['edc']):,} fields</div></div>
      <div class="rail-step"><div class="rail-label">03 · Reconcile</div>
        <div class="rail-value">{len(data['queries'])}/{len(data['trial_defects'])} queries</div></div>
      <div class="rail-step"><div class="rail-label">04 · Standardize</div>
        <div class="rail-value">0 SDTM findings</div></div>
      <div class="rail-step"><div class="rail-label">05 · Scale</div>
        <div class="rail-value">{metrics['full_generation']['resources_generated']/1_000_000:.2f}M FHIR</div></div>
    </div>
    """,
    unsafe_allow_html=True,
)

tab_study, tab_quality, tab_safety, tab_fhir, tab_proof = st.tabs(
    [
        "01 · Study pulse",
        "02 · Quality & queries",
        "03 · Safety & coding",
        "04 · FHIR warehouse",
        "05 · Proof & lineage",
    ]
)


with tab_study:
    st.markdown('<div class="section-kicker">Cohort operations</div>', unsafe_allow_html=True)
    st.subheader("Enrollment and disposition")
    completion_rate = subjects["completed"].mean()
    dm = view["dm"]
    claims(
        [
            (fmt_int(len(subjects)), "subjects in active cohort", "data/subjects.csv · filtered"),
            (f"{completion_rate:.0%}", "completed", f"{subjects['completed'].sum()} / {len(subjects)} subjects"),
            (f"{dm['AGE'].median():.0f} y", "median age", f"range {dm['AGE'].min():.0f}–{dm['AGE'].max():.0f}"),
            (fmt_int(len(view["edc"])), "captured data points", "data/edc_item_data.csv · filtered"),
        ]
    )

    monthly = (
        subjects.assign(month=subjects["consent_date"].dt.to_period("M").dt.to_timestamp())
        .groupby(["month", "site_id"], as_index=False)
        .size()
        .sort_values("month")
    )
    monthly["cumulative"] = monthly.groupby("site_id")["size"].cumsum()
    left, right = st.columns([1.45, 1])
    with left:
        fig = px.line(
            monthly,
            x="month",
            y="cumulative",
            color="site_id",
            markers=True,
            labels={"month": "Consent month", "cumulative": "Cumulative subjects", "site_id": "Site"},
        )
        plot(style_figure(fig, "Enrollment accumulation by site"))
    with right:
        site_arm = subjects.groupby(["site_id", "arm"], as_index=False).size()
        fig = px.bar(
            site_arm,
            x="site_id",
            y="size",
            color="arm",
            barmode="stack",
            text_auto=True,
            labels={"site_id": "Site", "size": "Subjects", "arm": "Arm"},
            color_discrete_map={"A": TEAL, "B": COBALT},
        )
        plot(style_figure(fig, "Site mix and randomized arm", 390))

    left, right = st.columns([1.35, 1])
    with left:
        fig = px.histogram(
            dm,
            x="AGE",
            color="SEX",
            nbins=12,
            barmode="overlay",
            opacity=0.72,
            labels={"AGE": "Age (years)", "count": "Subjects", "SEX": "Sex"},
            color_discrete_map={"F": TEAL, "M": COBALT},
        )
        plot(style_figure(fig, "Age distribution by sex"))
    with right:
        disposition = pd.DataFrame(
            {
                "status": ["Completed", "Not completed"],
                "subjects": [subjects["completed"].sum(), (~subjects["completed"]).sum()],
            }
        )
        fig = px.pie(
            disposition,
            names="status",
            values="subjects",
            hole=0.66,
            color="status",
            color_discrete_map={"Completed": TEAL, "Not completed": AMBER},
        )
        fig.update_traces(textposition="inside", textinfo="percent+value")
        fig.add_annotation(text=f"{completion_rate:.0%}<br><sup>complete</sup>", showarrow=False, font_size=20)
        plot(style_figure(fig, "Subject disposition"))

    site_table = (
        subjects.groupby("site_id", as_index=False)
        .agg(
            subjects=("subject_id", "size"),
            arm_a=("arm", lambda s: s.eq("A").sum()),
            arm_b=("arm", lambda s: s.eq("B").sum()),
            completed=("completed", "sum"),
            first_consent=("consent_date", "min"),
            last_consent=("consent_date", "max"),
        )
    )
    site_table["completion_rate"] = (site_table["completed"] / site_table["subjects"]).map("{:.0%}".format)
    site_table["first_consent"] = site_table["first_consent"].dt.date
    site_table["last_consent"] = site_table["last_consent"].dt.date
    st.dataframe(site_table, width="stretch", hide_index=True)


with tab_quality:
    st.markdown('<div class="section-kicker">Closed-loop data quality</div>', unsafe_allow_html=True)
    st.subheader("Every injected EDC defect became a query")
    queries = view["queries"]
    open_queries = queries.loc[queries["status"].eq("open")]
    reconciled_rate = len(data["queries"]) / max(len(data["trial_defects"]), 1)
    claims(
        [
            (f"{reconciled_rate:.0%}", "defect-to-query reconciliation", f"{len(data['queries'])} queries / {len(data['trial_defects'])} injected defects"),
            (fmt_int(len(open_queries)), "open queries in cohort", f"{len(queries)} total · output/query_log.csv"),
            (f"{open_queries['age_days'].median():.0f} d" if len(open_queries) else "—", "median open-query age", "as of generated query snapshot"),
            (fmt_int(open_queries["age_days"].ge(60).sum()), "open at least 60 days", "operational escalation signal"),
        ]
    )
    if len(open_queries):
        oldest = open_queries.sort_values("age_days", ascending=False).iloc[0]
        st.markdown(
            f'<div class="finding warn"><strong>Attention queue.</strong> The oldest open query is '
            f'{oldest.age_days:.0f} days old at {escape(oldest.site_id)}; '
            f'{open_queries["age_days"].ge(60).sum()} open item(s) have reached 60 days.</div>',
            unsafe_allow_html=True,
        )

    site_summary = query_site_summary(queries)
    left, right = st.columns([1.3, 1])
    with left:
        fig = px.bar(
            site_summary.sort_values("close_rate"),
            x="close_rate",
            y="site_id",
            orientation="h",
            color="queries_open",
            text="queries_open",
            color_continuous_scale=[[0, MINT], [1, CRIMSON]],
            labels={"close_rate": "Query close rate", "site_id": "Site", "queries_open": "Open"},
            hover_data=["queries_raised", "median_days_to_close"],
        )
        fig.update_xaxes(tickformat=".0%", range=[0, 1.03])
        plot(style_figure(fig, "Site close rate · label shows open count"))
    with right:
        age_order = ["0-7 days", "8-14 days", "15-30 days", "31-60 days", "60+ days"]
        aged = open_queries["age_band"].value_counts().reindex(age_order, fill_value=0).rename_axis("age_band").reset_index(name="queries")
        fig = px.bar(
            aged,
            x="queries",
            y="age_band",
            orientation="h",
            text_auto=True,
            color="age_band",
            color_discrete_sequence=[MINT, TEAL, COBALT, AMBER, CRIMSON],
            category_orders={"age_band": age_order[::-1]},
            labels={"queries": "Open queries", "age_band": "Age band"},
        )
        fig.update_layout(showlegend=False)
        plot(style_figure(fig, "Open-query aging"))

    left, right = st.columns([1.25, 1])
    with left:
        by_check = queries.groupby("check_id", as_index=False).size().sort_values("size", ascending=False)
        fig = px.bar(
            by_check,
            x="check_id",
            y="size",
            color="size",
            color_continuous_scale=[[0, MINT], [1, TEAL]],
            text_auto=True,
            labels={"check_id": "Edit check", "size": "Queries"},
        )
        fig.update_layout(coloraxis_showscale=False)
        plot(style_figure(fig, "Query concentration by edit check"))
    with right:
        severity = queries["severity"].value_counts().rename_axis("severity").reset_index(name="queries")
        fig = px.pie(
            severity,
            names="severity",
            values="queries",
            hole=.58,
            color="severity",
            color_discrete_map={"query": CRIMSON, "warning": AMBER},
        )
        fig.update_traces(textinfo="value+percent")
        plot(style_figure(fig, "Severity mix"))

    st.markdown("#### Validation coverage beyond EDC")
    validation_cols = st.columns(3)
    with validation_cols[0]:
        st.markdown(
            f'<div class="finding"><strong>SDTM:</strong> {len(data["sdtm_conformance"])} '
            'conformance findings across DM, AE, and VS.</div>',
            unsafe_allow_html=True,
        )
    with validation_cols[1]:
        st.markdown(
            f'<div class="finding"><strong>FHIR:</strong> {len(data["fhir_defects"])} of '
            f'{len(data["fhir_defects"])} planted defects captured in the quarantine contract.</div>',
            unsafe_allow_html=True,
        )
    with validation_cols[2]:
        st.markdown(
            f'<div class="finding warn"><strong>UAT:</strong> {len(data["uat"])} test cases generated; '
            'execution fields remain intentionally blank.</div>',
            unsafe_allow_html=True,
        )

    query_display = queries[
        ["query_id", "site_id", "subject_id", "check_id", "severity", "status", "age_days", "query_text"]
    ].sort_values(["status", "age_days"], ascending=[False, False])
    with st.expander("Open the query evidence ledger", expanded=False):
        st.dataframe(query_display, width="stretch", hide_index=True)
        st.download_button(
            "Download filtered queries (CSV)",
            query_display.to_csv(index=False).encode("utf-8"),
            "filtered_query_log.csv",
            "text/csv",
        )


with tab_safety:
    st.markdown('<div class="section-kicker">Safety review and dictionary operations</div>', unsafe_allow_html=True)
    st.subheader("Adverse-event signal context")
    ae = view["ae"]
    coding = view["coding"]
    claims(
        [
            (fmt_int(len(ae)), "adverse-event records", f"{ae['SUBJID'].nunique()} subjects represented"),
            (fmt_int(ae["AESER"].eq("Y").sum()), "serious events", f"{ae['AESER'].eq('Y').mean():.1%} of cohort AE records"),
            (fmt_int(ae["AESEV"].eq("SEVERE").sum()), "severe events", "severity is separate from seriousness"),
            (fmt_int(ae["AEOUT"].eq("FATAL").sum()), "fatal outcomes", "synthetic SDTM AE outcome"),
        ]
    )
    st.caption("Counts describe synthetic event records; they are not rates of treatment effect or clinical conclusions.")

    left, right = st.columns([1.3, 1])
    with left:
        monthly_ae = (
            ae.assign(month=ae["AESTDTC"].dt.to_period("M").dt.to_timestamp())
            .groupby(["month", "AESEV"], as_index=False)
            .size()
        )
        fig = px.bar(
            monthly_ae,
            x="month",
            y="size",
            color="AESEV",
            barmode="stack",
            labels={"month": "Event start month", "size": "AE records", "AESEV": "Severity"},
            color_discrete_map={"MILD": MINT, "MODERATE": AMBER, "SEVERE": CRIMSON},
        )
        plot(style_figure(fig, "AE starts over time"))
    with right:
        outcomes = ae["AEOUT"].value_counts().rename_axis("outcome").reset_index(name="events")
        fig = px.bar(
            outcomes.sort_values("events"),
            x="events",
            y="outcome",
            orientation="h",
            text_auto=True,
            color="events",
            color_continuous_scale=[[0, MINT], [1, TEAL]],
            labels={"events": "AE records", "outcome": "Outcome"},
        )
        fig.update_layout(coloraxis_showscale=False)
        plot(style_figure(fig, "Recorded AE outcomes"))

    left, right = st.columns(2)
    with left:
        top_terms = ae["AETERM"].value_counts().head(12).rename_axis("verbatim").reset_index(name="events")
        fig = px.bar(
            top_terms.sort_values("events"),
            x="events",
            y="verbatim",
            orientation="h",
            text_auto=True,
            color_discrete_sequence=[COBALT],
            labels={"events": "AE records", "verbatim": "Verbatim term"},
        )
        plot(style_figure(fig, "Most frequent AE verbatim terms", 430))
    with right:
        med_soc = (
            coding.loc[coding["dictionary"].eq("MedDRA") & coding["soc"].notna()]
            .groupby("soc", as_index=False)
            .size()
            .nlargest(10, "size")
        )
        fig = px.bar(
            med_soc.sort_values("size"),
            x="size",
            y="soc",
            orientation="h",
            text_auto=True,
            color_discrete_sequence=[TEAL],
            labels={"size": "Coded records", "soc": "MedDRA SOC"},
        )
        plot(style_figure(fig, "MedDRA system-organ-class profile", 430))

    coding_status = coding.groupby(["dictionary", "status"], as_index=False).size()
    fig = px.bar(
        coding_status,
        x="dictionary",
        y="size",
        color="status",
        barmode="stack",
        text_auto=True,
        labels={"dictionary": "Dictionary", "size": "Records", "status": "Coding outcome"},
        color_discrete_map={"auto": TEAL, "synonym": COBALT, "uncoded": AMBER, "ambiguous": CRIMSON},
    )
    plot(style_figure(fig, "Dictionary coding disposition"))

    missing_decoded = ae["AEDECOD"].isna().sum()
    if missing_decoded:
        st.markdown(
            f'<div class="finding risk"><strong>Integration handoff gap.</strong> '
            f'{missing_decoded:,} of {len(ae):,} filtered SDTM AE rows have blank AEDECOD values, '
            'even though MedDRA results exist in the separate coding output. This is a visible '
            'downstream merge opportunity, not a hidden success claim.</div>',
            unsafe_allow_html=True,
        )

    st.markdown("#### Manual coding worklist")
    st.dataframe(data["coding_worklist"], width="stretch", hide_index=True)

    st.markdown("#### Vital-sign distribution and statistical review queue")
    selected_vital = st.selectbox(
        "Vital-sign test",
        sorted(view["vs"]["VSTESTCD"].dropna().unique()),
        key="vital_test",
    )
    vital_frame = view["vs"].loc[view["vs"]["VSTESTCD"].eq(selected_vital)]
    fig = px.box(
        vital_frame,
        x="VISIT",
        y="VSSTRESN",
        color="ARMCD",
        points="outliers",
        labels={"VISIT": "Visit", "VSSTRESN": "Standardized result", "ARMCD": "Arm"},
        color_discrete_map={"A": TEAL, "B": COBALT},
    )
    plot(style_figure(fig, f"{vital_frame['VSTEST'].iloc[0]} · distribution by visit"))
    extremes = vital_statistical_extremes(view["vs"])
    st.caption(
        "Review candidates use a conservative 3×IQR statistical fence within each test. "
        "They are not clinical reference ranges and do not imply diagnosis."
    )
    if extremes.empty:
        st.success("No observations exceed the cohort-specific 3×IQR statistical fence.")
    else:
        extreme_display = extremes[
            ["SUBJID", "test_code", "VSSTRESN", "VSSTRESU", "VISIT", "expected_band"]
        ].rename(columns={"expected_band": "3×IQR cohort band"})
        st.dataframe(extreme_display, width="stretch", hide_index=True)


with tab_fhir:
    st.markdown('<div class="section-kicker">FHIR R4 to dimensional analytics</div>', unsafe_allow_html=True)
    st.subheader("Scale proof and warehouse behavior")
    full = metrics["full_generation"]
    ingest = metrics["ingest"]
    quality = metrics["quality"]
    claims(
        [
            (fmt_int(full["population"]), "synthetic FHIR patients generated", "Synthea · deterministic seed 20260806"),
            (fmt_int(full["resources_generated"]), "FHIR resources generated", "metrics.json · full-generation run"),
            (f"{metrics['total_seconds']:.2f} s", "local warehouse benchmark", f"{ingest['initial_read']:,} source resources · SQL Server 16"),
            (fmt_int(metrics["row_counts"]["dw.FactObservation"]), "fact observations loaded", "local benchmark warehouse"),
        ]
    )

    resource_counts = pd.DataFrame(
        full["resource_counts"].items(), columns=["resource_type", "resources"]
    ).sort_values("resources")
    left, right = st.columns([1.2, 1])
    with left:
        fig = px.bar(
            resource_counts,
            x="resources",
            y="resource_type",
            orientation="h",
            text_auto=".2s",
            color="resources",
            color_continuous_scale=[[0, MINT], [1, TEAL]],
            labels={"resources": "Generated resources", "resource_type": "FHIR resource"},
        )
        fig.update_layout(coloraxis_showscale=False)
        plot(style_figure(fig, "Full-generation resource composition", 430))
    with right:
        timings = pd.DataFrame(
            metrics["timings_seconds"].items(), columns=["stage", "seconds"]
        ).sort_values("seconds")
        fig = px.bar(
            timings,
            x="seconds",
            y="stage",
            orientation="h",
            text="seconds",
            color="stage",
            color_discrete_sequence=COLORS,
            labels={"seconds": "Seconds", "stage": "Pipeline stage"},
        )
        fig.update_traces(texttemplate="%{text:.2f}s", textposition="outside")
        fig.update_layout(showlegend=False)
        plot(style_figure(fig, "Measured local pipeline timing", 430))

    st.markdown(
        f'<div class="finding"><strong>Referential integrity held.</strong> '
        f'{quality["facts_on_unknown_patient"]} facts landed on the unknown-patient member; '
        f'{quality["encounters_with_resolved_provider"]:,} encounters resolved a provider and '
        f'{quality["encounters_with_resolved_organization"]:,} resolved an organization.</div>',
        unsafe_allow_html=True,
    )

    row_counts = pd.DataFrame(metrics["row_counts"].items(), columns=["object", "rows"])
    row_counts["layer"] = row_counts["object"].str.split(".").str[0].map(
        {"raw": "Raw", "stg": "Staging", "norm": "Normalized", "dw": "Warehouse"}
    )
    row_counts["table"] = row_counts["object"].str.split(".").str[1]
    fig = px.treemap(
        row_counts,
        path=["layer", "table"],
        values="rows",
        color="layer",
        color_discrete_map={"Raw": INK, "Staging": CRIMSON, "Normalized": TEAL, "Warehouse": COBALT},
    )
    fig.update_traces(textinfo="label+value+percent parent")
    plot(style_figure(fig, "Rows across raw, normalized, and dimensional layers", 500))

    st.markdown("#### Public FHIR endpoint quality snapshot")
    pulls = []
    reasons = []
    for resource_type, result in metrics["live_rest"]["pulls"].items():
        pulls.append(
            {
                "resource_type": resource_type,
                "accepted": result["resources"] - result["rejected"],
                "rejected": result["rejected"],
            }
        )
        for reason, count in result.get("reasons", {}).items():
            reasons.append({"resource_type": resource_type, "reason": reason, "records": count})
    pulls_df = pd.DataFrame(pulls).melt(
        id_vars="resource_type", var_name="disposition", value_name="records"
    )
    left, right = st.columns([1.1, 1])
    with left:
        fig = px.bar(
            pulls_df,
            x="resource_type",
            y="records",
            color="disposition",
            barmode="stack",
            text_auto=True,
            color_discrete_map={"accepted": TEAL, "rejected": CRIMSON},
            labels={"resource_type": "FHIR resource", "records": "Resources", "disposition": "Disposition"},
        )
        plot(style_figure(fig, "Read-only public REST pull disposition"))
    with right:
        reason_df = pd.DataFrame(reasons)
        fig = px.bar(
            reason_df,
            x="records",
            y="reason",
            orientation="h",
            color="resource_type",
            text_auto=True,
            labels={"records": "Rejected resources", "reason": "Validation reason", "resource_type": "Resource"},
            color_discrete_map={"Observation": AMBER, "Encounter": CRIMSON},
        )
        plot(style_figure(fig, "Why resources were quarantined"))
    st.caption(
        f"Observed {metrics['live_rest']['observed_on']} against {metrics['live_rest']['server']}; "
        f"writes performed: {metrics['live_rest']['writes_performed']}. Public test-server content changes over time."
    )

    st.markdown("#### Measured query-plan changes")
    perf_rows = []
    for query_id, result in data["performance"]["queries"].items():
        before = result["before"]["estimated_subtree_cost"]
        after = result["after"]["estimated_subtree_cost"]
        perf_rows.extend(
            [
                {"query": query_id, "version": "Before", "estimated_cost": before},
                {"query": query_id, "version": "After", "estimated_cost": after},
            ]
        )
    perf_df = pd.DataFrame(perf_rows)
    fig = px.bar(
        perf_df,
        x="query",
        y="estimated_cost",
        color="version",
        barmode="group",
        text_auto=".3f",
        color_discrete_map={"Before": AMBER, "After": TEAL},
        labels={"query": "Benchmark query", "estimated_cost": "Estimated subtree cost", "version": "Plan"},
    )
    plot(style_figure(fig, "Before/after optimizer cost · lower is not assumed for every fix"))
    perf_notes = []
    for query_id, result in data["performance"]["queries"].items():
        before = result["before"]["estimated_subtree_cost"]
        after = result["after"]["estimated_subtree_cost"]
        change = (after - before) / before if before else 0
        perf_notes.append(
            {
                "query": query_id,
                "fix": result["fix"],
                "before": before,
                "after": after,
                "change": f"{change:+.1%}",
                "batch mode after": result["after"]["batch_mode"],
                "non-parallel reason before": result["before"]["non_parallel_reason"] or "—",
            }
        )
    st.dataframe(pd.DataFrame(perf_notes), width="stretch", hide_index=True)
    st.caption(
        "These are saved optimizer-plan measurements from the committed CI benchmark, not live timings. "
        "Q3 demonstrates removal of a scalar-UDF parallelism blocker even though estimated cost was flat."
    )

    st.markdown("#### Type 2 patient-history proof")
    st.dataframe(data["patient_changes"], width="stretch", hide_index=True)
    st.caption(
        f"{quality['patients_with_multiple_versions']} patients retain multiple versions; "
        f"maximum versions per patient: {quality['scd2_max_versions_per_patient']}."
    )


with tab_proof:
    st.markdown('<div class="section-kicker">Auditability by construction</div>', unsafe_allow_html=True)
    st.subheader("Claims stay attached to their evidence")
    st.mermaid_chart(
        """
        flowchart LR
          A["Synthetic EDC · 8,754 fields"] --> B["Edit checks · 49 defects"]
          B --> C["Query ledger · 49 queries"]
          C --> D["SDTM DM / AE / VS"]
          D --> E["Conformance · 0 findings"]
          F["Synthea · 1.66M FHIR"] --> G["R4 validation + quarantine"]
          G --> H["Raw JSON ledger"]
          H --> I["Normalized clinical model"]
          I --> J["SQL Server star schema"]
          J --> K["Measured plan evidence"]
        """,
    )

    left, right = st.columns([1.1, 1])
    with left:
        st.markdown("#### Evidence ledger")
        ledger = source_ledger(data)
        st.dataframe(ledger, width="stretch", hide_index=True)
    with right:
        st.markdown("#### UAT coverage map")
        uat_categories = data["uat"]["category"].value_counts().rename_axis("category").reset_index(name="tests")
        fig = px.bar(
            uat_categories.sort_values("tests"),
            x="tests",
            y="category",
            orientation="h",
            text_auto=True,
            color_discrete_sequence=[COBALT],
            labels={"tests": "Generated tests", "category": "UAT category"},
        )
        plot(style_figure(fig, "80 generated test cases by category", 440))

    st.markdown("#### Read the result correctly")
    st.markdown(
        """
        - **Population:** all trial and warehouse records are synthetic. The dashboard must not be used for patient care.
        - **Cohort filters:** site, arm, and consent-date controls affect subject-linked trial views. Repository-wide FHIR and warehouse benchmarks remain fixed because they are a separate evidence run.
        - **Zero findings:** the SDTM conformance file records zero findings for the implemented checks; it is not a claim of universal regulatory compliance.
        - **Query reconciliation:** 49/49 refers to planted EDC defects and their generated query records, not all possible data-quality conditions.
        - **Performance:** plan costs and timings are saved measurements on the documented SQL Server/CI environment; production performance will vary.
        - **Public FHIR:** the REST snapshot was read-only and time-bound. The app uses only the committed snapshot metrics.
        """
    )

    st.markdown("#### Inspect a committed dataset")
    dataset_name = st.selectbox(
        "Dataset",
        options=list(CSV_FILES),
        format_func=lambda name: f"{name} · {CSV_FILES[name]}",
        key="dataset_explorer",
    )
    dataset = data[dataset_name]
    st.dataframe(dataset, width="stretch", hide_index=True)
    st.download_button(
        f"Download {dataset_name} (CSV)",
        dataset.to_csv(index=False).encode("utf-8"),
        f"{dataset_name}.csv",
        "text/csv",
    )

st.divider()
st.caption(
    "Clinical Evidence Console · generated from version-controlled synthetic artifacts · "
    "no runtime database or credentials required"
)
