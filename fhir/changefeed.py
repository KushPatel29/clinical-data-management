"""
A second extract, in which some patients have moved or married.

Why this exists. A Synthea export is a *snapshot*: each Patient resource
carries one current address and one current marital status, with no history.
Nothing in it changes, so a Type 2 dimension loaded from it produces exactly one
version per patient and demonstrates nothing. "Tracks address and marital status
changes over time" would be a claim with no evidence behind it.

So this generates the thing a warehouse actually receives second: an incremental
feed of updated resources. It is not Synthea output and is not labelled as such
— it is written here, from the Synthea patients, and it says so. Every changed
resource gets a bumped `meta.versionId` and a `meta.lastUpdated`, which is what
a FHIR server sends when a resource is updated, and what raw.fhir_resource's
(type, id, version) key is designed to hold side by side with the original.

Like the CDM half's defect manifest, it writes down exactly what it changed, so
the SCD2 test asserts against ground truth rather than against plausibility.

    python fhir/changefeed.py --source data/synthea/fhir --every 7
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = ROOT / "data" / "synthea" / "fhir"
DEFAULT_OUTPUT = ROOT / "data" / "synthea" / "changefeed"
MANIFEST = ROOT / "data" / "patient_change_manifest.csv"

# The date the change is asserted to have happened. Fixed, not "today", because
# a test that asserts effective_from must be able to name the date.
CHANGE_DATE = "2026-08-03"
CHANGE_TIMESTAMP = f"{CHANGE_DATE}T00:00:00.000+00:00"

# Real Massachusetts municipalities, so a moved patient stays in the same health
# region as the organisations in the extract. A move to a made-up city would
# break every geographic rollup in the semantic layer.
DESTINATIONS = [
    {"city": "Worcester", "state": "Massachusetts", "postalCode": "01602"},
    {"city": "Springfield", "state": "Massachusetts", "postalCode": "01103"},
    {"city": "Lowell", "state": "Massachusetts", "postalCode": "01852"},
    {"city": "New Bedford", "state": "Massachusetts", "postalCode": "02740"},
    {"city": "Quincy", "state": "Massachusetts", "postalCode": "02169"},
]

MARITAL_STATUSES = [
    ("M", "Married"),
    ("D", "Divorced"),
    ("W", "Widowed"),
]


def _patient_files(source: Path) -> list[Path]:
    return sorted(p for p in source.glob("*.ndjson") if p.name.split(".", 1)[0] == "Patient")


def _bump_version(resource: dict) -> str:
    meta = resource.setdefault("meta", {})
    current = meta.get("versionId")
    # Synthea does not stamp a versionId at all, so the original landed as
    # version "1" (see FhirResource.version_id). The successor is "2".
    try:
        nxt = str(int(current) + 1) if current else "2"
    except ValueError:
        nxt = "2"
    meta["versionId"] = nxt
    meta["lastUpdated"] = CHANGE_TIMESTAMP
    return nxt


def generate(source: Path = DEFAULT_SOURCE, output: Path = DEFAULT_OUTPUT,
             every: int = 7, manifest_path: Path = MANIFEST) -> dict:
    """Emit an updated Patient for every `every`-th patient in the extract."""
    output.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    written = 0
    seen = 0

    with open(output / "Patient.ndjson", "w", encoding="utf-8", newline="\n") as out:
        for path in _patient_files(source):
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    resource = json.loads(line)
                    index = seen
                    seen += 1
                    if index % every:
                        continue

                    before_address = (resource.get("address") or [{}])[0]
                    before_city = before_address.get("city")
                    before_marital = (
                        (resource.get("maritalStatus") or {}).get("text")
                    )

                    # Alternate the kind of change so the test covers a move, a
                    # marital status change, and both at once — the third being
                    # the case where a naive per-column comparison opens two
                    # versions instead of one.
                    kind = ["address", "marital", "both"][(index // every) % 3]

                    if kind in ("address", "both"):
                        destination = DESTINATIONS[(index // every) % len(DESTINATIONS)]
                        address = dict(before_address)
                        address.update(destination)
                        address.pop("extension", None)  # the geolocation no longer applies
                        resource["address"] = [address]

                    if kind in ("marital", "both"):
                        # Skip past any status the patient already holds. Without
                        # this the feed picks 'Divorced' for a patient who is
                        # already divorced, the row hash is unchanged, SCD2
                        # correctly opens no new version — and the manifest is
                        # left asserting a change that never happened. A ground
                        # truth file that overstates is worse than none: the
                        # test built on it either fails on correct behaviour or
                        # gets loosened until it stops testing anything.
                        offset = (index // every) % len(MARITAL_STATUSES)
                        for step in range(len(MARITAL_STATUSES)):
                            choice = (offset + step) % len(MARITAL_STATUSES)
                            code, display = MARITAL_STATUSES[choice]
                            if display != before_marital:
                                break
                        resource["maritalStatus"] = {
                            "coding": [{
                                "system": "http://terminology.hl7.org/CodeSystem/v3-MaritalStatus",
                                "code": code, "display": display,
                            }],
                            "text": display,
                        }

                    version = _bump_version(resource)
                    out.write(json.dumps(resource, separators=(",", ":"),
                                         ensure_ascii=False) + "\n")
                    written += 1

                    after_address = resource["address"][0] if resource.get("address") else {}
                    records.append({
                        "patient_id": resource["id"],
                        "new_version_id": version,
                        "change_date": CHANGE_DATE,
                        "change_kind": kind,
                        "city_before": before_city or "",
                        "city_after": after_address.get("city", ""),
                        "marital_before": before_marital or "",
                        "marital_after": (resource.get("maritalStatus") or {}).get("text", ""),
                    })

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "patient_id", "new_version_id", "change_date", "change_kind",
            "city_before", "city_after", "marital_before", "marital_after",
        ])
        writer.writeheader()
        writer.writerows(sorted(records, key=lambda r: r["patient_id"]))

    return {
        "patients_seen": seen,
        "patients_changed": written,
        "change_date": CHANGE_DATE,
        "output": str(output),
        "manifest": str(manifest_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a patient change feed.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--every", type=int, default=7)
    args = parser.parse_args(argv)

    summary = generate(args.source, args.output, args.every)
    print(f"change feed: {summary['patients_changed']:,} of {summary['patients_seen']:,} "
          f"patients updated on {summary['change_date']} -> {summary['output']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
