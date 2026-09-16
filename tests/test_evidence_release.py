"""Controls for the versioned clinical evidence-release decision."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "governance"))

import evidence_release as er


@pytest.fixture(scope="module")
def policy():
    return er.load_policy()


@pytest.fixture(scope="module")
def evidence():
    return er.load_evidence()


@pytest.fixture(scope="module")
def release(evidence, policy):
    return er.build_release(evidence, policy)


def test_policy_is_versioned(policy):
    assert policy["schema_version"] == 1
    assert policy["control_id"] == "CDM-REL-01"


def test_policy_names_three_versioned_cohorts(policy):
    assert len(policy["cohorts"]) == 3
    assert all("1.0.0" in cohort["cohort_id"] for cohort in policy["cohorts"])


def test_policy_has_explicit_demonstration_boundary(policy):
    boundary = policy["demonstration_boundary"]
    assert "synthetic" in boundary.lower()
    assert "not a regulatory approval" in boundary


def test_all_required_evidence_exists(policy):
    assert all((ROOT / path).exists() for path in policy["required_evidence"])


def test_register_contains_one_row_per_policy_cohort(release, policy):
    cohorts = release["cohorts"]
    assert len(cohorts) == len(policy["cohorts"])
    assert cohorts["cohort_id"].is_unique


def test_full_cohort_reconciles_to_120_subjects(release):
    row = release["cohorts"].set_index("cohort_id").loc["COHORT-ALL-1.0.0"]
    assert row["subjects"] == 120
    assert row["edc_rows"] == 8754


def test_site_102_cohort_is_deterministic(release):
    row = release["cohorts"].set_index("cohort_id").loc["COHORT-SITE102-1.0.0"]
    assert row["subjects"] == 27
    assert len(row["member_fingerprint"]) == 16


def test_safety_population_requires_an_ae(release):
    row = release["cohorts"].set_index("cohort_id").loc["COHORT-SAFETY-1.0.0"]
    assert row["subjects"] > 0
    assert row["ae_rows"] == 372


def test_gate_ids_are_unique(release):
    ids = [gate["gate_id"] for gate in release["gates"]]
    assert len(ids) == len(set(ids)) == 7


def test_cohort_identity_gate_passes(release):
    gate = {gate["gate_id"]: gate for gate in release["gates"]}["COHORT-01"]
    assert gate["status"] == "PASS"
    assert "120 matched / 120" in gate["observed"]


def test_query_gate_matches_exact_ground_truth(release):
    gate = {gate["gate_id"]: gate for gate in release["gates"]}["QUERY-01"]
    assert gate["status"] == "PASS"
    assert "49 query identities / 49 planted defects" in gate["observed"]


def test_sdtm_gate_reports_zero_implemented_findings(release):
    gate = {gate["gate_id"]: gate for gate in release["gates"]}["SDTM-01"]
    assert gate["status"] == "PASS"
    assert gate["observed"] == "0 findings"


def test_fhir_contract_gate_is_bounded_to_24_defects_and_8_classes(release):
    gate = {gate["gate_id"]: gate for gate in release["gates"]}["FHIR-01"]
    assert gate["status"] == "PASS"
    assert "24 expected rejects across 8 classes" in gate["observed"]


def test_runtime_quarantine_traceability_requires_review(release):
    gate = {gate["gate_id"]: gate for gate in release["gates"]}["FHIR-02"]
    assert gate["status"] == "REVIEW"
    assert "row-level reject ledger is not committed" in gate["observed"]


def test_generator_provenance_passes(release):
    gate = {gate["gate_id"]: gate for gate in release["gates"]}["PROV-01"]
    assert gate["status"] == "PASS"
    assert "seed 20260806" in gate["observed"]


def test_unexecuted_uat_is_not_presented_as_passed(release):
    gate = {gate["gate_id"]: gate for gate in release["gates"]}["UAT-01"]
    assert gate["status"] == "REVIEW"
    assert gate["observed"] == "0 executed / 80 generated cases"


def test_release_decision_is_review_required(release):
    assert release["summary"]["decision"] == "REVIEW REQUIRED"
    assert release["summary"]["gates_passed"] == 5
    assert release["summary"]["gates_review"] == 2
    assert release["summary"]["gates_blocked"] == 0


def test_release_records_no_human_approval(release):
    assert release["summary"]["human_approvals_recorded"] == 0
    assert release["manifest"]["human_approvals_recorded"] == 0


def test_register_fingerprint_is_deterministic(evidence, policy):
    first = er.build_release(evidence, policy)["summary"]["register_fingerprint"]
    second = er.build_release(evidence, policy)["summary"]["register_fingerprint"]
    assert first == second
    assert len(first) == 64


def test_manifest_hashes_every_required_input(release, policy):
    hashes = release["manifest"]["input_sha256"]
    assert set(hashes) == set(policy["required_evidence"])
    for relative, claimed in hashes.items():
        assert claimed == er.canonical_sha256(ROOT / relative)


def test_hash_is_cross_platform_newline_stable(tmp_path):
    lf = tmp_path / "lf.csv"
    crlf = tmp_path / "crlf.csv"
    lf.write_bytes(b"subject,arm\n1,A\n")
    crlf.write_bytes(b"subject,arm\r\n1,A\r\n")
    assert er.canonical_sha256(lf) == er.canonical_sha256(crlf)


def test_reverification_closes_release_on_cohort_mismatch(evidence, policy, release):
    probe = er.reverification_probe(evidence, policy, release)
    assert probe["cohort_gate_before"] == "PASS"
    assert probe["cohort_gate_after"] == "BLOCK"
    assert probe["decision_before"] == "REVIEW REQUIRED"
    assert probe["decision_after"] == "BLOCKED"
    assert probe["changed_gates"] == ["COHORT-01"]


def test_reverification_does_not_mutate_sources(evidence, policy, release):
    before = len(evidence["dm"])
    probe = er.reverification_probe(evidence, policy, release)
    assert len(evidence["dm"]) == before
    assert probe["source_data_mutated"] is False
    assert probe["baseline_register_fingerprint"] != probe["changed_register_fingerprint"]


def test_review_packet_preserves_non_approval_boundary(evidence, policy, release):
    packet = er.review_packet(release, er.reverification_probe(evidence, policy, release))
    assert "**REVIEW REQUIRED**" in packet
    assert "not a database lock, regulatory approval" in packet
    assert "records none" in packet


def test_published_release_outputs_match_current_evidence(release):
    published = json.loads(
        (ROOT / "output" / "evidence_release_summary.json").read_text(encoding="utf-8")
    )
    assert published == release["summary"]
    cohorts = pd.read_csv(ROOT / "output" / "cohort_release_register.csv")
    pd.testing.assert_frame_equal(cohorts, release["cohorts"], check_dtype=False)


def test_unknown_policy_schema_fails_closed(tmp_path):
    policy = json.loads(er.POLICY_PATH.read_text(encoding="utf-8"))
    policy["schema_version"] = 99
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        er.load_policy(path)
