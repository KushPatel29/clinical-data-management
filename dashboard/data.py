"""Load and shape the repository's committed analytical evidence.

The Streamlit app is deliberately a read-only view over versioned CSV and JSON
artefacts.  It does not invent demo records at runtime and it never needs a
database connection or a secret in order to render.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


CSV_FILES = {
    "subjects": "data/subjects.csv",
    "edc": "data/edc_item_data.csv",
    "trial_defects": "data/injected_defects.csv",
    "fhir_defects": "data/injected_fhir_defects.csv",
    "patient_changes": "data/patient_change_manifest.csv",
    "queries": "output/query_log.csv",
    "site_performance": "output/query_site_performance.csv",
    "coding": "output/coding_results.csv",
    "coding_worklist": "output/coding_worklist.csv",
    "ae": "output/sdtm/ae.csv",
    "dm": "output/sdtm/dm.csv",
    "vs": "output/sdtm/vs.csv",
    "sdtm_conformance": "output/sdtm_conformance.csv",
    "uat": "output/uat_plan.csv",
}


def _read_csv(root: Path, relative_path: str) -> pd.DataFrame:
    return pd.read_csv(root / relative_path)


def load_repository_data(root: Path = ROOT) -> dict[str, Any]:
    """Load every committed dashboard source and add reusable join columns."""
    data: dict[str, Any] = {
        name: _read_csv(root, path) for name, path in CSV_FILES.items()
    }
    data["metrics"] = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
    data["performance"] = json.loads(
        (root / "docs/performance_results.json").read_text(encoding="utf-8")
    )

    subjects = data["subjects"]
    subjects["consent_date"] = pd.to_datetime(subjects["consent_date"])
    subjects["baseline_date"] = pd.to_datetime(subjects["baseline_date"])
    subjects["completed"] = subjects["completed"].astype(bool)

    dm = data["dm"]
    dm["RFICDTC"] = pd.to_datetime(dm["RFICDTC"])
    dm["AGE"] = pd.to_numeric(dm["AGE"], errors="coerce")

    queries = data["queries"]
    for column in ("issued_date", "closed_date"):
        queries[column] = pd.to_datetime(queries[column], errors="coerce")
    for column in ("days_to_close", "age_days"):
        queries[column] = pd.to_numeric(queries[column], errors="coerce")

    ae = data["ae"]
    ae["AESTDTC"] = pd.to_datetime(ae["AESTDTC"], errors="coerce")
    ae["AEENDTC"] = pd.to_datetime(ae["AEENDTC"], errors="coerce")

    vs = data["vs"]
    vs["VSDTC"] = pd.to_datetime(vs["VSDTC"], errors="coerce")
    vs["VSSTRESN"] = pd.to_numeric(vs["VSSTRESN"], errors="coerce")

    # SDTM uses USUBJID while the operational outputs use subject_id. Carry the
    # business key and cohort dimensions onto AE/VS once so every view filters
    # through the same population definition.
    subject_lookup = dm[["USUBJID", "SUBJID", "SITEID", "ARMCD", "SEX", "AGE"]]
    data["ae"] = ae.merge(subject_lookup, on="USUBJID", how="left", validate="many_to_one")
    data["vs"] = vs.merge(subject_lookup, on="USUBJID", how="left", validate="many_to_one")
    return data


def filter_cohort(
    data: dict[str, Any],
    sites: list[str],
    arms: list[str],
    consent_start: pd.Timestamp,
    consent_end: pd.Timestamp,
) -> dict[str, Any]:
    """Return a consistent cohort slice across every subject-level artefact."""
    subjects = data["subjects"]
    mask = (
        subjects["site_id"].isin(sites)
        & subjects["arm"].isin(arms)
        & subjects["consent_date"].between(consent_start, consent_end)
    )
    selected_subjects = subjects.loc[mask].copy()
    subject_ids = set(selected_subjects["subject_id"])

    view = dict(data)
    view["subjects"] = selected_subjects
    view["dm"] = data["dm"].loc[data["dm"]["SUBJID"].isin(subject_ids)].copy()
    view["ae"] = data["ae"].loc[data["ae"]["SUBJID"].isin(subject_ids)].copy()
    view["vs"] = data["vs"].loc[data["vs"]["SUBJID"].isin(subject_ids)].copy()
    view["queries"] = data["queries"].loc[
        data["queries"]["subject_id"].isin(subject_ids)
    ].copy()
    view["coding"] = data["coding"].loc[
        data["coding"]["subject_id"].isin(subject_ids)
    ].copy()
    view["edc"] = data["edc"].loc[data["edc"]["subject_id"].isin(subject_ids)].copy()
    return view


def query_site_summary(queries: pd.DataFrame) -> pd.DataFrame:
    """Recompute site performance for the active cohort rather than using a snapshot."""
    columns = [
        "site_id",
        "queries_raised",
        "queries_open",
        "queries_closed",
        "close_rate",
        "median_days_to_close",
    ]
    if queries.empty:
        return pd.DataFrame(columns=columns)

    summary = (
        queries.assign(
            is_open=queries["status"].eq("open"),
            is_closed=queries["status"].eq("closed"),
        )
        .groupby("site_id", as_index=False)
        .agg(
            queries_raised=("query_id", "size"),
            queries_open=("is_open", "sum"),
            queries_closed=("is_closed", "sum"),
            median_days_to_close=("days_to_close", "median"),
        )
    )
    summary["close_rate"] = (
        summary["queries_closed"] / summary["queries_raised"]
    ).fillna(0)
    return summary[columns]


def vital_statistical_extremes(vs: pd.DataFrame) -> pd.DataFrame:
    """Flag robust statistical extremes without presenting clinical reference ranges.

    The committed synthetic data contains planted values intended to exercise
    validation.  A 3-IQR fence is intentionally conservative and is labelled as
    statistical, never as a diagnosis or clinical abnormality.
    """
    frames: list[pd.DataFrame] = []
    for test_code, group in vs.dropna(subset=["VSSTRESN"]).groupby("VSTESTCD"):
        q1 = group["VSSTRESN"].quantile(0.25)
        q3 = group["VSSTRESN"].quantile(0.75)
        iqr = q3 - q1
        lower, upper = q1 - (3 * iqr), q3 + (3 * iqr)
        flagged = group.loc[
            (group["VSSTRESN"] < lower) | (group["VSSTRESN"] > upper)
        ].copy()
        if flagged.empty:
            continue
        flagged["test_code"] = test_code
        flagged["expected_band"] = f"{lower:.1f} to {upper:.1f}"
        clipped = flagged["VSSTRESN"].clip(lower=lower, upper=upper)
        flagged["distance"] = (flagged["VSSTRESN"] - clipped).abs()
        frames.append(flagged)
    if not frames:
        return pd.DataFrame(
            columns=["SUBJID", "test_code", "VSSTRESN", "VSSTRESU", "VISIT", "expected_band"]
        )
    return pd.concat(frames, ignore_index=True).sort_values("distance", ascending=False)


def source_ledger(data: dict[str, Any]) -> pd.DataFrame:
    """List the exact versioned evidence used by the app."""
    claims = {
        "subjects": "Enrollment, site, arm and completion",
        "edc": "Captured field and visit volume",
        "trial_defects": "Injected edit-check ground truth",
        "fhir_defects": "FHIR quarantine ground truth",
        "patient_changes": "Type 2 patient history changes",
        "queries": "Query aging and status",
        "coding": "MedDRA and WHODrug outcomes",
        "coding_worklist": "Terms requiring human action",
        "ae": "SDTM adverse events",
        "dm": "SDTM demographics",
        "vs": "SDTM vital signs",
        "sdtm_conformance": "SDTM conformance findings",
        "uat": "Generated UAT coverage",
    }
    records = []
    for name, claim in claims.items():
        records.append(
            {
                "source": CSV_FILES[name],
                "rows": len(data[name]),
                "used for": claim,
            }
        )
    records.extend(
        [
            {"source": "metrics.json", "rows": 1, "used for": "FHIR and warehouse proof"},
            {
                "source": "docs/performance_results.json",
                "rows": len(data["performance"]["queries"]),
                "used for": "Before/after execution-plan evidence",
            },
        ]
    )
    return pd.DataFrame(records)
