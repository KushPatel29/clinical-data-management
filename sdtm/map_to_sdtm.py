"""
Map collected CRF data to SDTM-shaped submission domains, and check conformance.

SDTM (Study Data Tabulation Model) is the CDISC standard structure regulators
expect trial data in. The mapping is usually treated as an end-of-study
activity, which is why it usually goes badly: by then the CRF is locked, the
data is collected, and any structural mismatch has to be solved with
transformation logic that nobody can trace back to a collected field.

Here the mapping target is declared on every CRF item at design time
(`sdtm_target` in `crf/study_metadata.py`), so this module is a *projection* of
decisions already made and reviewed, not a reconstruction. Building DM, AE, and
VS end to end demonstrates the three structural patterns that cover most of
SDTM:

  * **DM** — one record per subject. Flat.
  * **AE** — one record per event, sequence-numbered within subject.
  * **VS** — **vertical/normalised**: one record per subject per test per visit.
    The CRF collects systolic, diastolic, pulse, temperature and weight as five
    *columns*; SDTM requires them as five *rows*, keyed by `VSTESTCD`. This
    transposition is the single most common source of SDTM mapping errors and
    the reason a wide-to-long step belongs in the specification.

Not implemented, and deliberately: full controlled-terminology validation
against CDISC CT, define.xml generation, and the SUPPQUAL domains. Those need
the published CT dictionaries; claiming them without the dictionaries would be
decoration.

Usage:
    python sdtm/map_to_sdtm.py
"""

import csv
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crf.study_metadata import FORMS, STUDY, VISITS  # noqa: E402
from dvs.edit_checks import load_records, load_subjects  # noqa: E402

OUT = ROOT / "output" / "sdtm"
OUT.mkdir(parents=True, exist_ok=True)

VISIT_LABEL = {v["visit_id"]: v["label"] for v in VISITS}
VISIT_NUM = {v["visit_id"]: v["visit_num"] for v in VISITS}

# Required variables per domain, from the SDTM Implementation Guide. Used by the
# conformance check below — an empty required variable is a submission finding,
# not a cosmetic issue.
REQUIRED = {
    "DM": ["STUDYID", "DOMAIN", "USUBJID", "SUBJID", "SITEID", "SEX", "ARMCD"],
    "AE": ["STUDYID", "DOMAIN", "USUBJID", "AESEQ", "AETERM", "AESEV", "AESER"],
    "VS": ["STUDYID", "DOMAIN", "USUBJID", "VSSEQ", "VSTESTCD", "VSTEST",
           "VSORRES"],
}

VS_TESTS = {
    "SYSBP": ("Systolic Blood Pressure", "mmHg"),
    "DIABP": ("Diastolic Blood Pressure", "mmHg"),
    "PULSE": ("Pulse Rate", "beats/min"),
    "TEMP": ("Temperature", "C"),
    "WEIGHT": ("Weight", "kg"),
}


def usubjid(subject_id):
    """Unique subject identifier — study-prefixed, because USUBJID must be
    unique across the whole submission, not just within one study."""
    return f"{STUDY['study_id']}-{subject_id}"


def years_between(start, end):
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    return d1.year - d0.year - ((d1.month, d1.day) < (d0.month, d0.day))


def build_dm(records, subjects):
    dm_by_subject = {}
    for meta, rec in records:
        if meta["form_id"] == "DM":
            dm_by_subject[meta["subject_id"]] = rec

    rows = []
    for sid, subject in sorted(subjects.items()):
        rec = dm_by_subject.get(sid, {})
        birth = rec.get("BRTHDAT", "")
        consent = rec.get("RFICDAT", subject["consent_date"])
        rows.append({
            "STUDYID": STUDY["study_id"], "DOMAIN": "DM",
            "USUBJID": usubjid(sid), "SUBJID": sid,
            "SITEID": subject["site_id"],
            "BRTHDTC": birth,
            "AGE": years_between(birth, consent) if birth else "",
            "AGEU": "YEARS" if birth else "",
            "SEX": rec.get("SEX", ""),
            "ARMCD": rec.get("ARM", subject["arm"]),
            "ARM": "Investigational product" if rec.get("ARM", subject["arm"]) == "A"
                   else "Placebo",
            "RFICDTC": consent,
            "COUNTRY": "CAN",
        })
    return rows


def build_ae(records):
    """One record per event, sequenced within subject.

    AESEQ is assigned in a deterministic order (start date, then verbatim term)
    rather than in whatever order the records happened to be read. A sequence
    number that changes between runs makes every downstream cross-reference —
    and every regulator query about "event 3" — unreproducible.
    """
    events = defaultdict(list)
    for meta, rec in records:
        if meta["form_id"] != "AE" or not rec.get("AETERM"):
            continue
        events[meta["subject_id"]].append((meta, rec))

    rows = []
    for sid in sorted(events):
        ordered = sorted(events[sid],
                         key=lambda mr: (mr[1].get("AESTDAT", ""),
                                         mr[1].get("AETERM", ""),
                                         mr[0]["visit_id"], mr[0]["record_num"]))
        for seq, (meta, rec) in enumerate(ordered, start=1):
            rows.append({
                "STUDYID": STUDY["study_id"], "DOMAIN": "AE",
                "USUBJID": usubjid(sid), "AESEQ": seq,
                "AETERM": rec.get("AETERM", ""),
                "AEDECOD": "",     # populated by medical coding — see coding/
                "AESTDTC": rec.get("AESTDAT", ""),
                "AEENDTC": rec.get("AEENDAT", ""),
                "AESEV": rec.get("AESEV", ""),
                "AESER": rec.get("AESER", ""),
                "AEREL": rec.get("AEREL", ""),
                "AEOUT": rec.get("AEOUT", ""),
                "AEACN": rec.get("AEACN", ""),
                "VISIT": VISIT_LABEL.get(meta["visit_id"], ""),
                "VISITNUM": VISIT_NUM.get(meta["visit_id"], ""),
            })
    return rows


