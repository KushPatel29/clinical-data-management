"""
Generate the User Acceptance Test plan from the CRF and DVS metadata.

Before a clinical database goes live, someone has to prove it does what the
specification says — every field saves, every codelist offers the right values,
every edit check fires when it should and, just as importantly, *stays silent
when it should not*. That is UAT, and it is a regulatory expectation, not a
nicety.

UAT plans are usually written by hand, which means they are usually incomplete
in a specific way: they test that checks fire, and forget to test that checks
do not fire on valid data. A check with no negative test case will happily fire
on every record and pass its UAT.

Generating the plan from the same metadata that builds the database closes both
gaps. Coverage is complete by construction, and every check gets a positive
*and* a negative case. The plan is versioned with the study build, so an
amendment that adds a field cannot ship without a test case for it.

Usage:
    python uat/generate_uat_plan.py
"""

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crf.study_metadata import CODELISTS, FORMS, STUDY, VISITS  # noqa: E402
from dvs.edit_checks import CHECKS  # noqa: E402

OUT = ROOT / "output"
OUT.mkdir(exist_ok=True)


def build_cases():
    cases = []

    def add(category, obj, objective, steps, expected):
        cases.append({
            "test_id": f"UAT-{len(cases) + 1:03d}",
            "category": category, "object": obj, "objective": objective,
            "steps": steps, "expected_result": expected,
            "actual_result": "", "pass_fail": "", "tester": "", "date": "",
        })

    # 1. Form and field rendering, one case per collected item.
    for form_id, form in FORMS.items():
        add("Form rendering", form_id,
            f"{form['label']} form renders with all specified items",
            f"Open the {form['label']} form for a test subject",
            f"All {len(form['items'])} items are present, labelled as "
            f"specified, in the specified order")
        for item in form["items"]:
            add("Data entry", f"{form_id}.{item['oid']}",
                f"{item['label']} accepts and saves a valid value",
                f"Enter a valid {item['type']} value in {item['label']} and save",
                "Value is saved and redisplays unchanged after navigating away "
                "and returning")

    # 2. Controlled terminology — the codelist offers exactly what it should.
    for form_id, form in FORMS.items():
        for item in form["items"]:
            if item["type"] == "code":
                allowed = ", ".join(c for c, _ in CODELISTS[item["codelist"]])
                add("Controlled terminology", f"{form_id}.{item['oid']}",
                    f"{item['label']} offers only its permitted values",
                    f"Open the {item['label']} picklist",
                    f"Exactly these options are offered: {allowed}")

    # 3. Edit checks — positive and negative case for every check.
    for check in CHECKS:
        add("Edit check (positive)", check["id"],
            f"{check['id']} fires on violating data",
            f"Enter data that violates: {check['description']}",
            f"Query is raised with severity '{check['severity']}' and the "
            f"specified query text")
        # The case everyone forgets. A check that fires on everything passes a
        # positive-only UAT and then buries the sites in queries at go-live.
        add("Edit check (negative)", check["id"],
            f"{check['id']} does NOT fire on valid data",
            f"Enter valid data for the same fields",
            "No query is raised")

    # 4. Visit structure.
    for visit in VISITS:
        add("Visit structure", visit["visit_id"],
            f"{visit['label']} presents the specified forms",
            f"Navigate to {visit['label']} for a test subject",
            f"Exactly these forms are available: {', '.join(visit['forms'])}")

    # 5. Regulatory and system-level requirements.
    add("Audit trail", "System",
        "All data changes are captured in the audit trail (21 CFR Part 11)",
        "Enter a value, save, change it, save again; open the audit trail",
        "Both the original and changed value are recorded with user, "
        "timestamp, and reason for change")
    add("Access control", "System",
        "Role permissions are enforced",
        "Log in as a read-only monitor role and attempt to enter data",
        "Data entry is not permitted; the record is viewable")
    add("Query workflow", "System",
        "A query can be answered, and closed, by the correct roles",
        "Answer an open query as a site user; close it as a data manager",
        "Status transitions open -> answered -> closed, each with an audit "
        "trail entry")
    add("Extract integrity", "System",
        "The data extract matches what is displayed in the EDC",
        "Extract the study data and compare a sample of 20 records against "
        "the screen",
        "All 20 records match on every field")

    return cases


def main():
    cases = build_cases()

    with open(OUT / "uat_plan.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cases[0].keys())
        w.writeheader()
        w.writerows(cases)

    by_category = {}
    for c in cases:
        by_category[c["category"]] = by_category.get(c["category"], 0) + 1

    lines = [
        f"# User Acceptance Test Plan — {STUDY['study_id']}", "",
        f"**Study:** {STUDY['title']}", "",
        f"**Generated from:** `crf/study_metadata.py` and `dvs/edit_checks.py`. "
        f"Regenerate after every CRF or DVS change — a study build whose UAT "
        f"plan predates its specification has not been tested.", "",
        f"**Total test cases:** {len(cases)}", "",
        "| Category | Cases |", "|---|---:|",
        *[f"| {k} | {v} |" for k, v in sorted(by_category.items())],
        "", "## Coverage statement", "",
        f"- Every one of the {sum(len(f['items']) for f in FORMS.values())} "
        f"collected items has a data-entry test case.",
        f"- Every one of the {len(CHECKS)} edit checks has **both** a positive "
        f"case (fires when it should) and a negative case (stays silent when "
        f"it should). Positive-only UAT is how a check that fires on every "
        f"record reaches production.",
        f"- Every one of the {len(VISITS)} visits has a structure test case.",
        "- Audit trail, access control, query workflow, and extract integrity "
        "are covered at system level.",
        "", "## Execution", "",
        "Test cases are in `output/uat_plan.csv`, with empty `actual_result`, "
        "`pass_fail`, `tester`, and `date` columns for execution. A failed case "
        "is a build defect: it is fixed, the build is re-released, and the "
        "affected cases are re-executed — not annotated and waived.", "",
        "## Exit criteria", "",
        "1. 100% of test cases executed.",
        "2. 100% of test cases passed, or a documented and approved deviation.",
        "3. No open defects of severity 'critical' or 'major'.",
        "4. Sign-off by Data Management, the study statistician, and the sponsor.",
    ]
    (OUT / "uat_plan.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:12]))
    print(f"\nwrote {len(cases)} test cases -> output/uat_plan.csv")


if __name__ == "__main__":
    main()
