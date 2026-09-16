# SYN-2026-01 Clinical Evidence Release Packet

> Synthetic portfolio evidence. This packet is not a database lock, regulatory approval, investigator sign-off, or patient-care authorization.

## Release decision

**REVIEW REQUIRED** — 5 gates pass, 2 require review, and 0 block.

Release: `SYN-2026-01-CUT-001` · fingerprint: `eeca413793d22db5772163a47144a3f0ce76f65083f267efc5c41c44b4a4fefa`

## Gate docket

| Gate | Status | Observed | Evidence |
|---|---|---|---|
| COHORT-01 · Cohort identity reconciliation | **PASS** | 120 matched / 120 source subjects | data/subjects.csv ↔ output/sdtm/dm.csv |
| QUERY-01 · Injected defect to query reconciliation | **PASS** | 49 query identities / 49 planted defects | data/injected_defects.csv ↔ output/query_log.csv |
| SDTM-01 · Implemented SDTM conformance checks | **PASS** | 0 findings | output/sdtm_conformance.csv |
| FHIR-01 · FHIR planted-defect contract | **PASS** | 24 expected rejects across 8 classes | manifest + executable SQL Server quarantine reconciliation in CI |
| FHIR-02 · Current runtime quarantine traceability | **REVIEW** | 5 runtime rejects summarized; row-level reject ledger is not committed | metrics.json; protected row-level operational evidence intentionally absent |
| PROV-01 · Generator provenance | **PASS** | seed 20260806 · jar 7fdbc2951d30… · reference date 20260801 | metrics.json retained full_generation provenance |
| UAT-01 · UAT execution | **REVIEW** | 0 executed / 80 generated cases | output/uat_plan.csv; execution fields intentionally blank |

## Versioned cohorts

| Cohort | Owner | Subjects | EDC rows | Queries | AE rows | Fingerprint |
|---|---|---:|---:|---:|---:|---|
| COHORT-ALL-1.0.0 · All enrolled subjects | Clinical Data Manager | 120 | 8754 | 49 | 372 | `2E232AEE46164D81` |
| COHORT-SAFETY-1.0.0 · Safety population | Safety Data Reviewer | 105 | 8066 | 47 | 372 | `EAFD4A2F0243436A` |
| COHORT-SITE102-1.0.0 · SITE-102 operational review | Site Data Lead | 27 | 1946 | 10 | 84 | `EC1A684F18941A2A` |

## Re-verification proof

Removing one DM subject in memory changes COHORT-01 from **PASS** to **BLOCK** and the decision from **REVIEW REQUIRED** to **BLOCKED**. Committed evidence is not mutated.

## Required disposition

1. Retain a controlled row-level runtime quarantine ledger outside this public repository.
2. Execute the 80 generated UAT cases in the authorized quality workflow.
3. Rebuild after any evidence, cohort, protocol, mapping, terminology or validation change.
4. Obtain real-world approvals in the system of record; this repository records none.