def build_vs(records):
    """The wide-to-long transposition: five CRF columns become five SDTM rows."""
    collected = defaultdict(list)
    for meta, rec in records:
        if meta["form_id"] != "VS":
            continue
        collected[meta["subject_id"]].append((meta, rec))

    rows = []
    for sid in sorted(collected):
        ordered = sorted(collected[sid],
                         key=lambda mr: VISIT_NUM.get(mr[0]["visit_id"], 99))
        seq = 0
        for meta, rec in ordered:
            for testcd, (test, unit) in VS_TESTS.items():
                value = rec.get(testcd, "").strip()
                if not value:
                    continue          # not collected is not the same as missing
                seq += 1
                rows.append({
                    "STUDYID": STUDY["study_id"], "DOMAIN": "VS",
                    "USUBJID": usubjid(sid), "VSSEQ": seq,
                    "VSTESTCD": testcd, "VSTEST": test,
                    "VSORRES": value, "VSORRESU": unit,
                    "VSSTRESN": value, "VSSTRESU": unit,
                    "VSDTC": rec.get("VSDAT", ""),
                    "VISIT": VISIT_LABEL.get(meta["visit_id"], ""),
                    "VISITNUM": VISIT_NUM.get(meta["visit_id"], ""),
                })
    return rows


def conformance(domain, rows):
    """Structural conformance findings, in the shape a Pinnacle 21 report gives
    them: rule, severity, count. Three rules, each a real submission finding."""
    findings = []
    for var in REQUIRED[domain]:
        missing = sum(1 for r in rows if str(r.get(var, "")).strip() == "")
        if missing:
            findings.append({"domain": domain, "rule": f"{var} is required",
                             "severity": "error", "records": missing})

    # ISO 8601 on every --DTC variable. A date that is not ISO 8601 is not a
    # date to a submission reviewer's tooling.
    for var in [v for v in rows[0] if v.endswith("DTC")]:
        bad = 0
        for r in rows:
            v = str(r.get(var, "")).strip()
            if v and not (len(v) == 10 and v[4] == "-" and v[7] == "-"):
                bad += 1
        if bad:
            findings.append({"domain": domain,
                             "rule": f"{var} must be ISO 8601 (YYYY-MM-DD)",
                             "severity": "error", "records": bad})

    # Sequence numbers unique within subject.
    seqvar = f"{domain}SEQ"
    if rows and seqvar in rows[0]:
        seen = set()
        dupes = 0
        for r in rows:
            key = (r["USUBJID"], r[seqvar])
            if key in seen:
                dupes += 1
            seen.add(key)
        if dupes:
            findings.append({"domain": domain,
                             "rule": f"{seqvar} must be unique within USUBJID",
                             "severity": "error", "records": dupes})
    return findings


def write_mapping_spec():
    """The source-to-target mapping specification, generated from the CRF
    metadata so it cannot drift from what the study actually collects."""
    lines = ["# SDTM mapping specification — SYN-2026-01", "",
             "Generated from `crf/study_metadata.py`. Every collected item "
             "declares its SDTM target at CRF design time.", "",
             "| CRF form | Item | Label | Type | SDTM target |",
             "|---|---|---|---|---|"]
    for form_id, form in FORMS.items():
        for item in form["items"]:
            lines.append(f"| {form_id} | `{item['oid']}` | {item['label']} | "
                         f"{item['type']} | `{item['sdtm_target']}` |")
    (ROOT / "output" / "sdtm_mapping_specification.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")


def main():
    records = load_records()
    subjects = load_subjects()

    domains = {"DM": build_dm(records, subjects),
               "AE": build_ae(records),
               "VS": build_vs(records)}

    all_findings = []
    for name, rows in domains.items():
        with open(OUT / f"{name.lower()}.csv", "w", newline="",
                  encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader()
            w.writerows(rows)
        all_findings += conformance(name, rows)

    with open(ROOT / "output" / "sdtm_conformance.csv", "w", newline="",
              encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["domain", "rule", "severity", "records"])
        for finding in all_findings:
            w.writerow([finding["domain"], finding["rule"],
                        finding["severity"], finding["records"]])

    write_mapping_spec()

    lines = [
        "SDTM MAPPING — SYN-2026-01",
        "=" * 48,
        *[f"  {name:<4} {len(rows):>6,} records" for name, rows in domains.items()],
        "-" * 48,
        f"Conformance findings: {len(all_findings)}",
        *[f"  [{f['severity']}] {f['domain']}: {f['rule']} ({f['records']} rec)"
          for f in all_findings],
    ]
    (ROOT / "output" / "sdtm_summary.txt").write_text("\n".join(lines) + "\n",
                                                      encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
