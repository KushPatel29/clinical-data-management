"""
Medical coding: verbatim terms to a controlled dictionary, and what fails.

Sites type what the patient said. Analysis needs a controlled vocabulary. The
gap between those two facts is medical coding — mapping free-text adverse
events to **MedDRA** and concomitant medications to **WHODrug** — and it is one
of the last genuinely manual steps in clinical data management.

This module uses a small synthetic dictionary, because MedDRA and WHODrug are
licensed and cannot be shipped. That constraint is fine for the purpose: the
interesting part was never the dictionary lookup, it is everything around it —

  * **auto-coding** on an exact normalised match, which handles the majority;
  * **synonym resolution**, so "head ache" and "tylenol" find their term;
  * **flagging what cannot be coded**, which is the actual deliverable. An
    uncoded term is a query to the site, not a gap to be quietly left blank;
  * **ambiguity**, where a verbatim term plausibly maps to more than one
    preferred term and a human — not an algorithm — has to decide.

The output a coder works from is a worklist, ranked so the terms blocking the
most records are resolved first.

Usage:
    python coding/code_terms.py
"""

import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dvs.edit_checks import load_records  # noqa: E402

OUT = ROOT / "output"
OUT.mkdir(exist_ok=True)

# A miniature MedDRA-shaped hierarchy: preferred term -> system organ class.
# Real MedDRA has ~26,000 preferred terms across 27 SOCs and five levels.
MEDDRA = {
    "Headache": "Nervous system disorders",
    "Nausea": "Gastrointestinal disorders",
    "Vomiting": "Gastrointestinal disorders",
    "Diarrhoea": "Gastrointestinal disorders",
    "Abdominal pain": "Gastrointestinal disorders",
    "Fatigue": "General disorders and administration site conditions",
    "Pyrexia": "General disorders and administration site conditions",
    "Dizziness": "Nervous system disorders",
    "Rash": "Skin and subcutaneous tissue disorders",
    "Pruritus": "Skin and subcutaneous tissue disorders",
    "Upper respiratory tract infection": "Infections and infestations",
    "Nasopharyngitis": "Infections and infestations",
    "Cough": "Respiratory, thoracic and mediastinal disorders",
    "Back pain": "Musculoskeletal and connective tissue disorders",
    "Arthralgia": "Musculoskeletal and connective tissue disorders",
    "Insomnia": "Psychiatric disorders",
    "Hypertension": "Vascular disorders",
}

# Synonyms a coder would accept without escalating. This is the list that grows
# every week of a real study.
MEDDRA_SYNONYMS = {
    "head ache": "Headache",
    "feeling sick to stomach": "Nausea",
    "tired all the time": "Fatigue",
    "sore throat and runny nose": "Nasopharyngitis",
}

# Terms that are genuinely ambiguous — a verbatim that maps to more than one
# preferred term with no way to choose from the CRF alone. These must go to a
# human, and pretending otherwise is how a safety signal gets miscoded.
MEDDRA_AMBIGUOUS = {
    "bp high": ["Hypertension", "Blood pressure increased"],
}

WHODRUG = {
    "Paracetamol": "N02BE01", "Ibuprofen": "M01AE01",
    "Amoxicillin": "J01CA04", "Omeprazole": "A02BC01",
    "Atorvastatin": "C10AA05", "Metformin": "A10BA02",
    "Salbutamol": "R03AC02",
}
WHODRUG_SYNONYMS = {"tylenol": "Paracetamol", "advil": "Ibuprofen"}


def normalise(term):
    """Lowercase, collapse whitespace, strip punctuation.

    Deliberately conservative. Aggressive normalisation (stemming, fuzzy
    matching) raises the auto-code rate and lowers the *correct* auto-code rate,
    and in safety data a confidently wrong code is worse than an honest gap.
    """
    return re.sub(r"[^a-z0-9 ]", "", term.lower().strip())


