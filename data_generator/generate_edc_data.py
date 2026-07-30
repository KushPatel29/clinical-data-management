"""
Synthetic EDC extract for study SYN-2026-01, with a defect manifest.

Produces captured clinical data in the long "item data" shape an EDC actually
exports — one row per subject / visit / form / record / item — plus, critically,
a **manifest of every defect deliberately injected**.

The manifest is the point. A validation engine tested only against clean data
proves nothing, and a validation engine tested against dirty data with no ground
truth proves only that it fires. With a manifest the test suite can demand the
engine recover *exactly* the injected set: nothing missed, and nothing invented.
Recall and false-positive rate both become measurable, which is the difference
between a Data Validation Specification and a pile of plausible-looking checks.

Synthetic only: no real trial, subjects, sites, investigational product, or
adverse events. Fixed seed for reproducibility.

Usage:
    python data_generator/generate_edc_data.py
"""

import csv
import random
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crf.study_metadata import FORMS, STUDY, VISITS  # noqa: E402

random.seed(31415)

OUT = ROOT / "data"
OUT.mkdir(parents=True, exist_ok=True)

N_SUBJECTS = 120
FIRST_CONSENT = date(2025, 9, 1)

# Defect classes and the rate each is injected at. Rates are per eligible
# opportunity, chosen to be realistic rather than convenient — real EDC data is
# mostly clean, and a validation engine that only works on filthy data is not
# demonstrating anything useful.
DEFECT_RATES = {
    "missing_required": 0.012,
    "out_of_range": 0.008,
    "invalid_code": 0.004,
    "end_before_start": 0.030,      # of AEs that have an end date
    "ae_before_consent": 0.015,     # of AEs
    "visit_out_of_window": 0.035,   # of visits
    "sbp_not_above_dbp": 0.010,     # of VS forms
    "serious_ae_no_action": 0.120,  # of serious AEs
    "recovered_no_end_date": 0.060,  # of AEs with outcome 'recovered'
    "discont_no_reason": 0.150,     # of discontinued subjects
}

AE_TERMS = [
    "Headache", "Nausea", "Fatigue", "Dizziness", "Diarrhoea", "Rash",
    "Upper respiratory tract infection", "Back pain", "Insomnia", "Pyrexia",
    "Vomiting", "Cough", "Arthralgia", "Abdominal pain", "Pruritus",
    # Deliberately messy verbatim terms — this is what sites actually type, and
    # it is what makes medical coding a job rather than a lookup.
    "head ache", "feeling sick to stomach", "tired all the time",
    "sore throat and runny nose", "BP high",
    # Terms no dictionary will match. Real, common, and the reason the
    # coding worklist has an "uncoded -> query the site" path at all.
    "felt off", "not himself",
]
CM_TERMS = ["Paracetamol", "Ibuprofen", "Amoxicillin", "Omeprazole",
            "Atorvastatin", "Metformin", "Salbutamol", "tylenol", "advil"]
MH_TERMS = ["Hypertension", "Type 2 diabetes mellitus", "Asthma",
            "Osteoarthritis", "Hypercholesterolaemia", "Migraine"]
DISCONT_REASONS = ["Adverse event", "Withdrawal by subject", "Lost to follow-up",
                   "Protocol deviation", "Physician decision"]

defects = []


def log_defect(subject, visit, form, record, item, kind, expected_check):
    defects.append({
        "subject_id": subject, "visit_id": visit, "form_id": form,
        "record_num": record, "item_oid": item, "defect_class": kind,
        "expected_check": expected_check,
    })


def hit(rate):
    return random.random() < rate


