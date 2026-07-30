"""
Data Validation Specification (DVS) — edit checks for study SYN-2026-01.

The DVS is the central artefact of clinical data management: the agreed,
versioned list of every automated check that will run against captured data,
what each one means, what the site will be asked, and which protocol section it
enforces. It is written before first-patient-in, reviewed by the sponsor, and
tested through UAT before the database goes live.

Two design decisions worth defending:

**Checks are declarative data, not code.** Each check is a record with an id, a
severity, the *exact* query text the site will see, and a protocol reference.
That makes the DVS reviewable by a medical monitor who does not read Python, and
it makes the specification and the implementation the same object — the most
common failure in study builds is a DVS document that has drifted from what the
EDC actually does.

**Every check carries the query text.** A check that fires without a clear,
answerable question wastes a site coordinator's afternoon and delays the data.
"AESTDAT fails EC-AE-04" is not a question. "The adverse event start date
precedes the date of informed consent. Please confirm the event start date, or
confirm the event should be recorded as medical history" is.

Usage:
    python dvs/edit_checks.py            # run the checks, write queries
    python dvs/edit_checks.py --spec     # print the DVS as a review document
"""

import argparse
import csv
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crf.study_metadata import CODELISTS, FORMS, VISITS, item_index  # noqa: E402

DATA = ROOT / "data"
OUT = ROOT / "output"
OUT.mkdir(exist_ok=True)

# Severity drives who acts and how fast.
#   query   — a question goes to the site; the data point is not usable until
#             it is answered.
#   warning — recorded for the monitor and the medical reviewer, but does not
#             on its own block database lock.
QUERY, WARNING = "query", "warning"


# ---------------------------------------------------------------------------
# The specification.
# ---------------------------------------------------------------------------
CHECKS = [
    {
        "id": "EC-REQ", "form": "*", "severity": QUERY,
        "description": "A field marked required on the CRF is blank.",
        "query_text": "{label} is required but was not entered. Please complete "
                      "this field, or confirm the assessment was not performed "
                      "and provide the reason.",
        "protocol_ref": "Section 8.1 Data collection",
    },
    {
        "id": "EC-RANGE", "form": "*", "severity": QUERY,
        "description": "A numeric value falls outside the clinically plausible "
                       "range defined on the CRF.",
        "query_text": "{label} was recorded as {value} {unit}, which is outside "
                      "the expected range of {low} to {high} {unit}. Please "
                      "verify against source and correct, or confirm the value "
                      "is accurate.",
        "protocol_ref": "Section 8.3 Range checks",
    },
    {
        "id": "EC-CODE", "form": "*", "severity": QUERY,
        "description": "A coded field contains a value outside its controlled "
                       "terminology.",
        "query_text": "{label} contains '{value}', which is not a permitted "
                      "value. Please select one of the permitted values: "
                      "{allowed}.",
        "protocol_ref": "Section 8.2 Controlled terminology",
    },
    {
        "id": "EC-VS-03", "form": "VS", "severity": QUERY,
        "description": "Systolic blood pressure is not greater than diastolic. "
                       "Almost always a transcription swap.",
        "query_text": "Systolic BP ({sbp} mmHg) is not greater than diastolic BP "
                      "({dbp} mmHg). Please verify against source; if the values "
                      "were transposed, please correct both.",
        "protocol_ref": "Section 8.3 Vital signs consistency",
    },
    {
        "id": "EC-AE-01", "form": "AE", "severity": QUERY,
        "description": "Adverse event end date precedes its start date.",
        "query_text": "The adverse event end date ({end}) is before the start "
                      "date ({start}). Please verify both dates against source "
                      "and correct.",
        "protocol_ref": "Section 9.2 Adverse event reporting",
    },
    {
        "id": "EC-AE-04", "form": "AE", "severity": QUERY,
        "description": "Adverse event start date precedes the date of informed "
                       "consent. Such an event is pre-existing and belongs on "
                       "Medical History, not Adverse Events.",
        "query_text": "The adverse event start date ({start}) is before the date "
                      "of informed consent ({consent}). Please confirm the event "
                      "start date, or confirm the event should be recorded as "
                      "medical history instead.",
        "protocol_ref": "Section 9.1 Definition of an adverse event",
    },
    {
        "id": "EC-AE-05", "form": "AE", "severity": QUERY,
        "description": "A serious adverse event has no action recorded for "
                       "study drug.",
        "query_text": "This adverse event is recorded as serious, but no action "
                      "taken with study drug has been entered. Please complete "
                      "the action taken field.",
        "protocol_ref": "Section 9.4 Serious adverse events",
    },
    {
        "id": "EC-AE-06", "form": "AE", "severity": WARNING,
        "description": "Adverse event outcome is 'recovered/resolved' but no "
                       "end date has been entered.",
        "query_text": "The outcome is recorded as recovered/resolved but the "
                      "event end date is blank. Please enter the date the event "
                      "resolved.",
        "protocol_ref": "Section 9.2 Adverse event reporting",
    },
    {
        "id": "EC-DS-01", "form": "DS", "severity": QUERY,
        "description": "Subject did not complete the study but no reason for "
                       "discontinuation was recorded.",
        "query_text": "The subject is recorded as not having completed the study, "
                      "but no reason for discontinuation was entered. Please "
                      "provide the primary reason.",
        "protocol_ref": "Section 7.4 Subject withdrawal",
    },
    {
        "id": "EC-VISIT-01", "form": "*", "severity": WARNING,
        "description": "Visit performed outside the protocol-defined window. "
                       "A protocol deviation, recorded rather than corrected.",
        "query_text": "The {visit} visit was performed on {actual}, which is "
                      "{days} day(s) outside the protocol window of +/-{window} "
                      "days around study day {target}. Please confirm the visit "
                      "date and complete a protocol deviation form.",
        "protocol_ref": "Section 6.1 Schedule of assessments",
    },
]