def code(term, dictionary, synonyms, ambiguous=None):
    """Returns (status, coded_term). Status is one of
    auto | synonym | ambiguous | uncoded."""
    n = normalise(term)
    for preferred in dictionary:
        if normalise(preferred) == n:
            return "auto", preferred
    if n in synonyms:
        return "synonym", synonyms[n]
    if ambiguous and n in ambiguous:
        return "ambiguous", " | ".join(ambiguous[n])
    return "uncoded", ""


def main():
    records = load_records()

    coded_rows = []
    for meta, rec in records:
        if meta["form_id"] == "AE" and rec.get("AETERM"):
            status, term = code(rec["AETERM"], MEDDRA, MEDDRA_SYNONYMS,
                                MEDDRA_AMBIGUOUS)
            coded_rows.append({
                "dictionary": "MedDRA", "subject_id": meta["subject_id"],
                "site_id": meta["site_id"], "visit_id": meta["visit_id"],
                "form_id": "AE", "record_num": meta["record_num"],
                "verbatim": rec["AETERM"], "status": status,
                "coded_term": term,
                "soc": MEDDRA.get(term, "") if status in ("auto", "synonym") else "",
            })
        if meta["form_id"] == "CM" and rec.get("CMTRT"):
            status, term = code(rec["CMTRT"], WHODRUG, WHODRUG_SYNONYMS)
            coded_rows.append({
                "dictionary": "WHODrug", "subject_id": meta["subject_id"],
                "site_id": meta["site_id"], "visit_id": meta["visit_id"],
                "form_id": "CM", "record_num": meta["record_num"],
                "verbatim": rec["CMTRT"], "status": status,
                "coded_term": term,
                "soc": WHODRUG.get(term, "") if status in ("auto", "synonym") else "",
            })

    with open(OUT / "coding_results.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=coded_rows[0].keys())
        w.writeheader()
        w.writerows(coded_rows)

    # The coder's worklist: unresolved verbatims, ranked by how many records
    # each one is blocking. Coding the term that appears 40 times before the
    # term that appears once is the whole of coding workload management.
    pending = defaultdict(lambda: {"records": 0, "status": "", "dictionary": ""})
    for r in coded_rows:
        if r["status"] in ("uncoded", "ambiguous"):
            key = (r["dictionary"], r["verbatim"])
            pending[key]["records"] += 1
            pending[key]["status"] = r["status"]
            pending[key]["dictionary"] = r["dictionary"]

    worklist = [{"dictionary": d, "verbatim": v, "status": p["status"],
                 "records_blocked": p["records"],
                 "suggested_action": ("Query site for clarification"
                                      if p["status"] == "uncoded"
                                      else "Medical monitor to select term")}
                for (d, v), p in pending.items()]
    worklist.sort(key=lambda r: -r["records_blocked"])
    with open(OUT / "coding_worklist.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=worklist[0].keys())
        w.writeheader()
        w.writerows(worklist)

    counts = Counter(r["status"] for r in coded_rows)
    total = len(coded_rows)
    auto_rate = (counts["auto"] + counts["synonym"]) / total

    lines = [
        "MEDICAL CODING — SYN-2026-01",
        "=" * 52,
        f"Terms requiring coding:   {total:>6}",
        f"  auto-coded (exact):     {counts['auto']:>6}"
        f" ({counts['auto'] / total:.1%})",
        f"  coded via synonym:      {counts['synonym']:>6}"
        f" ({counts['synonym'] / total:.1%})",
        f"  ambiguous (to monitor): {counts['ambiguous']:>6}",
        f"  uncoded (to site):      {counts['uncoded']:>6}",
        f"Auto-code rate:           {auto_rate:>6.1%}",
        "-" * 52,
        "CODING WORKLIST (ranked by records blocked)",
        *[f"  {r['records_blocked']:>3}  [{r['status']:<9}] "
          f"{r['verbatim'][:34]:<34} {r['dictionary']}"
          for r in worklist[:10]],
    ]
    (OUT / "coding_summary.txt").write_text("\n".join(lines) + "\n",
                                            encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
