"""
CRF metadata for study SYN-2026-01, expressed as version-controlled definitions.

In a real trial this lives inside an EDC platform (Medidata Rave, Oracle
InForm, Veeva) and is built by clicking through a study designer. That works,
and it is also why so many studies cannot answer "what changed between
protocol amendment 2 and 3, and who approved it" without opening an audit
trail viewer.

Defining the CRF as code makes the study build reviewable the way any other
regulated artefact is: diffable, testable, and traceable to a protocol section.
Field names follow **CDASH** conventions (SDTM-aligned domain prefixes, standard
variable naming) so the collected data maps to submission domains without a
translation layer invented per study.

Scope note: this is a demonstration of the discipline, not a replacement for an
EDC. It carries three domains end to end rather than forty superficially.
"""

# ---------------------------------------------------------------------------
# Controlled terminology. Codelists live in one place because a codelist copied
# into two forms is a codelist that will disagree with itself by amendment 3.
# ---------------------------------------------------------------------------
CODELISTS = {
    "NY": [("Y", "Yes"), ("N", "No")],
    "NYU": [("Y", "Yes"), ("N", "No"), ("U", "Unknown")],
    "SEX": [("F", "Female"), ("M", "Male"), ("U", "Unknown")],
    "AESEV": [("MILD", "Mild"), ("MODERATE", "Moderate"), ("SEVERE", "Severe")],
    "AEREL": [("NOT RELATED", "Not related"), ("UNLIKELY", "Unlikely"),
              ("POSSIBLE", "Possible"), ("PROBABLE", "Probable"),
              ("RELATED", "Related")],
    "AEOUT": [("RECOVERED", "Recovered/resolved"),
              ("RECOVERING", "Recovering/resolving"),
              ("NOT RECOVERED", "Not recovered/not resolved"),
              ("RECOVERED WITH SEQUELAE", "Recovered with sequelae"),
              ("FATAL", "Fatal"), ("UNKNOWN", "Unknown")],
    "ARM": [("A", "Investigational product"), ("B", "Placebo")],
}

# ---------------------------------------------------------------------------
# Visit schedule. `window_days` is what makes a protocol-deviation check
# possible at all — a visit schedule without windows cannot be monitored.
# ---------------------------------------------------------------------------
VISITS = [
    {"visit_num": 1, "visit_id": "SCR", "label": "Screening",
     "day": -14, "window_days": 7,
     "forms": ["DM", "IE", "VS", "MH"]},
    {"visit_num": 2, "visit_id": "BASE", "label": "Baseline / Day 1",
     "day": 1, "window_days": 0,
     "forms": ["VS", "EX", "AE"]},
    {"visit_num": 3, "visit_id": "WK4", "label": "Week 4",
     "day": 28, "window_days": 3,
     "forms": ["VS", "EX", "AE", "CM"]},
    {"visit_num": 4, "visit_id": "WK12", "label": "Week 12",
     "day": 84, "window_days": 5,
     "forms": ["VS", "EX", "AE", "CM"]},
    {"visit_num": 5, "visit_id": "EOS", "label": "End of study",
     "day": 168, "window_days": 7,
     "forms": ["VS", "AE", "CM", "DS"]},
]

