"""Build a deterministic clinical evidence-release decision.

The repository already proves individual pipelines. This control binds their
published artefacts into a versioned data-cut decision without inventing human
approval or claiming regulatory readiness.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "governance" / "evidence_release_policy.json"
OUT = ROOT / "output"

CSV_SOURCES = {
    "subjects": "data/subjects.csv",
    "edc": "data/edc_item_data.csv",
    "trial_defects": "data/injected_defects.csv",
    "fhir_defects": "data/injected_fhir_defects.csv",
    "patient_changes": "data/patient_change_manifest.csv",
    "queries": "output/query_log.csv",
    "ae": "output/sdtm/ae.csv",
    "dm": "output/sdtm/dm.csv",
    "vs": "output/sdtm/vs.csv",
    "sdtm_conformance": "output/sdtm_conformance.csv",
    "uat": "output/uat_plan.csv",
}


def canonical_sha256(path: Path) -> str:
    """Hash logical text consistently across LF and CRLF checkouts."""
    payload = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(payload).hexdigest()


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    policy = json.loads(path.read_text(encoding="utf-8"))
    if policy.get("schema_version") != 1:
        raise ValueError("Unsupported evidence-release policy schema_version")
    required = {
        "control_id",
        "study_id",
        "release_id",
        "cohorts",
        "required_evidence",
        "reverification_triggers",
        "demonstration_boundary",
    }
    missing = required - policy.keys()
    if missing:
        raise ValueError(f"Evidence-release policy missing: {sorted(missing)}")
    return policy


def load_evidence(root: Path = ROOT) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        name: pd.read_csv(root / relative) for name, relative in CSV_SOURCES.items()
    }
    evidence["metrics"] = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
    return evidence


def cohort_register(evidence: dict[str, Any], policy: dict[str, Any]) -> pd.DataFrame:
    subjects = evidence["subjects"].copy()
    subject_ids = set(subjects["subject_id"].astype(str))
    usubjid_to_subject = dict(
        zip(
            evidence["dm"]["USUBJID"].astype(str),
            evidence["dm"]["SUBJID"].astype(str),
            strict=True,
        )
    )
    safety_ids = {
        usubjid_to_subject[value]
        for value in evidence["ae"]["USUBJID"].astype(str)
        if value in usubjid_to_subject
    }
    definitions = {
        "COHORT-ALL-1.0.0": subject_ids,
        "COHORT-SAFETY-1.0.0": subject_ids & safety_ids,
        "COHORT-SITE102-1.0.0": set(
            subjects.loc[subjects["site_id"].eq("SITE-102"), "subject_id"].astype(str)
        ),
    }
    records = []
    for cohort in policy["cohorts"]:
        members = definitions[cohort["cohort_id"]]
        records.append(
            {
                "cohort_id": cohort["cohort_id"],
                "label": cohort["label"],
                "definition": cohort["definition"],
                "owner": cohort["owner"],
                "subjects": len(members),
                "edc_rows": int(evidence["edc"]["subject_id"].astype(str).isin(members).sum()),
                "query_rows": int(
                    evidence["queries"]["subject_id"].astype(str).isin(members).sum()
                ),
                "ae_rows": int(
                    evidence["ae"]["USUBJID"]
                    .astype(str)
                    .map(usubjid_to_subject)
                    .isin(members)
                    .sum()
                ),
                "member_fingerprint": hashlib.sha256(
                    "\n".join(sorted(members)).encode("utf-8")
                ).hexdigest()[:16].upper(),
            }
        )
    return pd.DataFrame(records)


def _trial_keys(frame: pd.DataFrame, check_column: str) -> set[tuple[str, ...]]:
    columns = ["subject_id", "visit_id", "form_id", "record_num", "item_oid", check_column]
    return {
        tuple(str(value) for value in row)
        for row in frame[columns].itertuples(index=False, name=None)
    }


def evaluate_gates(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    subjects = set(evidence["subjects"]["subject_id"].astype(str))
    dm_subjects = set(evidence["dm"]["SUBJID"].astype(str))
    expected_queries = _trial_keys(evidence["trial_defects"], "expected_check")
    actual_queries = _trial_keys(evidence["queries"], "check_id")
    uat = evidence["uat"]
    executed = int(uat["pass_fail"].fillna("").astype(str).str.strip().ne("").sum())
    metrics = evidence["metrics"]
    provenance = metrics.get("full_generation") or metrics["source"]
    jar_hash = str(provenance.get("jar_sha256") or "")

    return [
        {
            "gate_id": "COHORT-01",
            "name": "Cohort identity reconciliation",
            "status": "PASS" if subjects == dm_subjects else "BLOCK",
            "observed": f"{len(subjects & dm_subjects)} matched / {len(subjects)} source subjects",
            "evidence": "data/subjects.csv ↔ output/sdtm/dm.csv",
        },
        {
            "gate_id": "QUERY-01",
            "name": "Injected defect to query reconciliation",
            "status": "PASS" if expected_queries == actual_queries else "BLOCK",
            "observed": (
                f"{len(actual_queries)} query identities / "
                f"{len(expected_queries)} planted defects"
            ),
            "evidence": "data/injected_defects.csv ↔ output/query_log.csv",
        },
        {
            "gate_id": "SDTM-01",
            "name": "Implemented SDTM conformance checks",
            "status": "PASS" if evidence["sdtm_conformance"].empty else "BLOCK",
            "observed": f"{len(evidence['sdtm_conformance'])} findings",
            "evidence": "output/sdtm_conformance.csv",
        },
        {
            "gate_id": "FHIR-01",
            "name": "FHIR planted-defect contract",
            "status": "PASS"
            if len(evidence["fhir_defects"]) == 24
            and evidence["fhir_defects"]["defect_class"].nunique() == 8
            else "BLOCK",
            "observed": (
                f"{len(evidence['fhir_defects'])} expected rejects across "
                f"{evidence['fhir_defects']['defect_class'].nunique()} classes"
            ),
            "evidence": "manifest + executable SQL Server quarantine reconciliation in CI",
        },
        {
            "gate_id": "FHIR-02",
            "name": "Current runtime quarantine traceability",
            "status": "REVIEW",
            "observed": (
                f"{metrics['ingest']['initial_rejected']} runtime rejects summarized; "
                "row-level reject ledger is not committed"
            ),
            "evidence": (
                "metrics.json; protected row-level operational evidence intentionally absent"
            ),
        },
        {
            "gate_id": "PROV-01",
            "name": "Generator provenance",
            "status": "PASS" if len(jar_hash) == 64 and provenance.get("seed") else "BLOCK",
            "observed": (
                f"seed {provenance.get('seed')} · jar {jar_hash[:12]}… · "
                f"reference date {provenance.get('reference_date')}"
            ),
            "evidence": "metrics.json retained full_generation provenance",
        },
        {
            "gate_id": "UAT-01",
            "name": "UAT execution",
            "status": "PASS" if executed == len(uat) and len(uat) else "REVIEW",
            "observed": f"{executed} executed / {len(uat)} generated cases",
            "evidence": "output/uat_plan.csv; execution fields intentionally blank",
        },
    ]


def release_decision(gates: list[dict[str, Any]]) -> str:
    statuses = {gate["status"] for gate in gates}
    if "BLOCK" in statuses:
        return "BLOCKED"
    if "REVIEW" in statuses:
        return "REVIEW REQUIRED"
    return "READY"


def register_fingerprint(cohorts: pd.DataFrame, gates: list[dict[str, Any]]) -> str:
    payload = {"cohorts": cohorts.to_dict(orient="records"), "gates": gates}
    material = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def build_release(
    evidence: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evidence = evidence or load_evidence()
    policy = policy or load_policy()
    cohorts = cohort_register(evidence, policy)
    gates = evaluate_gates(evidence)
    fingerprint = register_fingerprint(cohorts, gates)
    decision = release_decision(gates)
    summary = {
        "schema_version": 1,
        "control_id": policy["control_id"],
        "study_id": policy["study_id"],
        "release_id": policy["release_id"],
        "decision": decision,
        "cohorts": len(cohorts),
        "subjects": int(
            cohorts.loc[cohorts["cohort_id"].eq("COHORT-ALL-1.0.0"), "subjects"].iloc[0]
        ),
        "gates_passed": sum(gate["status"] == "PASS" for gate in gates),
        "gates_review": sum(gate["status"] == "REVIEW" for gate in gates),
        "gates_blocked": sum(gate["status"] == "BLOCK" for gate in gates),
        "register_fingerprint": fingerprint,
        "human_approvals_recorded": 0,
        "demonstration_boundary": policy["demonstration_boundary"],
    }
    required_paths = [ROOT / relative for relative in policy["required_evidence"]]
    missing = [path.relative_to(ROOT).as_posix() for path in required_paths if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Required release evidence missing: {missing}")
    manifest = {
        "schema_version": 1,
        "release_id": policy["release_id"],
        "decision": decision,
        "register_fingerprint": fingerprint,
        "policy_sha256": canonical_sha256(POLICY_PATH),
        "hash_canonicalization": "Text line endings normalized to LF before SHA-256",
        "input_sha256": {
            path.relative_to(ROOT).as_posix(): canonical_sha256(path)
            for path in required_paths
        },
        "required_evidence_complete": True,
        "reverification_triggers": policy["reverification_triggers"],
        "human_approvals_recorded": 0,
        "data_classification": policy["data_classification"],
        "demonstration_boundary": policy["demonstration_boundary"],
    }
    return {"summary": summary, "cohorts": cohorts, "gates": gates, "manifest": manifest}


def reverification_probe(
    evidence: dict[str, Any], policy: dict[str, Any], baseline: dict[str, Any]
) -> dict[str, Any]:
    """Remove one DM subject in memory and prove the release closes."""
    changed = deepcopy(evidence)
    changed["dm"] = evidence["dm"].iloc[1:].copy()
    after = build_release(changed, policy)
    before_gates = {gate["gate_id"]: gate["status"] for gate in baseline["gates"]}
    after_gates = {gate["gate_id"]: gate["status"] for gate in after["gates"]}
    changed_gates = [gate for gate in before_gates if before_gates[gate] != after_gates[gate]]
    return {
        "probe": "synthetic cohort-membership mutation",
        "trigger": "source population changed",
        "decision_before": baseline["summary"]["decision"],
        "decision_after": after["summary"]["decision"],
        "changed_gates": changed_gates,
        "cohort_gate_before": before_gates["COHORT-01"],
        "cohort_gate_after": after_gates["COHORT-01"],
        "baseline_register_fingerprint": baseline["summary"]["register_fingerprint"],
        "changed_register_fingerprint": after["summary"]["register_fingerprint"],
        "source_data_mutated": False,
        "boundary": (
            "The mutation is in-memory test evidence; committed clinical artefacts are unchanged."
        ),
    }


def review_packet(release: dict[str, Any], probe: dict[str, Any]) -> str:
    summary = release["summary"]
    lines = [
        "# SYN-2026-01 Clinical Evidence Release Packet",
        "",
        (
            "> Synthetic portfolio evidence. This packet is not a database lock, "
            "regulatory approval, investigator sign-off, or patient-care authorization."
        ),
        "",
        "## Release decision",
        "",
        f"**{summary['decision']}** — {summary['gates_passed']} gates pass, "
        f"{summary['gates_review']} require review, and {summary['gates_blocked']} block.",
        "",
        f"Release: `{summary['release_id']}` · fingerprint: `{summary['register_fingerprint']}`",
        "",
        "## Gate docket",
        "",
        "| Gate | Status | Observed | Evidence |",
        "|---|---|---|---|",
    ]
    for gate in release["gates"]:
        lines.append(
            f"| {gate['gate_id']} · {gate['name']} | **{gate['status']}** | "
            f"{gate['observed']} | {gate['evidence']} |"
        )
    lines.extend(
        [
            "",
            "## Versioned cohorts",
            "",
            "| Cohort | Owner | Subjects | EDC rows | Queries | AE rows | Fingerprint |",
            "|---|---|---:|---:|---:|---:|---|",
        ]
    )
    for row in release["cohorts"].to_dict(orient="records"):
        lines.append(
            f"| {row['cohort_id']} · {row['label']} | {row['owner']} | {row['subjects']} | "
            f"{row['edc_rows']} | {row['query_rows']} | {row['ae_rows']} | "
            f"`{row['member_fingerprint']}` |"
        )
    lines.extend(
        [
            "",
            "## Re-verification proof",
            "",
            "Removing one DM subject in memory changes COHORT-01 from "
            f"**{probe['cohort_gate_before']}** "
            f"to **{probe['cohort_gate_after']}** and the decision from "
            f"**{probe['decision_before']}** to **{probe['decision_after']}**. "
            "Committed evidence is not mutated.",
            "",
            "## Required disposition",
            "",
            (
                "1. Retain a controlled row-level runtime quarantine ledger outside this "
                "public repository."
            ),
            "2. Execute the 80 generated UAT cases in the authorized quality workflow.",
            (
                "3. Rebuild after any evidence, cohort, protocol, mapping, terminology or "
                "validation change."
            ),
            "4. Obtain real-world approvals in the system of record; this repository records none.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    policy = load_policy()
    evidence = load_evidence()
    release = build_release(evidence, policy)
    probe = reverification_probe(evidence, policy, release)
    OUT.mkdir(exist_ok=True)
    release["cohorts"].to_csv(OUT / "cohort_release_register.csv", index=False)
    pd.DataFrame(release["gates"]).to_csv(OUT / "evidence_release_gates.csv", index=False)
    for name in ("summary", "manifest"):
        (OUT / f"evidence_release_{name}.json").write_text(
            json.dumps(release[name], indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    (OUT / "clinical_reverification_evidence.json").write_text(
        json.dumps(probe, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (OUT / "clinical_evidence_release_packet.md").write_text(
        review_packet(release, probe), encoding="utf-8"
    )
    print(
        f"{policy['control_id']} {release['summary']['decision']} · "
        f"{release['summary']['gates_passed']} pass / "
        f"{release['summary']['gates_review']} review / "
        f"{release['summary']['gates_blocked']} block"
    )


if __name__ == "__main__":
    main()
