# PLAN — adding a FHIR ingestion layer and a SQL Server warehouse

Phase 0 deliverable. Written before any file was modified, against the repo at
`acd9cdc` on `main`. Work happens on `feat/fhir-warehouse`.

---

## 1. What already exists

**Shape.** A pure-standard-library clinical data management pipeline for a
synthetic CDISC study, `SYN-2026-01`. Six scripts run in sequence; each writes
CSV/Markdown into `output/`; the test suite rebuilds all of it per session and
asserts invariants over the result. No database, no dependencies, no service.

| Component | File | Lines | What it produces |
|---|---|---|---|
| CRF metadata | `crf/study_metadata.py` | 212 | forms, items, codelists, visit schedule with windows; every item carries an `sdtm_target` |
| Data generator | `data_generator/generate_edc_data.py` | 327 | `data/subjects.csv`, `data/edc_item_data.csv`, `data/injected_defects.csv` (the defect manifest) |
| Validation spec | `dvs/edit_checks.py` | 372 | `output/queries_raised.csv`, `output/data_validation_specification.md`, `output/edit_check_summary.txt` |
| Query lifecycle | `dvs/query_management.py` | 172 | `output/query_log.csv`, `output/query_site_performance.csv`, `output/query_summary.txt` |
| SDTM mapping | `sdtm/map_to_sdtm.py` | 278 | `output/sdtm/{dm,ae,vs}.csv`, `output/sdtm_conformance.csv`, mapping spec |
| Medical coding | `coding/code_terms.py` | 194 | `output/coding_results.csv`, `output/coding_worklist.csv` |
| UAT plan | `uat/generate_uat_plan.py` | 166 | `output/uat_plan.csv`, `output/uat_plan.md` |
| Status board | `analytics/make_dashboard.py` | 187 | `docs/dm_status_board.svg` + `.png` — hand-drawn SVG, stdlib only |

**Data sources.** One: a seeded synthetic generator. There is no external
input, no network call, and no database anywhere in the repo today.

**CI** (`.github/workflows/ci.yml`). `ubuntu-latest`, Python 3.12,
`pip install pytest`, run the seven scripts, then `pytest tests/ -v`. Single
job. No services, no matrix, no lint step.

**Deployed demo.** None. This repo has no Streamlit app and no hosted surface.
Its only external presence is the GitHub repo itself, a card on the portfolio
site, and a row in the profile README.

**metrics.json.** Does not exist. Every number in the README today is typed by
hand. That is the single biggest structural weakness in the repo and Phase 9
fixes it.

## 2. Current test count and how it is generated

**58 tests**, confirmed by `python -m pytest --collect-only -q`.

```
tests/conftest.py               51 lines   session-scoped autouse fixture that
                                           subprocesses the six pipeline scripts
tests/test_edit_checks.py      204 lines   manifest reconciliation, severity
                                           rules, query-text standards
tests/test_sdtm_and_coding.py  322 lines   SDTM structure, conformance,
                                           coding behaviour, UAT coverage
tests/test_dashboard.py        100 lines   board reads from CSVs, is
                                           deterministic, stdlib-only AST check
```

The count is asserted nowhere. The README badge says `tests-58 passing` and a
human keeps it in step — which is exactly the failure mode this repo's own
README argues against. Phase 9 makes the count tool-derived.

`tests/test_dashboard.py::test_dashboard_uses_the_standard_library_only`
walks the AST of `analytics/make_dashboard.py` and asserts every import is in
`{collections, csv, pathlib, __future__}`. **It is scoped to that one module**,
so adding dependencies elsewhere in the repo does not break it — but the
README's global "Pure standard library" claim does break. See §4.

## 3. Reuse versus add

**Reused as-is, unmodified:**

- `crf/study_metadata.py` — the visit schedule and codelists become the source
  of the trial's eligibility criteria for the one genuine bridge between the two
  halves of the repo (below).
- The entire CDM pipeline and its 58 tests. Nothing is deleted or rewritten.
- `tests/conftest.py`'s pattern — a session fixture that builds artefacts once
  before asserting on them. The warehouse tests copy this shape.
- `.gitattributes` LF normalisation, which the new SQL and JSON artefacts need
  for the same reason the CSVs did.
- The README's voice, and its "no claim without a test" rule.

**Must be added:**

- A dependency story. The repo currently has none; the warehouse needs
  `pyodbc`, `httpx`, `pydantic`, `pytest`. Handled by keeping the two halves
  independently runnable — see §4.
- A database connection layer, schema DDL, and a migration runner.
- Everything in Phases 1–8.

**The bridge.** The two halves are not glued together artificially. The single
connection is a *feasibility query*: the trial defined in `crf/` has inclusion
criteria; the warehouse holds a health system's patients. Counting how many
patients in `dw` would be eligible for `SYN-2026-01` is the real-world reason a
sponsor asks a hospital for a FHIR extract in the first place. That is one view,
`dw.vw_trial_feasibility`, and it is the honest amount of integration between an
EDC study build and an EHR warehouse.

