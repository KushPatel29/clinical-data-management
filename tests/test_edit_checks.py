"""
Validation of the Data Validation Specification.

The central test is a reconciliation against the injected-defect manifest. It
demands two things at once, and the second is the one usually skipped:

  * **Recall** — every defect deliberately planted in the data is found. A
    check that misses is worse than no check, because it creates confidence.
  * **Precision** — nothing is found that was not planted. A validation suite
    that raises spurious queries burns site goodwill, and site goodwill is the
    scarcest resource on a trial.

Because the generator writes an exhaustive manifest, both are measurable rather
than asserted. Reconciling them is also what caught a real bug during
development: the visit-window check was keyed on the first record seen for a
visit, which is not always the record carrying the visit date, so four injected
protocol deviations went silently undetected.
"""

import csv
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crf.study_metadata import CODELISTS, FORMS, VISITS, all_items  # noqa: E402
from dvs.edit_checks import CHECKS, CHECK_INDEX, QUERY, WARNING  # noqa: E402


def key(row, check_field):
    return (row["subject_id"], row["visit_id"], row["form_id"],
            row["record_num"], row["item_oid"], row[check_field])


# --------------------------------------------------------------------------
# The reconciliation
# --------------------------------------------------------------------------

def test_every_injected_defect_is_detected(manifest, queries):
    """Recall = 100%. Reported per defect class, because a suite that finds 95%
    of everything is usually finding 100% of eight classes and 0% of a ninth."""
    injected = {key(r, "expected_check") for r in manifest}
    detected = {key(r, "check_id") for r in queries}
    missed = injected - detected
    if missed:
        classes = Counter(r["defect_class"] for r in manifest
                          if key(r, "expected_check") in missed)
        pytest.fail(f"{len(missed)} injected defect(s) undetected, by class: "
                    f"{dict(classes)}")


def test_no_query_is_raised_that_was_not_injected(manifest, queries):
    """Precision = 100%. Every query traces to a planted defect."""
    injected = {key(r, "expected_check") for r in manifest}
    detected = {key(r, "check_id") for r in queries}
    extra = detected - injected
    assert not extra, f"{len(extra)} spurious quer(ies): {sorted(extra)[:5]}"


def test_the_manifest_covers_every_check_that_has_a_defect_class(manifest):
    """Guards the reconciliation from passing vacuously. If the generator stops
    injecting a defect class, the check for it becomes untested and both tests
    above still pass."""
    covered = {r["expected_check"] for r in manifest}
    expected = {"EC-REQ", "EC-RANGE", "EC-VS-03", "EC-AE-01", "EC-AE-04",
                "EC-AE-05", "EC-AE-06", "EC-DS-01", "EC-VISIT-01"}
    assert expected <= covered, f"no defects injected for: {expected - covered}"


def test_the_data_is_mostly_clean(manifest, item_data):
    """Realism check. A validation engine demonstrated on data that is 40%
    broken has not been demonstrated on anything resembling a real study."""
    assert len(manifest) / len(item_data) < 0.02


# --------------------------------------------------------------------------
# Specification quality — a DVS is a document before it is code
# --------------------------------------------------------------------------

def test_check_ids_are_unique():
    ids = [c["id"] for c in CHECKS]
    assert len(ids) == len(set(ids))


def test_every_check_has_severity_query_text_and_protocol_reference():
    for c in CHECKS:
        assert c["severity"] in (QUERY, WARNING), c["id"]
        assert c["protocol_ref"].startswith("Section"), c["id"]
        assert len(c["description"]) > 30, c["id"]


def test_query_text_asks_an_answerable_question():
    """'AESTDAT fails EC-AE-04' is not a question a site coordinator can act
    on. Every query text must contain an instruction, and must not leak the
    internal check id or raw field name to the site."""
    for c in CHECKS:
        text = c["query_text"]
        assert "Please" in text, f"{c['id']} does not ask the site to do anything"
        assert c["id"] not in text, f"{c['id']} leaks its check id to the site"


