"""
The reporting views, and the measures documented on top of them.

Two kinds of assertion here, and the second is the one that earns its keep:

  1. Every view runs, returns a sane shape, and states its grain correctly —
     a view that fans out is a view that doubles a measure.

  2. Every number in `powerbi/validation.sql` is recomputed a *second, different
     way* and the two must agree. Documented DAX with nothing to check it
     against is a claim; a filter-context bug that produces a plausible number
     is invisible until someone recomputes it by hand in a meeting.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db.connection import available, connect  # noqa: E402

pytestmark = pytest.mark.warehouse

VALIDATION_SQL = ROOT / "powerbi" / "validation.sql"
MEASURES_MD = ROOT / "powerbi" / "measures.md"

VIEWS = [
    "dw.vw_encounter",
    "dw.vw_encounter_volume_monthly",
    "dw.vw_observation_result",
    "dw.vw_observation_reference_band",
    "dw.vw_observation_outlier",
    "dw.vw_medication_order",
    "dw.vw_diagnosis",
    "dw.vw_patient_panel",
    "dw.vw_readmission_rate_monthly",
    "dw.vw_organization_scorecard",
    "dw.vw_trial_feasibility",
    "dw.vw_pipeline_reconciliation",
    "stg.vw_reject_summary",
]


@pytest.fixture(scope="module")
def cur():
    reachable, reason = available()
    if not reachable:
        pytest.skip(f"no SQL Server: {reason}")
    cn = connect()
    cursor = cn.cursor()
    cursor.execute("SELECT CASE WHEN OBJECT_ID('dw.FactEncounter') IS NULL THEN -1 "
                   "ELSE (SELECT COUNT_BIG(*) FROM dw.FactEncounter) END")
    if int(cursor.fetchone()[0]) <= 0:
        cn.close()
        pytest.skip("warehouse is empty — run `python db/build_warehouse.py --reset` first")
    yield cursor
    cn.close()


def scalar(cur, sql, *params):
    cur.execute(sql, *params)
    return cur.fetchone()[0]


@pytest.fixture(scope="module")
def measures(cur) -> dict[str, float]:
    """Run powerbi/validation.sql and collect (measure_name, value)."""
    text = VALIDATION_SQL.read_text(encoding="utf-8")
    collected: dict[str, float] = {}
    cur.execute(text)
    while True:
        if cur.description:
            for row in cur.fetchall():
                if len(row) >= 2 and row[0] is not None:
                    collected[str(row[0])] = float(row[1]) if row[1] is not None else 0.0
        if not cur.nextset():
            break
    return collected


# ---------------------------------------------------------------------------
# The views
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("view", VIEWS)
def test_view_runs_and_returns_columns(cur, view):
    cur.execute(f"SELECT TOP (5) * FROM {view}")
    assert cur.description, f"{view} returned no columns"
    cur.fetchall()


def test_encounter_view_does_not_fan_out(cur):
    """Grain: one encounter. Six joins to dimensions, and a duplicate row in any
    of them multiplies every measure built on this view."""
    view_rows = scalar(cur, "SELECT COUNT_BIG(*) FROM dw.vw_encounter")
    fact_rows = scalar(cur, "SELECT COUNT_BIG(*) FROM dw.FactEncounter")
    assert view_rows == fact_rows


def test_observation_view_does_not_fan_out(cur):
    view_rows = scalar(cur, "SELECT COUNT_BIG(*) FROM dw.vw_observation_result")
    fact_rows = scalar(cur, "SELECT COUNT_BIG(*) FROM dw.FactObservation")
    assert view_rows == fact_rows


def test_patient_panel_is_one_row_per_patient(cur):
    duplicated = scalar(cur, """
        SELECT COUNT_BIG(*) FROM (
            SELECT patient_id FROM dw.vw_patient_panel
            GROUP BY patient_id HAVING COUNT_BIG(*) > 1) AS x
    """)
    assert duplicated == 0


def test_views_do_not_hide_the_unknown_member(cur):
    """A view with `WHERE provider_key <> -1` baked in makes the numbers look
    better than they are. Unresolved references must stay visible."""
    source = scalar(cur, """
        SELECT OBJECT_DEFINITION(OBJECT_ID('dw.vw_encounter'))
    """)
    assert "<> -1" not in source.replace("start_date_key <> -1", ""), (
        "vw_encounter filters out an Unknown member, hiding a data quality problem")


# ---------------------------------------------------------------------------
# Cross-checks: every measure computed a second way
# ---------------------------------------------------------------------------

def test_encounter_count_matches_the_fact_table(cur, measures):
    assert measures["Encounters"] == scalar(cur, "SELECT COUNT_BIG(*) FROM dw.FactEncounter")


def test_readmission_rate_is_recomputed_from_its_parts(cur, measures):
    numerator = measures["Readmissions 30d"]
    denominator = measures["Inpatient Discharges"]
    assert denominator > 0, "no inpatient encounters — the rate is undefined, not zero"
    assert abs(measures["Readmission Rate 30d"] - numerator / denominator) < 1e-9


def test_readmission_denominator_is_not_all_encounters(cur, measures):
    """The specific way this measure is most often quoted wrong."""
    all_encounters = measures["Encounters"]
    assert measures["Inpatient Discharges"] < all_encounters, (
        "the readmission denominator equals total encounters; it must be inpatient only")


def test_readmission_flag_agrees_with_a_recomputation(cur):
    """Recompute the flag from scratch with a window function and compare.

    The loader computes it with LAG at load time. If that is wrong — a missing
    PARTITION BY, an ORDER BY on the wrong column — the number is plausible and
    nothing else in the pipeline notices.
    """
    disagreements = scalar(cur, """
        WITH recomputed AS (
            SELECT e.encounter_id,
                   CASE WHEN DATEDIFF(DAY,
                            LAG(e.period_end) OVER (PARTITION BY e.patient_id
                                                    ORDER BY e.period_start, e.encounter_id),
                            e.period_start) BETWEEN 0 AND 30
                        THEN 1 ELSE 0 END AS expected
            FROM norm.encounter AS e
            WHERE e.class_code IN ('IMP', 'ACUTE', 'NONAC')
        )
        SELECT COUNT_BIG(*)
        FROM recomputed AS r
        JOIN dw.FactEncounter AS f ON f.encounter_id = r.encounter_id
        WHERE CAST(f.is_readmission_30d AS INT) <> r.expected
    """)
    assert disagreements == 0


def test_average_length_of_stay_ignores_open_stays(cur, measures):
    """AVG over NULLs, matching DAX AVERAGE. Coalescing to zero would drag the
    mean down by however many patients are currently admitted."""
    with_nulls_as_zero = scalar(cur, """
        SELECT CAST(AVG(COALESCE(length_of_stay_days, 0)) AS DECIMAL(18,4))
        FROM dw.vw_encounter WHERE is_inpatient = 1
    """)
    open_stays = scalar(cur, """
        SELECT COUNT_BIG(*) FROM dw.vw_encounter
        WHERE is_inpatient = 1 AND length_of_stay_days IS NULL
    """)
    if open_stays == 0:
        pytest.skip("no open stays in this extract, so the two agree by construction")
    assert measures["Average Length of Stay"] > float(with_nulls_as_zero)


def test_median_and_mean_length_of_stay_both_exist(cur, measures):
    """Publishing only the mean of a right-skewed distribution invites the
    reader to treat it as typical."""
    assert measures["Median Length of Stay"] > 0
    assert measures["Average Length of Stay"] > 0


def test_distinct_patient_count_uses_the_business_key(cur, measures):
    """The Type 2 trap.

    A patient who moved has two surrogate keys. Counting those counts them
    twice. The measure counts patient_id; this asserts the difference is real
    and in the direction that proves the trap is live in this data.
    """
    by_business_key = measures["Patients Seen"]
    by_surrogate_key = measures["Patients Seen (surrogate key - WRONG)"]
    assert by_surrogate_key >= by_business_key
    changed = scalar(cur, """
        SELECT COUNT_BIG(*) FROM (
            SELECT patient_id FROM dw.DimPatient WHERE patient_key <> -1
            GROUP BY patient_id HAVING COUNT_BIG(*) > 1) AS x
    """)
    if changed:
        assert by_surrogate_key > by_business_key, (
            "patients have multiple versions but the surrogate-key count did not "
            "over-count — the Type 2 join is not doing anything")


def test_active_panel_is_anchored_to_the_data_not_to_today(cur, measures):
    """A warehouse loaded from a two-year extract has no encounters this month.
    A panel measured against GETDATE() would report zero."""
    assert measures["Active Patients"] > 0


def test_outlier_rate_is_recomputed_from_its_parts(cur, measures):
    numerator = measures["Statistical Outlier Results"]
    denominator = scalar(cur, "SELECT COUNT_BIG(*) FROM dw.vw_observation_outlier")
    assert denominator > 0
    assert abs(measures["Statistical Outlier Rate"] - numerator / denominator) < 1e-6


def test_outlier_band_is_per_code_not_global(cur):
    """Comparing a systolic blood pressure to the distribution of every numeric
    result in the warehouse is meaningless."""
    distinct_bands = scalar(cur, """
        SELECT COUNT_BIG(DISTINCT CONCAT(p05_value, '|', p95_value))
        FROM dw.vw_observation_reference_band
    """)
    assert distinct_bands > 1, "every code shares one band; the partition is missing"


def test_outlier_band_excludes_thin_codes(cur):
    """A 5th percentile computed from four values is noise."""
    thin = scalar(cur, "SELECT COUNT_BIG(*) FROM dw.vw_observation_reference_band "
                       "WHERE result_count < 30")
    assert thin == 0


def test_outlier_rate_is_plausible(cur, measures):
    """Outside the 5th-95th percentile is about 10% of a distribution by
    construction. Wildly away from that means the band is being computed over
    the wrong population."""
    rate = measures["Statistical Outlier Rate"]
    assert 0.01 < rate < 0.25, f"outlier rate {rate:.3f} is not consistent with a p5-p95 band"


# ---------------------------------------------------------------------------
# The documentation of the measures
# ---------------------------------------------------------------------------

def test_every_documented_measure_has_a_sql_equivalent():
    """A DAX measure with nothing to check it against is a claim."""
    dax_names = set(re.findall(r"^([A-Z][A-Za-z0-9 %]+) =$",
                               MEASURES_MD.read_text(encoding="utf-8"),
                               re.MULTILINE))
    sql_text = VALIDATION_SQL.read_text(encoding="utf-8")
    missing = sorted(name for name in dax_names if f"'{name}'" not in sql_text)
    # YoY is a time-intelligence measure with no single-number equivalent.
    missing = [m for m in missing if "YoY" not in m]
    assert missing == [], f"documented DAX with no SQL cross-check: {missing}"


def test_the_abnormal_result_measure_is_not_called_abnormal():
    """There are no reference ranges in this data. Calling a statistical outlier
    an abnormal result turns a data quality signal into a clinical claim, and
    the rename has to hold in every layer or one of them becomes the place it
    quietly changes back."""
    text = MEASURES_MD.read_text(encoding="utf-8")
    assert "Statistical Outlier Rate" in text
    assert "Abnormal Result Rate =" not in text
    sql = VALIDATION_SQL.read_text(encoding="utf-8")
    assert "'Statistical Outlier Rate'" in sql
    assert "'Abnormal Result Rate'" not in sql


def test_feasibility_criteria_narrow_monotonically(cur):
    """A funnel whose later steps are larger than its earlier ones is a funnel
    with a join in the wrong place."""
    steps = [row for row in
             (cur.execute("SELECT criterion_order, criterion, patients "
                          "FROM dw.vw_trial_feasibility ORDER BY criterion_order").fetchall())]
    final = steps[-1]
    assert final[1] == "Meeting all criteria"
    for _order, criterion, patients in steps[:-1]:
        assert final[2] <= patients, (
            f"the intersection ({final[2]}) exceeds '{criterion}' ({patients})")