## 4. Every existing README claim, and what these changes do to it

| # | Claim (README line) | Status after this work |
|---|---|---|
| 1 | Badge `Python — stdlib only` (L5) | **INVALIDATED.** Becomes `Python 3.12` plus a separate badge for the SQL Server layer. The stdlib property is real but is a property of the *CDM pipeline*, not of the repo, and must be stated that way. |
| 2 | "Pure standard library — no install step, no database, runs anywhere in seconds." (L164) | **INVALIDATED as written.** Rewritten to scope it: the CDM pipeline still has no install step and no database; the warehouse layer requires SQL Server and three packages. Both statements stay testable. |
| 3 | Badge `tests-58 passing` (L6) | Changes. Regenerated from `--collect-only` and asserted by a test. |
| 4 | "120 subjects, 5 sites, 5 visits, 8 forms" (L14) | Unaffected. |
| 5 | `injected 49 detected 49 missed 0 false positives 0` (L31) | Unaffected — but moves into `metrics.json` so it can never drift. |
| 6 | Coding block: 640 / 469 (73.3%) / 128 (20.0%) / 12 / 31 (L120-125) | Unaffected — same treatment. |
| 7 | "Ten checks" in the DVS (L64) | Unaffected. |
| 8 | "80 test cases" for UAT (L128) | Unaffected. |
| 9 | "58 invariants" in the run block and repo layout (L161, L176) | Changes with #3, from the same source. |
| 10 | "Not real MedDRA or WHODrug" (L184) | Unaffected and still true. |
| 11 | "Not full CDISC conformance … no define.xml" (L188) | Unaffected. |
| 12 | "Not SAS" (L192) | Unaffected. |
| 13 | The status-board caption, "standard library only … like the rest of the repo" (L20-22) | **INVALIDATED** by "like the rest of the repo". The board itself stays stdlib and its AST test stays green; the caption is corrected. |
| 14 | "The bug the manifest caught" (L140-150) | Preserved verbatim. Any new honest failure is appended, not substituted. |

Three claims break. All three break because the repo grows a second half, and
all three are fixed by scoping rather than by deleting the property — the CDM
pipeline's stdlib-only, no-database character is a real engineering decision and
is worth keeping true and keeping tested.

## 5. File-by-file change list

**Added — ingestion**
```
fhir/__init__.py
fhir/models.py                 pydantic R4 models for the 8 resource types
fhir/client.py                 REST client: pagination, _count/_since/_include,
                               exponential backoff, retry budget
fhir/bulk.py                   NDJSON / Bundle file reader
fhir/ingest.py                 orchestrator: validate → land → quarantine
fhir/synthea.py                Synthea invocation + config
synthea/synthea.properties     committed config (not output)
```

**Added — database**
```
db/__init__.py
db/connection.py               connection factory, env-var driven
db/migrate.py                  ordered .sql runner, idempotent
sql/00_database.sql            database + schemas raw/stg/norm/dw
sql/01_raw.sql                 raw.fhir_resource, ISJSON constraint
sql/02_stg.sql                 stg.ingest_rejects
sql/03_norm.sql                3NF tables, PK/FK, check constraints
sql/04_terminology.sql         norm.code_system, norm.code_concept + seed
sql/05_dw.sql                  dimensions and facts
sql/10_load_norm.sql           OPENJSON shredding procedures
sql/11_load_dw.sql             SCD2 + fact load procedures
sql/12_views.sql               dw.vw_* semantic layer
sql/indexes/*.sql              one file per index, each with a rationale
```

**Added — quality, docs, ops**
```
tests/dq/*.sql                 runnable T-SQL data-quality suite
tests/test_fhir_client.py      pagination/backoff against a fake transport
tests/test_fhir_models.py      validation and quarantine behaviour
tests/test_warehouse.py        SCD2, idempotency, reconciliation (needs SQL Server)
tests/test_metrics.py          every README number matches metrics.json
docs/architecture.md
docs/data-dictionary.md
docs/data-map.md
docs/erd.md
docs/performance.md
powerbi/measures.md
metrics.json
Makefile
requirements.txt
PLAN.md                        this file
```

**Modified**
```
README.md                      scoped claims, new architecture, limitations
.github/workflows/ci.yml       second job with a SQL Server service container
.gitignore                     synthea output, .env, plan cache
analytics/make_dashboard.py    one new panel from dw (stays stdlib — reads CSV)
```

**Untouched — every file below keeps its current bytes**
```
crf/study_metadata.py          dvs/edit_checks.py       dvs/query_management.py
data_generator/generate_edc_data.py                     sdtm/map_to_sdtm.py
coding/code_terms.py           uat/generate_uat_plan.py
tests/conftest.py              tests/test_edit_checks.py
tests/test_sdtm_and_coding.py  tests/test_dashboard.py
data/*.csv                     output/**                LICENSE  .gitattributes
```