# ---------------------------------------------------------------------------
# Forms and items.
#
# `sdtm_target` is carried on every item from the moment the form is designed.
# Deciding the SDTM mapping at database build time rather than at submission
# time is the difference between a mapping exercise and a mapping crisis.
# ---------------------------------------------------------------------------
FORMS = {
    "DM": {
        "label": "Demographics", "sdtm_domain": "DM", "repeating": False,
        "items": [
            {"oid": "BRTHDAT", "label": "Date of birth", "type": "date",
             "required": True, "sdtm_target": "DM.BRTHDTC"},
            {"oid": "SEX", "label": "Sex", "type": "code", "codelist": "SEX",
             "required": True, "sdtm_target": "DM.SEX"},
            {"oid": "RFICDAT", "label": "Date of informed consent",
             "type": "date", "required": True, "sdtm_target": "DM.RFICDTC"},
            {"oid": "ARM", "label": "Randomised arm", "type": "code",
             "codelist": "ARM", "required": True, "sdtm_target": "DM.ARMCD"},
        ],
    },
    "IE": {
        "label": "Inclusion / exclusion", "sdtm_domain": "IE", "repeating": False,
        "items": [
            {"oid": "IEYN", "label": "All eligibility criteria met",
             "type": "code", "codelist": "NY", "required": True,
             "sdtm_target": "IE.IEORRES"},
            {"oid": "AGEELIG", "label": "Age 18-75 inclusive", "type": "code",
             "codelist": "NY", "required": True, "sdtm_target": "IE.IEORRES"},
        ],
    },
    "VS": {
        "label": "Vital signs", "sdtm_domain": "VS", "repeating": False,
        "items": [
            {"oid": "VSDAT", "label": "Assessment date", "type": "date",
             "required": True, "sdtm_target": "VS.VSDTC"},
            {"oid": "SYSBP", "label": "Systolic BP", "type": "number",
             "unit": "mmHg", "required": True, "range": (60, 250),
             "sdtm_target": "VS.VSORRES", "sdtm_testcd": "SYSBP"},
            {"oid": "DIABP", "label": "Diastolic BP", "type": "number",
             "unit": "mmHg", "required": True, "range": (30, 150),
             "sdtm_target": "VS.VSORRES", "sdtm_testcd": "DIABP"},
            {"oid": "PULSE", "label": "Pulse rate", "type": "number",
             "unit": "beats/min", "required": True, "range": (30, 200),
             "sdtm_target": "VS.VSORRES", "sdtm_testcd": "PULSE"},
            {"oid": "TEMP", "label": "Temperature", "type": "number",
             "unit": "C", "required": False, "range": (33.0, 43.0),
             "sdtm_target": "VS.VSORRES", "sdtm_testcd": "TEMP"},
            {"oid": "WEIGHT", "label": "Weight", "type": "number",
             "unit": "kg", "required": True, "range": (30.0, 250.0),
             "sdtm_target": "VS.VSORRES", "sdtm_testcd": "WEIGHT"},
        ],
    },
    "MH": {
        "label": "Medical history", "sdtm_domain": "MH", "repeating": True,
        "items": [
            {"oid": "MHTERM", "label": "Reported condition", "type": "text",
             "required": True, "sdtm_target": "MH.MHTERM", "coded": "MedDRA"},
            {"oid": "MHSTDAT", "label": "Start date", "type": "date",
             "required": False, "sdtm_target": "MH.MHSTDTC"},
            {"oid": "MHONGO", "label": "Ongoing", "type": "code",
             "codelist": "NY", "required": True, "sdtm_target": "MH.MHENRTPT"},
        ],
    },
    "EX": {
        "label": "Study drug administration", "sdtm_domain": "EX",
        "repeating": False,
        "items": [
            {"oid": "EXSTDAT", "label": "Dose date", "type": "date",
             "required": True, "sdtm_target": "EX.EXSTDTC"},
            {"oid": "EXDOSE", "label": "Dose administered", "type": "number",
             "unit": "mg", "required": True, "range": (0, 200),
             "sdtm_target": "EX.EXDOSE"},
            {"oid": "EXADJ", "label": "Dose adjusted", "type": "code",
             "codelist": "NY", "required": True, "sdtm_target": "EX.EXADJ"},
        ],
    },
    "AE": {
        "label": "Adverse events", "sdtm_domain": "AE", "repeating": True,
        "items": [
            {"oid": "AETERM", "label": "Adverse event (verbatim)",
             "type": "text", "required": True, "sdtm_target": "AE.AETERM",
             "coded": "MedDRA"},
            {"oid": "AESTDAT", "label": "Start date", "type": "date",
             "required": True, "sdtm_target": "AE.AESTDTC"},
            {"oid": "AEENDAT", "label": "End date", "type": "date",
             "required": False, "sdtm_target": "AE.AEENDTC"},
            {"oid": "AESEV", "label": "Severity", "type": "code",
             "codelist": "AESEV", "required": True, "sdtm_target": "AE.AESEV"},
            {"oid": "AESER", "label": "Serious", "type": "code",
             "codelist": "NY", "required": True, "sdtm_target": "AE.AESER"},
            {"oid": "AEREL", "label": "Relationship to study drug",
             "type": "code", "codelist": "AEREL", "required": True,
             "sdtm_target": "AE.AEREL"},
            {"oid": "AEOUT", "label": "Outcome", "type": "code",
             "codelist": "AEOUT", "required": True, "sdtm_target": "AE.AEOUT"},
            {"oid": "AEACN", "label": "Action taken with study drug",
             "type": "text", "required": False, "sdtm_target": "AE.AEACN"},
        ],
    },
    "CM": {
        "label": "Concomitant medications", "sdtm_domain": "CM",
        "repeating": True,
        "items": [
            {"oid": "CMTRT", "label": "Medication (verbatim)", "type": "text",
             "required": True, "sdtm_target": "CM.CMTRT", "coded": "WHODrug"},
            {"oid": "CMINDC", "label": "Indication", "type": "text",
             "required": False, "sdtm_target": "CM.CMINDC"},
            {"oid": "CMSTDAT", "label": "Start date", "type": "date",
             "required": False, "sdtm_target": "CM.CMSTDTC"},
        ],
    },
    "DS": {
        "label": "Disposition", "sdtm_domain": "DS", "repeating": False,
        "items": [
            {"oid": "DSCOMP", "label": "Completed study", "type": "code",
             "codelist": "NY", "required": True, "sdtm_target": "DS.DSDECOD"},
            {"oid": "DSTERM", "label": "Reason for discontinuation",
             "type": "text", "required": False, "sdtm_target": "DS.DSTERM"},
            {"oid": "DSSTDAT", "label": "Date of completion / discontinuation",
             "type": "date", "required": True, "sdtm_target": "DS.DSSTDTC"},
        ],
    },
}

STUDY = {
    "study_id": "SYN-2026-01",
    "title": "A randomised, double-blind, placebo-controlled study "
             "(synthetic — no real trial, subjects, or product)",
    "phase": "II",
    "sites": ["SITE-101", "SITE-102", "SITE-103", "SITE-104", "SITE-105"],
}


def all_items():
    """Flatten the CRF to (form, item) pairs — the spine of the UAT plan, the
    edit-check coverage report, and the SDTM mapping specification."""
    for form_id, form in FORMS.items():
        for item in form["items"]:
            yield form_id, item


def item_index():
    return {(form_id, item["oid"]): item for form_id, item in all_items()}


def forms_for_visit(visit_id):
    for v in VISITS:
        if v["visit_id"] == visit_id:
            return v["forms"]
    raise KeyError(visit_id)
