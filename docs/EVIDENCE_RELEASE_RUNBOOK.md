# CDM-REL-01 clinical evidence-release runbook

## Purpose

Use this control to decide whether a synthetic clinical data cut is ready,
requires review, or must be blocked. It binds cohort membership, query
reconciliation, SDTM checks, FHIR quarantine evidence, provenance and UAT into
one reproducible docket.

It does not replace a validated clinical system, protected operational reject
ledger, database lock, investigator sign-off or regulatory approval.

## 90-second review route

1. Open `output/clinical_evidence_release_packet.md` and read the decision.
2. Inspect the two REVIEW gates before relying on any PASS result.
3. Reconcile the three cohort versions to `cohort_release_register.csv`.
4. Confirm the register fingerprint and 12 source hashes in
   `evidence_release_manifest.json`.
5. Review `clinical_reverification_evidence.json` to confirm a one-subject
   mismatch closes COHORT-01 and moves the release to BLOCKED.

## Operating procedure

Rebuild all upstream study outputs as required, then run:

```bash
python governance/evidence_release.py
pytest tests/test_evidence_release.py -v
python -m dashboard.smoke_test
```

Never edit generated release CSV, JSON or Markdown manually. Correct the source
evidence or policy and rebuild.

## Decision rules

- Any BLOCK gate makes the release `BLOCKED`.
- With no BLOCK and at least one REVIEW gate, the release is `REVIEW REQUIRED`.
- `READY` is possible only when every governed gate passes.
- No state records a human approval; approval belongs in the authorized system
  of record.

## Re-verification triggers

Rebuild after a source evidence hash, cohort definition, protocol, edit check,
terminology, SDTM mapping, FHIR validation/quarantine rule, or UAT execution
state changes. The CI release job regenerates the full packet and fails on any
unexplained published drift.
