# Clinical Data Management — study build, validation, and submission mapping

[![CI](https://github.com/KushPatel29/clinical-data-management/actions/workflows/ci.yml/badge.svg)](https://github.com/KushPatel29/clinical-data-management/actions/workflows/ci.yml)
![CDISC](https://img.shields.io/badge/CDISC-CDASH%20%2B%20SDTM-0B5FA5)
![Python](https://img.shields.io/badge/Python-stdlib%20only-3776AB?logo=python&logoColor=white)
![Tests](https://img.shields.io/badge/tests-51%20passing-3B8C6E)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)

A clinical trial database built the way any other regulated system should be:
the CRF as version-controlled metadata, the Data Validation Specification as
executable declarations, the SDTM mapping decided at design time, and a UAT plan
generated from the specification rather than written from memory.

**Synthetic study SYN-2026-01 — 120 subjects, 5 sites, 5 visits, 8 forms.** No
real trial, subjects, investigational product, or adverse events.

## The number that matters

The generator writes an **exhaustive manifest of every defect it injects**, and
the test suite demands the validation engine recover exactly that set:

```
injected 49   detected 49   missed 0   false positives 0
```

100% recall, 100% precision, re-measured on every run. A validation suite tested
only on clean data proves nothing; one tested on dirty data without ground truth
proves only that it fires. The manifest is what makes both recall and the
false-positive rate *measurable* — and it is what caught a real bug during
development, described below.

## Why build this outside an EDC

In a real trial this lives inside Medidata Rave, Oracle InForm, or Veeva, and
the study is built by clicking through a designer. That works, and it is also
why so many studies cannot answer *"what changed between protocol amendment 2
and 3, and who approved it"* without opening an audit-trail viewer.

Defining the study as code makes the build reviewable the way any regulated
artefact should be — diffable, testable, traceable to a protocol section — and
makes the specification and the implementation **the same object**. The most
common failure in study builds is a DVS document that has quietly drifted from
what the EDC actually does.

## What it does

### 1. CRF metadata ([`crf/study_metadata.py`](crf/study_metadata.py))

CDASH-conventioned forms, items, codelists, and a visit schedule with windows.
Every item declares its **SDTM target at design time**, which is the difference
between a mapping exercise and a mapping crisis. Every visit declares a window,
without which protocol deviation cannot be monitored at all.

### 2. Data Validation Specification ([`dvs/edit_checks.py`](dvs/edit_checks.py))

Ten checks, each carrying an id, a severity, a protocol reference, and **the
exact query text the site will see**. Universal checks (required, range,
codelist) are generated from the CRF metadata rather than written out one per
field — forty fields would otherwise mean forty near-identical specification
entries, each a chance to mistype a range.

Severity is not decoration. A `query` blocks the data point; a `warning` is
recorded for the monitor but does not block database lock. Protocol deviations
are *recorded, not corrected*, so they can never be issued at query severity —
and a test enforces that.

Query text is held to a standard too: `AESTDAT fails EC-AE-04` is not a
question a site coordinator can act on. A test asserts every query text contains
an instruction and never leaks an internal check id to the site.

```
python dvs/edit_checks.py --spec     # the DVS as a review document
```

### 3. Query management ([`dvs/query_management.py`](dvs/query_management.py))

Aging bands, site responsiveness, and top checks by volume. The metric that
predicts whether a study locks on time is not how many queries were raised but
how long the open ones have been open — 400 queries under a week old is healthy;
40 queries averaging 60 days is not, and the second one looks better on a count.

A check firing far more than its peers is usually telling you about the CRF, not
about the sites: a field that is unclear, a range set too tight, or a required
flag on something sites cannot always know.

### 4. SDTM mapping ([`sdtm/map_to_sdtm.py`](sdtm/map_to_sdtm.py))

DM, AE, and VS built end to end — the three structural patterns that cover most
of SDTM: flat, sequence-numbered, and **vertical**. The VS domain is the
interesting one: the CRF collects five vital signs as five *columns*, SDTM
requires them as five *rows* keyed by `VSTESTCD`. That transposition is the
single most common source of SDTM mapping errors.

Plus a conformance checker (required variables, ISO 8601 dates, sequence
uniqueness) — and a test proving the conformance checker can actually *fail*,
because one that has never returned a finding is indistinguishable from one that
does nothing.

### 5. Medical coding ([`coding/code_terms.py`](coding/code_terms.py))

Verbatim terms to a controlled dictionary, with the four outcomes that matter:
auto-coded, resolved by synonym, **ambiguous** (a human decides), and
**uncoded** (a query goes to the site).

Normalisation is deliberately conservative. Aggressive fuzzy matching raises the
auto-code rate and lowers the *correct* auto-code rate, and in safety data a
confidently wrong code is worse than an honest gap. The output is a worklist
ranked by records blocked — coding the term that appears 16 times before the one
that appears once is the whole of coding workload management.

```
Terms requiring coding:      640
  auto-coded (exact):        469 (73.3%)
  coded via synonym:         128 (20.0%)
  ambiguous (to monitor):     12
  uncoded (to site):          31
```

### 6. UAT plan ([`uat/generate_uat_plan.py`](uat/generate_uat_plan.py))

80 test cases generated from the CRF and DVS metadata, so coverage is complete
by construction. The gap this closes: hand-written UAT plans test that checks
*fire* and forget to test that they *stay silent on valid data*. **A check with
no negative test case will happily fire on every record and still pass its UAT.**
Every check here gets both.

Audit trail (21 CFR Part 11), access control, query workflow, and extract
integrity are covered at system level. The plan ships unexecuted — pre-filled
results would be a fabricated record, which in a regulated context is
considerably worse than no record.

## The bug the manifest caught

The visit-window check was keyed on the first record seen for a visit. But the
first record is not always the one carrying the visit date — an AE or CM form
carries none — so the visit was marked evaluated and its window check silently
skipped. **Four injected protocol deviations went undetected.**

Nothing crashed. No test failed except the reconciliation. On a real study the
deviations would have been found by the sponsor's auditor instead of by the
database. This is the entire argument for testing a validation engine against
ground truth rather than against the absence of complaints.

## Run it

```bash
python data_generator/generate_edc_data.py   # 120 subjects + defect manifest
python dvs/edit_checks.py                    # execute the DVS, raise queries
python dvs/query_management.py               # aging, site performance
python sdtm/map_to_sdtm.py                   # DM / AE / VS + conformance
python coding/code_terms.py                  # MedDRA / WHODrug + worklist
python uat/generate_uat_plan.py              # 80 UAT cases
pytest tests/ -v                             # 51 invariants
```

Pure standard library — no install step, no database, runs anywhere in seconds.

## Repo layout

```
crf/              study_metadata.py — forms, items, codelists, visit schedule
data_generator/   synthetic EDC extract + exhaustive defect manifest
dvs/              edit_checks.py (the DVS) · query_management.py (lifecycle)
sdtm/             map_to_sdtm.py — DM/AE/VS + conformance + mapping spec
coding/           code_terms.py — MedDRA/WHODrug coding + coder worklist
uat/              generate_uat_plan.py — UAT cases from the specification
output/           queries, query log, SDTM domains, coding worklist, UAT plan
tests/            51 invariants: manifest reconciliation, CRF integrity,
                  SDTM structure, coding behaviour, query lifecycle, UAT coverage
```

## What this deliberately is not

- **Not an EDC.** No data entry UI, no user management, no audit trail
  implementation. Those are the parts a vendor platform genuinely does better.
- **Not real MedDRA or WHODrug.** Both are licensed and cannot be shipped. The
  dictionary here is a stub; everything *around* the lookup — synonyms,
  ambiguity, the uncoded path, the worklist — is the part that was worth
  building.
- **Not full CDISC conformance.** Three domains, three structural patterns, and
  a conformance checker with three rules. No define.xml, no SUPPQUAL, no
  validation against published CDISC controlled terminology — that needs the CT
  dictionaries, and claiming it without them would be decoration.
- **Not SAS.** The industry standard for this work is SAS, and this is Python.
  An honest gap, stated rather than hidden.

## Honest positioning

This project demonstrates the *transferable core* of clinical data management —
specification discipline, validation design, traceability, and testing against
ground truth. It is not a substitute for hands-on Medidata Rave or Oracle InForm
build experience, or for SAS, both of which senior CDM roles genuinely require.

What it does show is that the engineering habits transfer: a specification that
cannot drift from its implementation, a validation suite with a measured recall
and false-positive rate, and a bug found by reconciliation rather than by an
auditor.
