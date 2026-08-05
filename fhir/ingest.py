"""
Land FHIR resources in `raw.fhir_resource`, quarantine the ones that cannot be
loaded, and never lose a row either way.

The load is idempotent on (resource_type, resource_id, version_id) — the raw
table's primary key — and it gets there by staging into a temp table and
inserting the difference, rather than by checking each row first. Checking first
is both slower and wrong: two loaders running at once both check, both miss,
both insert, one dies.

Throughput matters here. A 10,000-patient extract is 1.6 million resources, and
a per-row INSERT at 400 rows/second is an hour and ten minutes. Batched
executemany against an unconstrained temp table, then one set-based insert, does
the same work in minutes. `fast_executemany` is enabled, and the NVARCHAR(MAX)
payload column is given an explicit input size — without it pyodbc infers the
width from the first row of a batch and truncates every longer row that follows,
which fails as a constraint violation somewhere else entirely.

    python fhir/ingest.py --source data/synthea/fhir
    python fhir/ingest.py --source data/synthea/fhir --inject-invalid 40
    python fhir/ingest.py --rest --resource Patient --count 50 --max-pages 3
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import SqlServerUnavailable, connect  # noqa: E402
from fhir import bulk  # noqa: E402
from fhir.models import FhirValidationError, validate_resource  # noqa: E402
from fhir.synthea import RESOURCE_TYPES as MODELLED_TYPES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = ROOT / "data" / "synthea" / "fhir"
DEFECT_MANIFEST = ROOT / "data" / "injected_fhir_defects.csv"

BATCH_ROWS = 2000
# Rows staged before they are drained into raw.fhir_resource and committed.
# Bounds tempdb and the transaction log, and gives the load a heartbeat.
CHUNK_ROWS = 250_000


@dataclass
class IngestResult:
    batch_id: int | None = None
    read: int = 0
    accepted: int = 0
    duplicates: int = 0
    rejected: int = 0
    rejects_by_reason: dict[str, int] = field(default_factory=dict)
    elapsed_seconds: float = 0.0

    @property
    def landed(self) -> int:
        return self.accepted + self.duplicates


# ---------------------------------------------------------------------------
# Deliberate defects
#
# The repository's standing rule is that a detection claim needs ground truth to
# be measured against — the CDM half already generates an exhaustive manifest of
# every defect it injects and demands the validation engine recover exactly that
# set. The ingestion layer earns the same treatment: without a manifest,
# "invalid resources are quarantined" is a claim tested by the absence of
# complaints. Each class below fails a different rule, so the test can assert
# the *reason* and not just the count.
# ---------------------------------------------------------------------------

DEFECT_CLASSES = (
    "malformed_json",
    "missing_required_status",
    "code_outside_value_set",
    "medication_choice_both_branches",
    "coding_absent",
    "unmodelled_resource_type",
    "malformed_id",
    "organization_without_name",
)

_DEFECT_TARGETS = {
    "malformed_json": None,                       # any resource
    "missing_required_status": "Observation",
    "code_outside_value_set": "Patient",
    "medication_choice_both_branches": "MedicationRequest",
    "coding_absent": "Condition",
    "unmodelled_resource_type": "Procedure",
    "malformed_id": "Encounter",
    "organization_without_name": "Organization",
}


def _apply_defect(payload: dict, defect_class: str) -> tuple[dict | str, str]:
    """Return (corrupted payload or raw text, the reason fragment expected back)."""
    corrupted = json.loads(json.dumps(payload))
    if defect_class == "malformed_json":
        text = json.dumps(corrupted)
        return text[: max(20, len(text) // 2)], "not valid JSON"
    if defect_class == "missing_required_status":
        corrupted.pop("status", None)
        return corrupted, "status"
    if defect_class == "code_outside_value_set":
        corrupted["gender"] = "M"
        return corrupted, "value set"
    if defect_class == "medication_choice_both_branches":
        corrupted["medicationCodeableConcept"] = {
            "coding": [{"system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                        "code": "999999", "display": "Injected duplicate branch"}]
        }
        corrupted["medicationReference"] = {"reference": "Medication/injected"}
        return corrupted, "medication[x]"
    if defect_class == "coding_absent":
        corrupted.setdefault("code", {})["coding"] = []
        return corrupted, "coding"
    if defect_class == "unmodelled_resource_type":
        corrupted["resourceType"] = "Provenance"
        return corrupted, "not modelled"
    if defect_class == "malformed_id":
        corrupted["id"] = "not a valid id!"
        return corrupted, "valid FHIR id"
    if defect_class == "organization_without_name":
        corrupted.pop("name", None)
        return corrupted, "org-1"
    raise ValueError(f"unknown defect class {defect_class!r}")


class DefectInjector:
    """Corrupts a deterministic sample of resources and records what it did.

    Injection is by *position*: every resource of a targeted type is counted as
    it streams past, and the ones whose index is in the chosen set get corrupted.
    That keeps it deterministic without holding the extract in memory — which at
    1.6 million resources is not optional.
    """

    def __init__(self, per_class: int):
        self.per_class = per_class
        self.records: list[dict] = []
        self._seen: dict[str, int] = {}
        # Positions are a stride, not a random sample. A random sample from a
        # wide range misses small extracts entirely — the first version drew
        # from range(4000) and injected 7 of an intended 24 into a 20-patient
        # fixture, because Patient only had 20 rows to hit. A stride of
        # len(DEFECT_CLASSES) with a per-class offset guarantees the sets are
        # disjoint (so two classes never contend for one resource) and that the
        # highest position needed is per_class * 8, which every resource type in
        # a realistic extract exceeds.
        stride = len(DEFECT_CLASSES)
        self._targets = {
            defect: {offset + 1 + i * stride for i in range(per_class)}
            for offset, defect in enumerate(DEFECT_CLASSES)
        }
        self._used: dict[str, int] = {defect: 0 for defect in DEFECT_CLASSES}

    def maybe_corrupt(self, payload: dict, source_ref: str, line_number: int | None):
        resource_type = payload.get("resourceType")
        index = self._seen.get(resource_type, 0)
        self._seen[resource_type] = index + 1

        for defect_class in DEFECT_CLASSES:
            wanted = _DEFECT_TARGETS[defect_class]
            if wanted is not None and wanted != resource_type:
                continue
            if wanted is None and resource_type != "Observation":
                continue  # malformed_json rides on Observations, the largest type
            if self._used[defect_class] >= self.per_class:
                continue
            if index not in self._targets[defect_class]:
                continue
            self._used[defect_class] += 1
            corrupted, expected = _apply_defect(payload, defect_class)
            self.records.append({
                "resource_type": resource_type,
                "resource_id": payload.get("id", ""),
                "source_ref": source_ref,
                "line_number": line_number or "",
                "defect_class": defect_class,
                "expected_reason_fragment": expected,
            })
            return corrupted
        return payload

    def write_manifest(self, path: Path = DEFECT_MANIFEST) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "resource_type", "resource_id", "source_ref", "line_number",
                "defect_class", "expected_reason_fragment",
            ])
            writer.writeheader()
            writer.writerows(sorted(
                self.records,
                key=lambda r: (r["defect_class"], str(r["resource_id"])),
            ))
        return path


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

_STAGE_DDL = """
CREATE TABLE #incoming (
    resource_type VARCHAR(32)   NOT NULL,
    resource_id   VARCHAR(64)   NOT NULL,
    version_id    VARCHAR(32)   NOT NULL,
    last_updated  DATETIME2(3)  NULL,
    payload       NVARCHAR(MAX) NOT NULL
);
"""

_REJECT_DDL = """
CREATE TABLE #rejects (
    resource_type  VARCHAR(32)   NULL,
    resource_id    VARCHAR(64)   NULL,
    source_ref     NVARCHAR(400) NOT NULL,
    source_line    INT           NULL,
    failure_stage  VARCHAR(32)   NOT NULL,
    failure_reason NVARCHAR(400) NOT NULL,
    failure_detail NVARCHAR(MAX) NULL,
    payload        NVARCHAR(MAX) NULL
);
"""


class Ingestor:
    def __init__(self, connection, batch_kind: str, source: str, source_system: str,
                 progress=None):
        self.cn = connection
        self.cur = connection.cursor()
        self.cur.fast_executemany = True
        self.source_system = source_system
        self.result = IngestResult()

        self.cur.execute(
            "INSERT meta.load_batch (batch_kind, source) OUTPUT INSERTED.batch_id VALUES (?, ?)",
            batch_kind, source,
        )
        self.result.batch_id = int(self.cur.fetchone()[0])
        self.cn.commit()

        self.cur.execute(_STAGE_DDL)
        self.cur.execute(_REJECT_DDL)
        self._pending: list[tuple] = []
        self._pending_rejects: list[tuple] = []
        self._staged = 0
        self.progress = progress

    # -- staging -----------------------------------------------------------

    def _flush_resources(self) -> None:
        if not self._pending:
            return
        import pyodbc

        # Explicit widths. pyodbc's fast_executemany sizes each parameter from
        # the first row of the batch; a 400-byte first payload followed by a
        # 40 KB one truncates silently under some drivers and raises "String
        # data, right truncation" under others. 0 means MAX.
        self.cur.setinputsizes([
            (pyodbc.SQL_VARCHAR, 32, 0),
            (pyodbc.SQL_VARCHAR, 64, 0),
            (pyodbc.SQL_VARCHAR, 32, 0),
            (pyodbc.SQL_TYPE_TIMESTAMP, 0, 0),
            (pyodbc.SQL_WVARCHAR, 0, 0),
        ])
        self.cur.executemany(
            "INSERT INTO #incoming (resource_type, resource_id, version_id, last_updated, payload)"
            " VALUES (?, ?, ?, ?, ?)",
            self._pending,
        )
        self.cur.setinputsizes(None)
        self._pending.clear()

    def _flush_rejects(self) -> None:
        if not self._pending_rejects:
            return
        import pyodbc

        self.cur.setinputsizes([
            (pyodbc.SQL_VARCHAR, 32, 0),
            (pyodbc.SQL_VARCHAR, 64, 0),
            (pyodbc.SQL_WVARCHAR, 400, 0),
            (pyodbc.SQL_INTEGER, 0, 0),
            (pyodbc.SQL_VARCHAR, 32, 0),
            (pyodbc.SQL_WVARCHAR, 400, 0),
            (pyodbc.SQL_WVARCHAR, 0, 0),
            (pyodbc.SQL_WVARCHAR, 0, 0),
        ])
        self.cur.executemany(
            "INSERT INTO #rejects (resource_type, resource_id, source_ref, source_line,"
            " failure_stage, failure_reason, failure_detail, payload) VALUES (?,?,?,?,?,?,?,?)",
            self._pending_rejects,
        )
        self.cur.setinputsizes(None)
        self._pending_rejects.clear()

    def add(self, item: bulk.ReadResource) -> None:
        self.result.read += 1

        if item.payload is None:
            self.reject(None, None, item.source_ref, item.line_number,
                        "parse", item.parse_error or "unparseable", None, item.raw_text)
            return

        try:
            model = validate_resource(item.payload)
        except FhirValidationError as exc:
            self.reject(
                item.payload.get("resourceType"),
                str(item.payload.get("id"))[:64] if item.payload.get("id") else None,
                item.source_ref, item.line_number,
                "schema", exc.reason, exc.detail, json.dumps(item.payload)[:8000],
            )
            return

        self._pending.append((
            model.resourceType,
            model.id,
            model.version_id,
            model.last_updated,
            json.dumps(item.payload, separators=(",", ":"), ensure_ascii=False),
        ))
        self._staged += 1
        if len(self._pending) >= BATCH_ROWS:
            self._flush_resources()
        if self._staged >= CHUNK_ROWS:
            self._drain_stage()
            self._staged = 0
            if self.progress:
                self.progress(self.result)

    def reject(self, resource_type, resource_id, source_ref, source_line,
               stage, reason, detail, payload) -> None:
        self.result.rejected += 1
        self.result.rejects_by_reason[reason] = self.result.rejects_by_reason.get(reason, 0) + 1
        self._pending_rejects.append((
            resource_type, resource_id, source_ref[:400], source_line,
            stage, reason[:400], detail, payload,
        ))
        if len(self._pending_rejects) >= BATCH_ROWS:
            self._flush_rejects()

    # -- commit ------------------------------------------------------------

    def _drain_stage(self) -> None:
        """Move everything staged so far into raw.fhir_resource and commit.

        Called every CHUNK_ROWS as well as at the end. The first version of this
        loader staged the whole extract before inserting anything, which on a
        1.6 million resource run meant a single transaction holding 4 GB in
        tempdb, no progress output for the duration, and a transaction log that
        could not truncate until it finished. Chunking bounds all three. The
        cost is that a failure halfway through leaves the first half loaded —
        which is fine, because the load is idempotent: re-running it inserts the
        rest and skips what is already there.
        """
        self._flush_resources()
        self._flush_rejects()
        self._merge_stage()
        self.cn.commit()

    def _merge_stage(self) -> None:
        # One resource can arrive twice inside a single batch — a Bundle that
        # includes the same Practitioner on forty encounters, or an _include
        # that returns it once per match. Deduplicate before the insert or the
        # target's primary key rejects the whole statement.
        self.cur.execute("""
            WITH deduped AS (
                SELECT *, ROW_NUMBER() OVER (
                           PARTITION BY resource_type, resource_id, version_id
                           ORDER BY (SELECT NULL)) AS rn
                FROM #incoming
            )
            INSERT raw.fhir_resource
                (resource_type, resource_id, version_id, last_updated,
                 source_system, source_ref, batch_id, payload)
            SELECT d.resource_type, d.resource_id, d.version_id, d.last_updated,
                   ?, ?, ?, d.payload
            FROM deduped AS d
            WHERE d.rn = 1
              AND NOT EXISTS (
                    SELECT 1 FROM raw.fhir_resource AS r
                    WHERE r.resource_type = d.resource_type
                      AND r.resource_id   = d.resource_id
                      AND r.version_id    = d.version_id);
        """, self.source_system, "batch", self.result.batch_id)
        inserted = self.cur.rowcount

        self.cur.execute("SELECT COUNT(*) FROM #incoming")
        staged = int(self.cur.fetchone()[0])
        self.result.accepted += inserted
        self.result.duplicates += staged - inserted

        self.cur.execute("""
            INSERT stg.ingest_rejects
                (batch_id, resource_type, resource_id, source_system, source_ref,
                 source_line, failure_stage, failure_reason, failure_detail, payload)
            SELECT ?, resource_type, resource_id, ?, source_ref, source_line,
                   failure_stage, failure_reason, failure_detail, payload
            FROM #rejects;
        """, self.result.batch_id, self.source_system)

        # TRUNCATE, not DELETE: the stage is emptied every chunk and a DELETE
        # would log every row it removed, which on 250,000 rows is most of the
        # transaction log the chunking was introduced to bound.
        self.cur.execute("TRUNCATE TABLE #incoming")
        self.cur.execute("TRUNCATE TABLE #rejects")

    def finish(self) -> IngestResult:
        self._drain_stage()
        self.cur.execute(
            "UPDATE meta.load_batch SET completed_at = SYSUTCDATETIME(), status = 'succeeded',"
            " resources_read = ?, resources_accepted = ?, resources_rejected = ?"
            " WHERE batch_id = ?",
            self.result.read, self.result.accepted, self.result.rejected, self.result.batch_id,
        )
        self.cn.commit()
        return self.result

    def fail(self, reason: str) -> None:
        try:
            self.cn.rollback()
            self.cur.execute(
                "UPDATE meta.load_batch SET completed_at = SYSUTCDATETIME(), status = 'failed'"
                " WHERE batch_id = ?", self.result.batch_id)
            self.cn.commit()
        except Exception:  # pragma: no cover - the connection may be gone
            pass


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def ingest_bulk(source: Path, *, resource_types: tuple[str, ...] | None = MODELLED_TYPES,
                inject_invalid: int = 0, limit: int | None = None,
                database: str | None = None, progress=None) -> IngestResult:
    """Load an extract directory.

    `resource_types` defaults to the eight this warehouse models, and the filter
    is applied at the *file* level so unmodelled types are never read at all.
    That is not an optimisation, it is what keeps the quarantine readable: a
    Synthea extract is 60% Claim and ExplanationOfBenefit, and accepting them
    into stg.ingest_rejects as "not modelled" buries two dozen genuine
    validation failures under three thousand rows of noise. Something a data
    manager cannot scan is something a data manager stops opening.

    Pass `resource_types=None` to load everything and see that happen.
    """
    started = time.perf_counter()
    injector = DefectInjector(inject_invalid) if inject_invalid else None

    with connect(database) as cn:
        ingestor = Ingestor(cn, "bulk", str(source), "synthea", progress=progress)
        try:
            for index, item in enumerate(bulk.iter_directory(source, resource_types)):
                if limit is not None and index >= limit:
                    break
                if injector is not None and item.payload is not None:
                    corrupted = injector.maybe_corrupt(
                        item.payload, item.source_ref, item.line_number)
                    if isinstance(corrupted, str):
                        item = bulk.ReadResource(
                            payload=None, source_ref=item.source_ref,
                            line_number=item.line_number,
                            parse_error="not valid JSON: injected truncation",
                            raw_text=corrupted,
                        )
                    else:
                        item = bulk.ReadResource(
                            payload=corrupted, source_ref=item.source_ref,
                            line_number=item.line_number,
                        )
                ingestor.add(item)
            result = ingestor.finish()
        except Exception:
            ingestor.fail("exception during ingest")
            raise

    if injector is not None:
        injector.write_manifest()
    result.elapsed_seconds = round(time.perf_counter() - started, 2)
    return result


def ingest_rest(resource_type: str, *, base_url: str | None = None, count: int = 50,
                since: str | None = None, include: list[str] | None = None,
                max_pages: int = 3, max_resources: int | None = None,
                database: str | None = None) -> tuple[IngestResult, dict]:
    from fhir.client import DEFAULT_BASE_URL, FhirClient

    started = time.perf_counter()
    base_url = base_url or DEFAULT_BASE_URL

    with connect(database) as cn:
        ingestor = Ingestor(cn, "rest", f"{base_url}/{resource_type}", "hapi")
        try:
            with FhirClient(base_url) as client:
                for resource, mode in client.search(
                    resource_type, count=count, since=since, include=include,
                    max_pages=max_pages, max_resources=max_resources,
                ):
                    ingestor.add(bulk.ReadResource(
                        payload=resource,
                        source_ref=f"{base_url}/{resource.get('resourceType')}"
                                   f"/{resource.get('id')} ({mode})",
                        line_number=None,
                    ))
                stats = client.stats
            result = ingestor.finish()
        except Exception:
            ingestor.fail("exception during REST ingest")
            raise

    result.elapsed_seconds = round(time.perf_counter() - started, 2)
    return result, {
        "requests": stats.requests, "retries": stats.retries, "pages": stats.pages,
        "matched": stats.matched, "included": stats.included,
        "status_counts": stats.status_counts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest FHIR resources into raw.fhir_resource.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--inject-invalid", type=int, default=0,
                        help="corrupt N resources per defect class and write a manifest")
    parser.add_argument("--all-types", action="store_true",
                        help="do not filter to the modelled types; every other resource"
                             " in the extract is then quarantined as not modelled")
    parser.add_argument("--rest", action="store_true", help="pull from a live FHIR server instead")
    parser.add_argument("--resource", default="Patient")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--since", default=None)
    parser.add_argument("--include", action="append", default=None)
    parser.add_argument("--max-pages", type=int, default=3)
    args = parser.parse_args(argv)

    try:
        if args.rest:
            result, stats = ingest_rest(
                args.resource, base_url=args.base_url, count=args.count,
                since=args.since, include=args.include, max_pages=args.max_pages,
            )
            print(f"REST {args.resource}: {stats['pages']} pages, "
                  f"{stats['requests']} requests, {stats['retries']} retries, "
                  f"{stats['matched']} match / {stats['included']} include")
        else:
            result = ingest_bulk(
                args.source,
                resource_types=None if args.all_types else MODELLED_TYPES,
                inject_invalid=args.inject_invalid, limit=args.limit,
                progress=lambda r: print(f'    ... {r.accepted:,} landed', flush=True))
    except SqlServerUnavailable as exc:
        print(f"SQL Server unavailable: {exc}", file=sys.stderr)
        return 2

    print(f"batch {result.batch_id}: read {result.read:,}  accepted {result.accepted:,}  "
          f"duplicate {result.duplicates:,}  rejected {result.rejected:,}  "
          f"in {result.elapsed_seconds}s")
    for reason, n in sorted(result.rejects_by_reason.items(), key=lambda kv: -kv[1])[:10]:
        print(f"    {n:>6}  {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
