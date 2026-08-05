"""
Read FHIR resources out of files.

Two shapes arrive in practice and this reads both without being told which:

  *  **NDJSON**, one resource per line, one file per resource type. This is what
     a FHIR Bulk Data Export ($export) produces and what Synthea writes with
     `exporter.fhir.bulk_data = true`.
  *  **Bundles**, a JSON document with an `entry` array. This is what a search
     interaction returns and what Synthea writes by default — one transaction
     Bundle per patient.

The reader yields `(payload, source_ref, line_number)` and never raises on a bad
line. A file with one truncated row in the middle must still deliver the other
nine hundred thousand; the bad line comes back as a payload of `None` with the
parse error attached, and the caller quarantines it. A reader that raises turns
one corrupt byte into a failed nightly load.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReadResource:
    payload: dict | None
    source_ref: str
    line_number: int | None
    parse_error: str | None = None
    raw_text: str | None = None

    @property
    def ok(self) -> bool:
        return self.payload is not None


def _iter_ndjson(path: Path) -> Iterator[ReadResource]:
    with open(path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                yield ReadResource(
                    payload=None,
                    source_ref=path.name,
                    line_number=line_number,
                    parse_error=f"not valid JSON: {exc}",
                    # Truncated, because a quarantine row is for diagnosis and a
                    # 40 MB malformed line helps nobody read it.
                    raw_text=line[:4000],
                )
                continue
            yield ReadResource(payload=payload, source_ref=path.name, line_number=line_number)


def _iter_bundle(path: Path) -> Iterator[ReadResource]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        yield ReadResource(
            payload=None, source_ref=path.name, line_number=None,
            parse_error=f"file is not valid JSON: {exc}",
        )
        return

    if document.get("resourceType") != "Bundle":
        yield ReadResource(payload=document, source_ref=path.name, line_number=None)
        return

    for index, entry in enumerate(document.get("entry") or [], start=1):
        resource = entry.get("resource") if isinstance(entry, dict) else None
        if resource is None:
            yield ReadResource(
                payload=None, source_ref=path.name, line_number=index,
                parse_error="bundle entry has no resource",
            )
            continue
        yield ReadResource(payload=resource, source_ref=f"{path.name}#{index}", line_number=index)


def read_file(path: Path) -> Iterator[ReadResource]:
    if path.suffix.lower() == ".ndjson":
        yield from _iter_ndjson(path)
    else:
        yield from _iter_bundle(path)


def iter_directory(
    directory: Path,
    resource_types: tuple[str, ...] | None = None,
) -> Iterator[ReadResource]:
    """Every resource in a Synthea (or $export) output directory.

    Filenames are matched on the prefix before the first dot, because Synthea
    timestamps the provider-level exports — `Practitioner.1785955552260.ndjson`
    — and matching on the stem would silently skip every Practitioner and
    Organization in the extract.
    """
    directory = Path(directory)
    if not directory.exists():
        raise FileNotFoundError(f"no such extract directory: {directory}")

    paths = sorted(
        list(directory.glob("*.ndjson")) + list(directory.glob("*.json")),
        # Load order matters for referential integrity: parents before children,
        # so norm's foreign keys hold at every point rather than only at the end.
        key=lambda p: (_load_order(p.name.split(".", 1)[0]), p.name),
    )
    for path in paths:
        resource_type = path.name.split(".", 1)[0]
        if resource_types is not None and resource_type not in resource_types:
            continue
        yield from read_file(path)


# Organizations and practitioners are referenced by encounters; encounters are
# referenced by everything clinical. This is the dependency order of the model,
# and any name not listed sorts last.
_ORDER = {
    "Organization": 0,
    "Practitioner": 1,
    "Patient": 2,
    "Encounter": 3,
    "Condition": 4,
    "Observation": 5,
    "Procedure": 6,
    "MedicationRequest": 7,
}


def _load_order(resource_type: str) -> int:
    return _ORDER.get(resource_type, 99)


def count_resources(directory: Path,
                    resource_types: tuple[str, ...] | None = None) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in iter_directory(directory, resource_types):
        if item.payload is None:
            counts["(unparsed)"] = counts.get("(unparsed)", 0) + 1
            continue
        resource_type = item.payload.get("resourceType", "(untyped)")
        counts[resource_type] = counts.get(resource_type, 0) + 1
    return counts