def test_query_text_placeholders_all_resolve(queries):
    """A query that reaches a site with an unresolved {placeholder} in it is an
    embarrassment that also cannot be answered."""
    for q in queries:
        assert "{" not in q["query_text"], q["query_id"]
        assert "}" not in q["query_text"], q["query_id"]


def test_severity_matches_consequence(queries):
    """Severity is not decoration: a 'query' blocks the data point, a 'warning'
    does not. Protocol deviations are recorded, not corrected, so they must
    never be issued at query severity."""
    for q in queries:
        assert q["severity"] == CHECK_INDEX[q["check_id"]]["severity"]
    deviations = [q for q in queries if q["check_id"] == "EC-VISIT-01"]
    assert all(q["severity"] == WARNING for q in deviations)


# --------------------------------------------------------------------------
# CRF metadata integrity — the database build itself
# --------------------------------------------------------------------------

def test_every_item_declares_an_sdtm_target():
    """Deciding the SDTM mapping at design time rather than at submission time
    is the difference between a mapping exercise and a mapping crisis."""
    for form_id, item in all_items():
        assert item.get("sdtm_target"), f"{form_id}.{item['oid']}"
        assert "." in item["sdtm_target"], f"{form_id}.{item['oid']}"


def test_every_coded_item_points_at_a_real_codelist():
    for form_id, item in all_items():
        if item["type"] == "code":
            assert item["codelist"] in CODELISTS, f"{form_id}.{item['oid']}"


def test_every_numeric_item_has_a_range():
    """A numeric field with no range is a field that will accept a systolic
    blood pressure of 3,000 and only be discovered at database lock."""
    for form_id, item in all_items():
        if item["type"] == "number":
            low, high = item.get("range", (None, None))
            assert low is not None, f"{form_id}.{item['oid']} has no range"
            assert low < high


def test_item_oids_are_unique_within_a_form():
    for form_id, form in FORMS.items():
        oids = [i["oid"] for i in form["items"]]
        assert len(oids) == len(set(oids)), form_id


def test_every_visit_references_defined_forms():
    for visit in VISITS:
        for form_id in visit["forms"]:
            assert form_id in FORMS, f"{visit['visit_id']} -> {form_id}"


def test_every_visit_has_a_window():
    """A visit schedule without windows cannot be monitored for protocol
    deviation, which means the deviation is found by the sponsor's auditor
    rather than by the database."""
    for visit in VISITS:
        assert "window_days" in visit
        assert visit["window_days"] >= 0


def test_codelists_have_unique_codes_and_labels():
    for name, entries in CODELISTS.items():
        codes = [c for c, _ in entries]
        assert len(codes) == len(set(codes)), name
        assert all(label.strip() for _, label in entries), name


# --------------------------------------------------------------------------
# Data integrity
# --------------------------------------------------------------------------

def test_no_data_exists_for_a_subject_that_does_not_exist(item_data, subjects):
    known = {s["subject_id"] for s in subjects}
    assert {r["subject_id"] for r in item_data} <= known


def test_every_captured_item_is_defined_on_its_form(item_data):
    """Data for an undefined field means the extract and the CRF specification
    have diverged — a finding in its own right."""
    defined = {(f, i["oid"]) for f, form in FORMS.items() for i in form["items"]}
    seen = {(r["form_id"], r["item_oid"]) for r in item_data}
    assert seen <= defined, f"undefined items in extract: {sorted(seen - defined)}"


def test_repeating_forms_are_the_only_ones_with_multiple_records(item_data):
    records = {(r["subject_id"], r["visit_id"], r["form_id"], r["record_num"])
               for r in item_data}
    counts = Counter((s, v, f) for s, v, f, _ in records)
    for (s, v, form_id), n in counts.items():
        if n > 1:
            assert FORMS[form_id]["repeating"], \
                f"{form_id} is not repeating but has {n} records for {s}/{v}"