def main():
    rows = []          # long item data
    subjects = []

    def emit(subject, visit, form, record, oid, value):
        rows.append({
            "study_id": STUDY["study_id"], "site_id": subject["site_id"],
            "subject_id": subject["subject_id"], "visit_id": visit,
            "form_id": form, "record_num": record, "item_oid": oid,
            "value": "" if value is None else str(value),
        })

    for n in range(1, N_SUBJECTS + 1):
        site = random.choice(STUDY["sites"])
        subject_id = f"{site}-{n:03d}"
        consent = FIRST_CONSENT + timedelta(days=random.randint(0, 210))
        arm = random.choice(["A", "B"])
        # Baseline is day 1; screening sits before it.
        baseline = consent + timedelta(days=random.randint(10, 18))
        completed = random.random() < 0.82

        subject = {"subject_id": subject_id, "site_id": site,
                   "consent_date": consent.isoformat(), "arm": arm,
                   "baseline_date": baseline.isoformat(),
                   "completed": 1 if completed else 0}
        subjects.append(subject)

        # How far the subject got before discontinuing.
        last_visit = 5 if completed else random.randint(2, 4)

        for visit in VISITS:
            if visit["visit_num"] > last_visit:
                break
            vid = visit["visit_id"]

            # Actual visit date, occasionally pushed outside the protocol window.
            drift = random.randint(-visit["window_days"], visit["window_days"])
            if hit(DEFECT_RATES["visit_out_of_window"]):
                drift = (visit["window_days"] + random.randint(2, 9)) * \
                    random.choice([1, -1])
                log_defect(subject_id, vid, "", "", "",
                           "visit_out_of_window", "EC-VISIT-01")
            vdate = baseline + timedelta(days=visit["day"] - 1 + drift)

            # A subject who discontinues early still completes a Disposition
            # form — at whatever visit turns out to be their last, not at the
            # scheduled End of Study they never reach. Omitting this is a
            # classic study-build defect: the discontinuation reason, which is
            # a primary safety output, is simply never collected.
            forms_this_visit = list(visit["forms"])
            if visit["visit_num"] == last_visit and "DS" not in forms_this_visit:
                forms_this_visit.append("DS")

            for form_id in forms_this_visit:
                form = FORMS[form_id]
                n_records = 1
                if form["repeating"]:
                    n_records = random.choices([0, 1, 2, 3],
                                               weights=[0.45, 0.30, 0.17, 0.08])[0]

                for rec in range(1, n_records + 1):
                    build_record(subject, vid, form_id, rec, vdate, consent,
                                 emit, arm)

    with open(OUT / "subjects.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=subjects[0].keys())
        w.writeheader()
        w.writerows(subjects)

    with open(OUT / "edc_item_data.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    with open(OUT / "injected_defects.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=defects[0].keys())
        w.writeheader()
        w.writerows(defects)

    print(f"wrote {len(subjects):5d} subjects")
    print(f"wrote {len(rows):5d} item-data rows")
    print(f"wrote {len(defects):5d} injected defects (the manifest)")
    by_class = {}
    for d in defects:
        by_class[d["defect_class"]] = by_class.get(d["defect_class"], 0) + 1
    for k in sorted(by_class):
        print(f"       {k:<24} {by_class[k]:>4}")


def build_record(subject, vid, form_id, rec, vdate, consent, emit, arm):
    """Populate one form record, injecting defects at the manifest rates."""
    sid = subject["subject_id"]

    if form_id == "DM":
        emit(subject, vid, form_id, rec, "BRTHDAT",
             (consent - timedelta(days=random.randint(18, 75) * 365)).isoformat())
        emit(subject, vid, form_id, rec, "SEX", random.choice(["F", "M"]))
        emit(subject, vid, form_id, rec, "RFICDAT", consent.isoformat())
        emit(subject, vid, form_id, rec, "ARM", arm)
        return

    if form_id == "IE":
        emit(subject, vid, form_id, rec, "IEYN", "Y")
        emit(subject, vid, form_id, rec, "AGEELIG", "Y")
        return

    if form_id == "VS":
        emit(subject, vid, form_id, rec, "VSDAT", vdate.isoformat())
        # Redrawn until the pair is unambiguously ordered. Without this the
        # tails of the two distributions overlap a couple of times per thousand
        # forms and produce *genuine* sbp <= dbp records that are not in the
        # manifest — real data problems, but they make the manifest
        # non-exhaustive and the recall test unable to distinguish a true
        # positive from an invented one.
        sbp = dbp = 0
        while sbp - dbp < 10:
            sbp = int(random.gauss(128, 14))
            dbp = int(random.gauss(80, 9))
        if hit(DEFECT_RATES["sbp_not_above_dbp"]):
            sbp, dbp = dbp, sbp          # transcription swap — a real error mode
            log_defect(sid, vid, form_id, rec, "SYSBP",
                       "sbp_not_above_dbp", "EC-VS-03")
        if hit(DEFECT_RATES["out_of_range"]):
            sbp = 310                    # implausible, outside the CRF range
            log_defect(sid, vid, form_id, rec, "SYSBP",
                       "out_of_range", "EC-RANGE")
        emit(subject, vid, form_id, rec, "SYSBP", sbp)
        emit(subject, vid, form_id, rec, "DIABP", dbp)
        emit(subject, vid, form_id, rec, "PULSE", int(random.gauss(74, 11)))
        if random.random() < 0.85:
            emit(subject, vid, form_id, rec, "TEMP", round(random.gauss(36.7, 0.4), 1))
        # Clamped to a plausible adult weight: the untruncated normal
        # produces sub-30kg adults a couple of times per thousand forms,
        # which are real range violations but absent from the manifest.
        weight = round(max(42.0, random.gauss(78, 15)), 1)
        if hit(DEFECT_RATES["missing_required"]):
            log_defect(sid, vid, form_id, rec, "WEIGHT",
                       "missing_required", "EC-REQ")
        else:
            emit(subject, vid, form_id, rec, "WEIGHT", weight)
        return

    if form_id == "MH":
        emit(subject, vid, form_id, rec, "MHTERM", random.choice(MH_TERMS))
        emit(subject, vid, form_id, rec, "MHSTDAT",
             (consent - timedelta(days=random.randint(200, 3000))).isoformat())
        emit(subject, vid, form_id, rec, "MHONGO", random.choice(["Y", "N"]))
        return

    if form_id == "EX":
        emit(subject, vid, form_id, rec, "EXSTDAT", vdate.isoformat())
        emit(subject, vid, form_id, rec, "EXDOSE",
             random.choice([50, 50, 50, 100, 100, 25]))
        adj = random.choice(["Y", "N", "N", "N"])
        if hit(DEFECT_RATES["invalid_code"]):
            adj = "YES"                  # free text where a codelist was expected
            log_defect(sid, vid, form_id, rec, "EXADJ",
                       "invalid_code", "EC-CODE")
        emit(subject, vid, form_id, rec, "EXADJ", adj)
        return

    if form_id == "AE":
        # An on-study adverse event starts on or after consent by definition —
        # anything earlier is medical history. Clamping here keeps the only
        # pre-consent events in the data the ones deliberately injected, so the
        # manifest stays exhaustive.
        start = max(consent, vdate - timedelta(days=random.randint(0, 20)))
        if hit(DEFECT_RATES["ae_before_consent"]):
            start = consent - timedelta(days=random.randint(2, 30))
            log_defect(sid, vid, form_id, rec, "AESTDAT",
                       "ae_before_consent", "EC-AE-04")
        emit(subject, vid, form_id, rec, "AETERM", random.choice(AE_TERMS))
        emit(subject, vid, form_id, rec, "AESTDAT", start.isoformat())

        outcome = random.choices(
            ["RECOVERED", "RECOVERING", "NOT RECOVERED", "FATAL", "UNKNOWN"],
            weights=[0.62, 0.14, 0.16, 0.01, 0.07])[0]
        emit(subject, vid, form_id, rec, "AEOUT", outcome)

        if outcome == "RECOVERED":
            if hit(DEFECT_RATES["recovered_no_end_date"]):
                # Outcome says resolved, but no resolution date — a soft
                # inconsistency that blocks database lock rather than the visit.
                log_defect(sid, vid, form_id, rec, "AEENDAT",
                           "recovered_no_end_date", "EC-AE-06")
            else:
                end = start + timedelta(days=random.randint(1, 21))
                if hit(DEFECT_RATES["end_before_start"]):
                    end = start - timedelta(days=random.randint(1, 5))
                    log_defect(sid, vid, form_id, rec, "AEENDAT",
                               "end_before_start", "EC-AE-01")
                emit(subject, vid, form_id, rec, "AEENDAT", end.isoformat())

        emit(subject, vid, form_id, rec, "AESEV",
             random.choices(["MILD", "MODERATE", "SEVERE"],
                            weights=[0.55, 0.35, 0.10])[0])
        serious = "Y" if (outcome == "FATAL" or random.random() < 0.06) else "N"
        emit(subject, vid, form_id, rec, "AESER", serious)
        emit(subject, vid, form_id, rec, "AEREL",
             random.choice(["NOT RELATED", "UNLIKELY", "POSSIBLE",
                            "PROBABLE", "RELATED"]))
        if serious == "Y":
            if hit(DEFECT_RATES["serious_ae_no_action"]):
                log_defect(sid, vid, form_id, rec, "AEACN",
                           "serious_ae_no_action", "EC-AE-05")
            else:
                emit(subject, vid, form_id, rec, "AEACN",
                     random.choice(["DOSE REDUCED", "DRUG WITHDRAWN",
                                    "DOSE NOT CHANGED"]))
        return

    if form_id == "CM":
        emit(subject, vid, form_id, rec, "CMTRT", random.choice(CM_TERMS))
        if random.random() < 0.8:
            emit(subject, vid, form_id, rec, "CMINDC",
                 random.choice(["Pain", "Infection", "Prophylaxis", "Hypertension"]))
        emit(subject, vid, form_id, rec, "CMSTDAT",
             (vdate - timedelta(days=random.randint(0, 60))).isoformat())
        return

    if form_id == "DS":
        completed = subject["completed"] == 1
        emit(subject, vid, form_id, rec, "DSCOMP", "Y" if completed else "N")
        if not completed:
            if hit(DEFECT_RATES["discont_no_reason"]):
                log_defect(sid, vid, form_id, rec, "DSTERM",
                           "discont_no_reason", "EC-DS-01")
            else:
                emit(subject, vid, form_id, rec, "DSTERM",
                     random.choice(DISCONT_REASONS))
        emit(subject, vid, form_id, rec, "DSSTDAT", vdate.isoformat())
        return


if __name__ == "__main__":
    main()
