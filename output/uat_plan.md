# User Acceptance Test Plan — SYN-2026-01

**Study:** A randomised, double-blind, placebo-controlled study (synthetic — no real trial, subjects, or product)

**Generated from:** `crf/study_metadata.py` and `dvs/edit_checks.py`. Regenerate after every CRF or DVS change — a study build whose UAT plan predates its specification has not been tested.

**Total test cases:** 80

| Category | Cases |
|---|---:|
| Access control | 1 |
| Audit trail | 1 |
| Controlled terminology | 11 |
| Data entry | 32 |
| Edit check (negative) | 10 |
| Edit check (positive) | 10 |
| Extract integrity | 1 |
| Form rendering | 8 |
| Query workflow | 1 |
| Visit structure | 5 |

## Coverage statement

- Every one of the 32 collected items has a data-entry test case.
- Every one of the 10 edit checks has **both** a positive case (fires when it should) and a negative case (stays silent when it should). Positive-only UAT is how a check that fires on every record reaches production.
- Every one of the 5 visits has a structure test case.
- Audit trail, access control, query workflow, and extract integrity are covered at system level.

## Execution

Test cases are in `output/uat_plan.csv`, with empty `actual_result`, `pass_fail`, `tester`, and `date` columns for execution. A failed case is a build defect: it is fixed, the build is re-released, and the affected cases are re-executed — not annotated and waived.

## Exit criteria

1. 100% of test cases executed.
2. 100% of test cases passed, or a documented and approved deviation.
3. No open defects of severity 'critical' or 'major'.
4. Sign-off by Data Management, the study statistician, and the sponsor.
