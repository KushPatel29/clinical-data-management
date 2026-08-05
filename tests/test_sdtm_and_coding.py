"""
Tests for the SDTM mapping, medical coding, query management, and UAT plan.

The theme is traceability. Every SDTM record has to trace back to a collected
value, every coded term to a verbatim one, and every UAT case to a specification
item — because in a regulated environment "where did this number come from" is
not a rhetorical question.
"""

import csv
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coding.code_terms import MEDDRA, code, normalise  # noqa: E402
from crf.study_metadata import FORMS, VISITS  # noqa: E402
from dvs.edit_checks import CHECKS  # noqa: E402
from sdtm.map_to_sdtm import REQUIRED, VS_TESTS  # noqa: E402

OUT = ROOT / "output"


def read(name):
    with open(OUT / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def dm():
    return read("sdtm/dm.csv")


@pytest.fixture(scope="module")
def ae():
    return read("sdtm/ae.csv")


@pytest.fixture(scope="module")
def vs():
    return read("sdtm/vs.csv")


# --------------------------------------------------------------------------
# SDTM structure
# --------------------------------------------------------------------------

def test_dm_has_exactly_one_record_per_subject(dm, subjects):
    assert len(dm) == len(subjects)
    assert len({r["USUBJID"] for r in dm}) == len(dm)


def test_usubjid_is_study_qualified(dm):
    """USUBJID must be unique across the whole submission, not just this study.
    A bare site-subject number collides the moment two studies are pooled."""
    for r in dm:
        assert r["USUBJID"].startswith("SYN-2026-01-")


def test_required_variables_are_populated(dm, ae, vs):
    for domain, rows in (("DM", dm), ("AE", ae), ("VS", vs)):
        for var in REQUIRED[domain]:
            blank = [r for r in rows if not str(r.get(var, "")).strip()]
            assert not blank, f"{domain}.{var} blank in {len(blank)} record(s)"


def test_sequence_numbers_are_unique_within_subject(ae, vs):
    for domain, rows, seqvar in (("AE", ae, "AESEQ"), ("VS", vs, "VSSEQ")):
        seen = Counter((r["USUBJID"], r[seqvar]) for r in rows)
        assert max(seen.values()) == 1, f"{domain}: duplicate {seqvar}"


def test_sequence_numbers_start_at_one_and_are_contiguous(ae):
    by_subject = {}
    for r in ae:
        by_subject.setdefault(r["USUBJID"], []).append(int(r["AESEQ"]))
    for subject, seqs in by_subject.items():
        assert sorted(seqs) == list(range(1, len(seqs) + 1)), subject


def test_sequence_assignment_is_deterministic(ae):
    """AESEQ is assigned by a stable sort on clinical content, not by whatever
    order the records were read. A sequence number that changes between runs
    makes every downstream cross-reference — and every regulator query about
    'event 3' — unreproducible."""
    from dvs.edit_checks import load_records
    from sdtm.map_to_sdtm import build_ae
    records = load_records()
    first = [(r["USUBJID"], r["AESEQ"], r["AETERM"]) for r in build_ae(records)]
    second = [(r["USUBJID"], r["AESEQ"], r["AETERM"])
              for r in build_ae(list(reversed(records)))]
    assert first == second


def test_vs_is_normalised_one_row_per_test(vs):
    """The wide-to-long transposition: five CRF columns become five SDTM rows.
    This is the most common source of SDTM mapping errors."""
    assert {r["VSTESTCD"] for r in vs} <= set(VS_TESTS)
    per_visit = Counter((r["USUBJID"], r["VISIT"], r["VSTESTCD"]) for r in vs)
    assert max(per_visit.values()) == 1, "a test is duplicated within a visit"


def test_vs_record_count_matches_collected_values(vs, item_data):
    """Every SDTM row traces to exactly one collected value, and no collected
    value is dropped in the transposition."""
    collected = sum(1 for r in item_data
                    if r["form_id"] == "VS" and r["item_oid"] in VS_TESTS
                    and r["value"].strip())
    assert len(vs) == collected


def test_dates_are_iso_8601(dm, ae, vs):
    for rows in (dm, ae, vs):
        for r in rows:
            for var, value in r.items():
                if var.endswith("DTC") and value:
                    assert len(value) == 10 and value[4] == "-" and value[7] == "-"


def test_conformance_report_is_clean():
    findings = read("sdtm_conformance.csv")
    errors = [f for f in findings if f.get("severity") == "error"]
    assert not errors, f"SDTM conformance errors: {errors}"


def test_the_conformance_checker_can_actually_fail():
    """A conformance check that has never returned a finding is indistinguishable
    from one that does nothing."""
    from sdtm.map_to_sdtm import conformance
    broken = [{"STUDYID": "S", "DOMAIN": "DM", "USUBJID": "U", "SUBJID": "",
               "SITEID": "S1", "SEX": "F", "ARMCD": "A", "RFICDTC": "01/02/2026"}]
    findings = conformance("DM", broken)
    rules = " ".join(f["rule"] for f in findings)
    assert "SUBJID is required" in rules
    assert "ISO 8601" in rules


def test_mapping_specification_covers_every_item():
    spec = (OUT / "sdtm_mapping_specification.md").read_text(encoding="utf-8")
    for form_id, form in FORMS.items():
        for item in form["items"]:
            assert f"`{item['oid']}`" in spec, f"{form_id}.{item['oid']}"


# --------------------------------------------------------------------------
# Medical coding
# --------------------------------------------------------------------------

def test_exact_terms_auto_code():
    status, term = code("Headache", MEDDRA, {})
    assert (status, term) == ("auto", "Headache")


def test_coding_is_case_and_whitespace_insensitive():
    assert code("  headache ", MEDDRA, {})[1] == "Headache"


def test_synonyms_resolve():
    from coding.code_terms import MEDDRA_SYNONYMS
    status, term = code("head ache", MEDDRA, MEDDRA_SYNONYMS)
    assert (status, term) == ("synonym", "Headache")


def test_unknown_terms_are_flagged_not_guessed():
    """The important negative. A confidently wrong code in safety data is worse
    than an honest gap, so anything unrecognised must come back 'uncoded' and
    become a query — never a nearest match."""
    status, term = code("felt off", MEDDRA, {})
    assert status == "uncoded"
    assert term == ""


def test_ambiguous_terms_go_to_a_human():
    from coding.code_terms import MEDDRA_AMBIGUOUS, MEDDRA_SYNONYMS
    status, term = code("BP high", MEDDRA, MEDDRA_SYNONYMS, MEDDRA_AMBIGUOUS)
    assert status == "ambiguous"
    assert "|" in term


def test_normalisation_is_conservative():
    """Aggressive normalisation raises the auto-code rate and lowers the
    *correct* auto-code rate. These two must not collapse to the same term."""
    assert normalise("Hypertension") != normalise("Hypotension")


def test_every_coded_term_exists_in_the_dictionary():
    for r in read("coding_results.csv"):
        if r["status"] in ("auto", "synonym") and r["dictionary"] == "MedDRA":
            assert r["coded_term"] in MEDDRA
            assert r["soc"], "a coded term must carry its system organ class"


def test_worklist_is_ranked_by_records_blocked():
    rows = read("coding_worklist.csv")
    blocked = [int(r["records_blocked"]) for r in rows]
    assert blocked == sorted(blocked, reverse=True)
    assert all(r["status"] in ("uncoded", "ambiguous") for r in rows)
    assert all(r["suggested_action"] for r in rows)


def test_uncoded_terms_never_reach_the_sdtm_decode_variable(ae):
    """AEDECOD carries the dictionary term. Populating it from an uncoded
    verbatim would launder a gap into an apparent code."""
    coded = {r["verbatim"]: r for r in read("coding_results.csv")}
    for r in ae:
        entry = coded.get(r["AETERM"])
        if entry and entry["status"] in ("uncoded", "ambiguous"):
            assert not r["AEDECOD"].strip()


# --------------------------------------------------------------------------
# Query management
# --------------------------------------------------------------------------

def test_query_log_accounts_for_every_query(queries):
    log = read("query_log.csv")
    assert len(log) == len(queries)
    assert {r["query_id"] for r in log} == {q["query_id"] for q in queries}


def test_open_and_closed_queries_are_mutually_exclusive():
    for r in read("query_log.csv"):
        if r["status"] == "closed":
            assert r["closed_date"] and not r["age_days"]
        else:
            assert r["age_days"] and not r["closed_date"]


def test_age_bands_match_age_in_days():
    bounds = {"0-7 days": (0, 7), "8-14 days": (8, 14), "15-30 days": (15, 30),
              "31-60 days": (31, 60), "60+ days": (61, 10**6)}
    for r in read("query_log.csv"):
        if r["status"] == "open":
            low, high = bounds[r["age_band"]]
            assert low <= int(r["age_days"]) <= high


def test_site_performance_ties_to_the_query_log():
    log = read("query_log.csv")
    perf = read("query_site_performance.csv")
    assert sum(int(r["queries_raised"]) for r in perf) == len(log)
    assert sum(int(r["queries_open"]) for r in perf) == \
        sum(1 for r in log if r["status"] == "open")


def test_the_slow_site_is_identified():
    """Site responsiveness differs persistently, which makes it a site
    *management* metric rather than a data one.

    The assertion is on **median turnaround**, not on close rate, and that is a
    finding rather than a convenience. At ~15 queries per site the close-rate
    ranking is unstable — it reshuffles between runs on a couple of queries —
    while turnaround separates the sites cleanly. Reporting an unstable ranking
    to a study team gets a site escalated for noise, so the report leads with
    the measure that holds up at this volume."""
    perf = read("query_site_performance.csv")
    with_median = [r for r in perf if r["median_days_to_close"] != ""]
    slowest = max(with_median, key=lambda r: int(r["median_days_to_close"]))
    assert slowest["site_id"] == "SITE-105"
    fastest = min(with_median, key=lambda r: int(r["median_days_to_close"]))
    assert int(slowest["median_days_to_close"]) > \
        2 * int(fastest["median_days_to_close"])


# --------------------------------------------------------------------------
# UAT plan
# --------------------------------------------------------------------------

def test_uat_covers_every_item_and_every_visit():
    plan = read("uat_plan.csv")
    objects = {c["object"] for c in plan}
    for form_id, form in FORMS.items():
        assert form_id in objects
        for item in form["items"]:
            assert f"{form_id}.{item['oid']}" in objects
    for visit in VISITS:
        assert visit["visit_id"] in objects


def test_every_check_has_both_a_positive_and_a_negative_uat_case():
    """The gap this generated plan exists to close. A check with no negative
    case will happily fire on every record and still pass its UAT."""
    plan = read("uat_plan.csv")
    positive = {c["object"] for c in plan if c["category"] == "Edit check (positive)"}
    negative = {c["object"] for c in plan if c["category"] == "Edit check (negative)"}
    for check in CHECKS:
        assert check["id"] in positive, check["id"]
        assert check["id"] in negative, check["id"]


def test_codelist_terminology_cases_exist():
    plan = read("uat_plan.csv")
    terminology = {c["object"] for c in plan
                   if c["category"] == "Controlled terminology"}
    for form_id, form in FORMS.items():
        for item in form["items"]:
            if item["type"] == "code":
                assert f"{form_id}.{item['oid']}" in terminology


def test_regulatory_cases_are_present():
    plan = read("uat_plan.csv")
    categories = {c["category"] for c in plan}
    assert "Audit trail" in categories
    assert "Access control" in categories


def test_uat_cases_are_left_unexecuted():
    """The plan ships as a plan. Pre-filled results would be a fabricated
    record, which in a regulated context is considerably worse than no record."""
    for c in read("uat_plan.csv"):
        assert c["pass_fail"] == ""
        assert c["tester"] == ""


def test_uat_test_ids_are_unique():
    ids = [c["test_id"] for c in read("uat_plan.csv")]
    assert len(ids) == len(set(ids))