CHECK_INDEX = {c["id"]: c for c in CHECKS}


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------

def load_records():
    """Pivot the long EDC extract into one dict per (subject, visit, form,
    record). Checks are far easier to read and to review against the protocol
    when they work on a record than on a stream of item rows."""
    with open(DATA / "edc_item_data.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    records = defaultdict(dict)
    meta = {}
    for r in rows:
        key = (r["subject_id"], r["visit_id"], r["form_id"], r["record_num"])
        records[key][r["item_oid"]] = r["value"]
        meta[key] = {"site_id": r["site_id"], "subject_id": r["subject_id"],
                     "visit_id": r["visit_id"], "form_id": r["form_id"],
                     "record_num": r["record_num"]}
    return [(meta[k], v) for k, v in records.items()]


def load_subjects():
    with open(DATA / "subjects.csv", encoding="utf-8") as f:
        return {r["subject_id"]: r for r in csv.DictReader(f)}


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def raise_query(queries, meta, check_id, item_oid, **fmt):
    check = CHECK_INDEX[check_id]
    queries.append({
        "query_id": f"Q{len(queries) + 1:05d}",
        "site_id": meta["site_id"],
        "subject_id": meta["subject_id"],
        "visit_id": meta["visit_id"],
        "form_id": meta["form_id"],
        "record_num": meta["record_num"],
        "item_oid": item_oid,
        "check_id": check_id,
        "severity": check["severity"],
        "protocol_ref": check["protocol_ref"],
        "query_text": check["query_text"].format(**fmt),
    })


def run_checks():
    subjects = load_subjects()
    visit_by_id = {v["visit_id"]: v for v in VISITS}
    records = load_records()
    queries = []

    # Visit-level checks need one pass to establish the visit date and a second
    # to evaluate it. Doing it inline was a real bug: the first record seen for
    # a visit is not necessarily the one carrying a date (an AE or CM form
    # carries none), so the visit got marked as evaluated and its window check
    # was silently skipped. Reconciling against the defect manifest is what
    # surfaced it — four injected deviations went undetected.
    visit_dates = {}
    for meta, rec in records:
        key = (meta["subject_id"], meta["visit_id"])
        actual = rec.get("VSDAT") or rec.get("EXSTDAT") or rec.get("DSSTDAT")
        if actual and key not in visit_dates:
            visit_dates[key] = (meta, actual)

    for (subject_id, visit_id), (meta, actual) in sorted(visit_dates.items()):
        if visit_id not in visit_by_id:
            continue
        visit = visit_by_id[visit_id]
        baseline = date.fromisoformat(subjects[subject_id]["baseline_date"])
        target = baseline + _days(visit["day"] - 1)
        delta = abs((date.fromisoformat(actual) - target).days)
        if delta > visit["window_days"]:
            # A visit-window deviation belongs to the visit, not to whichever
            # form happened to carry the date, so form and record are cleared.
            raise_query(queries, dict(meta, form_id="", record_num=""),
                        "EC-VISIT-01", "",
                        visit=visit["label"], actual=actual,
                        days=delta - visit["window_days"],
                        window=visit["window_days"], target=visit["day"])

    for meta, rec in records:
        form_id = meta["form_id"]
        form = FORMS[form_id]
        subject = subjects[meta["subject_id"]]

        # --- universal checks, driven by the CRF metadata itself ------------
        # These are generated from the form definition rather than written out
        # one per field. 40 fields would otherwise mean 40 near-identical
        # specification entries, each an opportunity to mistype a range.
        for item in form["items"]:
            oid = item["oid"]
            value = rec.get(oid, "").strip()

            if item.get("required") and not value:
                raise_query(queries, meta, "EC-REQ", oid, label=item["label"])
                continue
            if not value:
                continue

            if item["type"] == "number":
                try:
                    num = float(value)
                except ValueError:
                    raise_query(queries, meta, "EC-CODE", oid,
                                label=item["label"], value=value,
                                allowed="a numeric value")
                    continue
                low, high = item.get("range", (None, None))
                if low is not None and not (low <= num <= high):
                    raise_query(queries, meta, "EC-RANGE", oid,
                                label=item["label"], value=value,
                                unit=item.get("unit", ""), low=low, high=high)

            if item["type"] == "code":
                allowed = [c for c, _ in CODELISTS[item["codelist"]]]
                if value not in allowed:
                    raise_query(queries, meta, "EC-CODE", oid,
                                label=item["label"], value=value,
                                allowed=", ".join(allowed))

        # --- form-specific cross-field checks -------------------------------
        if form_id == "VS":
            sbp, dbp = rec.get("SYSBP", ""), rec.get("DIABP", "")
            if sbp and dbp and float(sbp) <= float(dbp):
                raise_query(queries, meta, "EC-VS-03", "SYSBP", sbp=sbp, dbp=dbp)

        if form_id == "AE":
            start, end = rec.get("AESTDAT", ""), rec.get("AEENDAT", "")
            if start and end and end < start:
                raise_query(queries, meta, "EC-AE-01", "AEENDAT",
                            start=start, end=end)
            if start and start < subject["consent_date"]:
                raise_query(queries, meta, "EC-AE-04", "AESTDAT",
                            start=start, consent=subject["consent_date"])
            if rec.get("AESER") == "Y" and not rec.get("AEACN", "").strip():
                raise_query(queries, meta, "EC-AE-05", "AEACN")
            if rec.get("AEOUT") == "RECOVERED" and not end:
                raise_query(queries, meta, "EC-AE-06", "AEENDAT")

        if form_id == "DS":
            if rec.get("DSCOMP") == "N" and not rec.get("DSTERM", "").strip():
                raise_query(queries, meta, "EC-DS-01", "DSTERM")

    return queries


def _days(n):
    from datetime import timedelta
    return timedelta(days=n)


def write_spec():
    """The DVS as a review document — what a medical monitor signs off."""
    lines = ["# Data Validation Specification — SYN-2026-01", "",
             "| Check | Form | Severity | Description | Protocol |",
             "|---|---|---|---|---|"]
    for c in CHECKS:
        lines.append(f"| `{c['id']}` | {c['form']} | {c['severity']} | "
                     f"{c['description']} | {c['protocol_ref']} |")
    lines += ["", "## Query text issued to sites", ""]
    for c in CHECKS:
        lines += [f"**{c['id']}** — {c['description']}", "",
                  f"> {c['query_text']}", ""]
    (OUT / "data_validation_specification.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", action="store_true",
                    help="print the DVS as a review document")
    args = ap.parse_args()

    spec = write_spec()
    if args.spec:
        print(spec)
        return

    queries = run_checks()
    with open(OUT / "queries_raised.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=queries[0].keys())
        w.writeheader()
        w.writerows(queries)

    by_check = defaultdict(int)
    by_site = defaultdict(int)
    for q in queries:
        by_check[q["check_id"]] += 1
        by_site[q["site_id"]] += 1

    records = len(load_records())
    lines = [
        "EDIT CHECK EXECUTION — SYN-2026-01",
        "=" * 50,
        f"Checks in specification:  {len(CHECKS):>6}",
        f"Form records evaluated:   {records:>6,}",
        f"Queries raised:           {len(queries):>6}",
        f"  query severity:         {sum(1 for q in queries if q['severity'] == QUERY):>6}",
        f"  warning severity:       {sum(1 for q in queries if q['severity'] == WARNING):>6}",
        "-" * 50,
        "BY CHECK",
        *[f"  {cid:<12} {by_check[cid]:>5}  {CHECK_INDEX[cid]['description'][:44]}"
          for cid in sorted(by_check, key=lambda c: -by_check[c])],
        "-" * 50,
        "BY SITE",
        *[f"  {s:<12} {by_site[s]:>5}" for s in sorted(by_site)],
    ]
    (OUT / "edit_check_summary.txt").write_text("\n".join(lines) + "\n",
                                                encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