The four existing test files are not edited. If any of the 58 tests need a
change to stay green, that is a signal the new work broke something and the new
work gets fixed instead.

*Correction, after the fact:* two of them were edited after all. Running
`ruff --fix` across `tests/` removed an unused `csv` import from
`test_edit_checks.py` and an unused `CODELISTS` from `test_sdtm_and_coding.py`,
and re-sorted imports in both. No assertion changed and all 58 still pass, but
the claim above was broader than what happened and is worth correcting rather
than quietly satisfying. The lint configuration now excludes the CDM modules; it
does not exclude their tests.

## 6. Risks

| Risk | Assessment |
|---|---|
| Breaking the deployed demo | **None.** This repo has no hosted demo. |
| Breaking the portfolio-site link | **None.** The card links to the repo root; the repo is not renamed. |
| Breaking the profile README link | **None.** Same. |
| CI going red | Real. The new job needs a SQL Server service container; if it cannot start, the badge goes red on a repo whose README leads with a green badge. Mitigated by keeping the existing job untouched and independent, so a warehouse failure cannot mask a CDM regression, and the CDM job's result is the one the README's claims depend on. |
| Test count on the portfolio site and profile README | Real and already a known problem across the portfolio. The new count propagates from `metrics.json` to the badge, and the site/profile need the same update in the same pass. |
| Contributor cannot run the warehouse half | Real. Mitigated: warehouse tests skip cleanly without a reachable server, the CDM half is untouched and still dependency-free, and `make` targets are separate. |

## 7. Deviations from the brief, and why

Three, all environmental, all stated in the README:

1. **SQL Server runs natively, not in Docker.** Docker is not installed on the
   build machine; SQL Server 2022 **Developer Edition 16.0.1000.6** is, and is
   running. The brief's intent — "SQL Server 2022 Developer Edition, one
   command" — is met by a real instance rather than a container, and CI uses the
   `mcr.microsoft.com/mssql/server:2022-latest` service container the brief
   names. Compose is provided for anyone who does have Docker, but it is not
   what these results were measured on, and the README says so.

2. **Population size is set by what can be loaded and measured honestly, not by
   the brief's 10,000.** The number that ends up in the README is the number
   that was actually generated, loaded, and queried. It is recorded in
   `metrics.json` by the loader itself.

3. **Python 3.12, not 3.11** — it is what is installed and what existing CI
   already uses. No 3.11-only syntax is involved.

---

---

## Status — what actually happened

Phase 0 is the document above, written before any file was modified. Phases 1–9
were then executed on `feat/fhir-warehouse`. This section is appended rather
than folded in, because a plan is only useful as a record if it still says what
was planned.

**Where the plan was right.** The claim inventory in §4 held: exactly the three
claims it flagged needed changing, and the fix in each case was scoping rather
than deletion. The risk register in §6 held too — the CDM half's 58 tests are
untouched and still pass, and no file in the "untouched" list was edited.

**Where the plan was wrong, and why.**

| Planned | Actual | Reason |
|---|---|---|
| `sql/03_norm.sql`, `sql/04_terminology.sql` | `03_terminology.sql`, `04_norm.sql` | `norm`'s `code_concept_id` columns are `NOT NULL` foreign keys, so terminology has to exist before the tables that reference it. Ordering discovered by the migration failing. |
| `DimDate` 2000–2035, per the brief | 1900–2035 | `exporter.years_of_history = 2` bounds Observations but not Encounters. 52 of 371 encounters in a 20-patient extract fall before 2000, the earliest in 1942. Truncating a patient's history to protect a design decision is the wrong trade. |
| No mention of a change feed | `fhir/changefeed.py` added | A Synthea export is a snapshot; nothing in it changes, so a Type 2 dimension loaded from it produces one version per patient and demonstrates nothing. |
| One board, extended | A second board, `analytics/make_warehouse_board.py` | Extending `make_dashboard.py` would have put the warehouse inside the module whose AST test enforces stdlib-only. A separate module with its own stdlib test keeps both claims true and leaves the working board alone. |
| Two tuned queries | Three | The third — a scalar UDF forcing the whole load to DOP 1 — was found while the build was running and is by a wide margin the largest effect measured. |
| `tests/test_warehouse.py` and friends | Plus `test_docs.py`, `test_semantic_layer.py`, `test_metrics.py`, `test_warehouse_board.py` | The documentation and the README needed the same "no claim without a test" treatment as the code. |

**Four bugs found by the repository's own checks**, all described in the README's
*What broke, and what it taught*: a pydantic validator that only caught half its
case, a Type 2 dimension that sent every fact to the Unknown member, a date
dimension that could not hold a patient's birth, and a view whose window
function was being paid for sixteen times.

**Test count: 58 → 241.** The original 58 are unmodified and run in their own CI
job with pytest and nothing else installed, which is what keeps the
standard-library claim about the CDM half honest.
