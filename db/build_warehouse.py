"""
The whole warehouse, one command.

    python db/build_warehouse.py --reset

Runs: schema -> bulk ingest -> shred to norm -> load dw -> change feed ->
re-ingest -> re-shred -> re-load (which is what exercises SCD2) -> record
metrics.

Every stage is timed and every count is measured, and the result is written to
`metrics.json`. That file is the single source for every number in the README,
so a claim in the documentation cannot drift from the pipeline that produced it
— a test asserts the two agree. The CDM half of this repository has always had
this property because its numbers were small enough to keep in step by hand;
this half is large enough that it needed enforcing.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import migrate  # noqa: E402
from db.connection import (  # noqa: E402
    DEFAULT_DATABASE,
    SqlServerUnavailable,
    connect,
    server_description,
)
from fhir import changefeed  # noqa: E402
from fhir.ingest import ingest_bulk  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EXTRACT = ROOT / "data" / "synthea" / "fhir"
CHANGEFEED = ROOT / "data" / "synthea" / "changefeed"
METRICS = ROOT / "metrics.json"

# The date the initial extract is treated as landing. Fixed so that a rebuild
# produces the same effective_from values and the SCD2 assertions can name them.
INITIAL_LOAD_DATE = "2026-08-01"

COUNT_TABLES = [
    "raw.fhir_resource", "stg.ingest_rejects",
    "norm.code_system", "norm.code_concept", "norm.organization", "norm.practitioner",
    "norm.practitioner_identifier", "norm.patient", "norm.patient_address",
    "norm.encounter", "norm.condition", "norm.observation", "norm.observation_component",
    "norm.[procedure]", "norm.medication_request", "norm.resource_coding",
    "dw.DimDate", "dw.DimPatient", "dw.DimProvider", "dw.DimOrganization",
    "dw.DimDiagnosis", "dw.DimProcedure", "dw.DimObservationCode", "dw.DimMedication",
    "dw.DimEncounterType", "dw.FactEncounter", "dw.FactObservation",
    "dw.FactMedicationOrder", "dw.FactEncounterDiagnosis", "dw.FactProcedure",
]


class Timer:
    def __init__(self):
        self.stages: dict[str, float] = {}

    def stage(self, name: str):
        return _StageTimer(self, name)


class _StageTimer:
    def __init__(self, timer: Timer, name: str):
        self.timer, self.name = timer, name

    def __enter__(self):
        self.started = time.perf_counter()
        print(f"  {self.name} ...", end="", flush=True)
        return self

    def __exit__(self, *exc):
        elapsed = round(time.perf_counter() - self.started, 2)
        self.timer.stages[self.name] = elapsed
        print(f" {elapsed}s")


def run_procedures(database: str | None, statements: list[str]) -> None:
    """Run load procedures with autocommit on.

    Without it, pyodbc opens an implicit transaction and the entire shred — ten
    MERGE statements over 1.6 million resources — becomes one transaction. The
    log cannot truncate until it commits, so it grows to hold every row written,
    and a failure at the last statement rolls back the previous nine.

    Autocommit lets each statement inside the procedure commit on its own. That
    is safe here precisely because every one of them is idempotent: a load that
    dies halfway leaves a consistent prefix, and re-running it finishes the job
    rather than duplicating what already landed.
    """
    with connect(database, autocommit=True) as cn:
        cur = cn.cursor()
        # A million-row MERGE against a table with foreign keys can wait a long
        # time for a lock, and the driver's default is what turns that into
        # "Query timeout expired" on an otherwise correct load.
        cur.execute("SET LOCK_TIMEOUT 300000")
        for statement in statements:
            cur.execute(statement)
            while cur.nextset():
                pass


def table_counts(database: str | None) -> dict[str, int]:
    counts: dict[str, int] = {}
    with connect(database) as cn:
        cur = cn.cursor()
        for table in COUNT_TABLES:
            cur.execute(f"SELECT COUNT_BIG(*) FROM {table}")
            counts[table.replace("[", "").replace("]", "")] = int(cur.fetchone()[0])
    return counts


def quality_measures(database: str | None) -> dict:
    """The measured facts the README is allowed to quote."""
    queries = {
        "encounters_with_resolved_provider":
            "SELECT COUNT_BIG(*) FROM dw.FactEncounter WHERE provider_key <> -1",
        "encounters_with_resolved_organization":
            "SELECT COUNT_BIG(*) FROM dw.FactEncounter WHERE organization_key <> -1",
        "facts_on_unknown_patient":
            "SELECT COUNT_BIG(*) FROM dw.FactEncounter WHERE patient_key = -1",
        "inferred_dimension_rows":
            "SELECT (SELECT COUNT_BIG(*) FROM dw.DimProvider WHERE is_inferred = 1)"
            "     + (SELECT COUNT_BIG(*) FROM dw.DimOrganization WHERE is_inferred = 1)"
            "     + (SELECT COUNT_BIG(*) FROM dw.DimPatient WHERE is_inferred = 1)",
        "patients_with_multiple_versions":
            "SELECT COUNT_BIG(*) FROM (SELECT patient_id FROM dw.DimPatient "
            "WHERE patient_key <> -1 GROUP BY patient_id HAVING COUNT(*) > 1) AS x",
        "scd2_max_versions_per_patient":
            "SELECT COALESCE(MAX(v), 0) FROM (SELECT COUNT(*) AS v FROM dw.DimPatient "
            "WHERE patient_key <> -1 GROUP BY patient_id) AS x",
        "medication_orders_via_reference":
            "SELECT COUNT_BIG(*) FROM norm.medication_request "
            "WHERE medication_reference_id IS NOT NULL",
        "observations_with_multiple_codings":
            "SELECT COUNT_BIG(*) FROM (SELECT resource_id FROM norm.resource_coding "
            "WHERE resource_type = 'Observation' GROUP BY resource_id HAVING COUNT(*) > 1) AS x",
        "inpatient_encounters":
            "SELECT COUNT_BIG(*) FROM dw.FactEncounter WHERE is_inpatient = 1",
        "readmissions_30d":
            "SELECT COUNT_BIG(*) FROM dw.FactEncounter WHERE is_readmission_30d = 1",
        "observations_without_a_value":
            "SELECT COUNT_BIG(*) FROM norm.observation o WHERE o.value_quantity IS NULL "
            "AND o.value_code_concept_id IS NULL AND o.value_string IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM norm.observation_component c "
            "WHERE c.observation_id = o.observation_id)",
    }
    measures: dict[str, int] = {}
    with connect(database) as cn:
        cur = cn.cursor()
        for name, sql in queries.items():
            cur.execute(sql)
            measures[name] = int(cur.fetchone()[0])
    return measures


def build(database: str | None = None, reset: bool = False, extract: Path = EXTRACT,
          run_changefeed: bool = True) -> dict:
    timer = Timer()
    database = database or DEFAULT_DATABASE

    print(f"building {database}")
    with timer.stage("schema"):
        if reset:
            migrate.drop_database(database)
        migrate.apply(database, verbose=False)

    with timer.stage("ingest (bulk)"):
        initial = ingest_bulk(extract, database=database)

    with timer.stage("shred raw -> norm"):
        run_procedures(database, ["EXEC norm.usp_load_all"])

    with timer.stage("load norm -> dw"):
        run_procedures(database, [f"EXEC dw.usp_load_all @effective_date='{INITIAL_LOAD_DATE}'"])

    change_summary = None
    change_result = None
    if run_changefeed:
        with timer.stage("change feed"):
            change_summary = changefeed.generate(extract, CHANGEFEED)
        with timer.stage("ingest (change feed)"):
            change_result = ingest_bulk(CHANGEFEED, database=database)
        with timer.stage("re-shred and re-load (SCD2)"):
            run_procedures(database, [
                "EXEC norm.usp_load_all",
                f"EXEC dw.usp_load_all @effective_date='{changefeed.CHANGE_DATE}'",
            ])

    with timer.stage("measure"):
        counts = table_counts(database)
        measures = quality_measures(database)

    provenance = {}
    provenance_file = extract.parent / "provenance.json"
    if provenance_file.exists():
        provenance = json.loads(provenance_file.read_text(encoding="utf-8"))

    source = {
        "generator": provenance.get("generator", "unknown"),
        "population": provenance.get("population_requested"),
        "seed": provenance.get("seed"),
        "reference_date": provenance.get("reference_date"),
        "jar_sha256": provenance.get("jar_sha256"),
        "resources_generated": provenance.get("resources_total"),
        "resource_counts": provenance.get("resource_counts", {}),
    }

    # CI deliberately exercises a smaller population than the portfolio-scale
    # generation. Rebuilding in CI must not erase the provenance for the real
    # 10,000-patient generation, nor the dated live-HAPI observation. They are
    # independent evidence sets and are labelled as such in metrics.json.
    retained: dict = {}
    if METRICS.exists():
        try:
            retained = json.loads(METRICS.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            retained = {}

    full_generation = retained.get("full_generation")
    if (source.get("population") or 0) >= 10_000:
        full_generation = {
            **source,
            "elapsed_seconds": provenance.get("elapsed_seconds"),
            "jar_bytes": provenance.get("jar_bytes"),
            "java": provenance.get("java"),
        }

    metrics = {
        "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "full_generation": full_generation,
        "live_rest": retained.get("live_rest"),
        "source": source,
        "server": server_description(),
        "ingest": {
            "initial_read": initial.read,
            "initial_accepted": initial.accepted,
            "initial_rejected": initial.rejected,
            "initial_seconds": initial.elapsed_seconds,
            "changefeed_patients": (change_summary or {}).get("patients_changed"),
            "changefeed_accepted": (change_result.accepted if change_result else None),
        },
        "row_counts": counts,
        "quality": measures,
        "timings_seconds": timer.stages,
        "total_seconds": round(sum(timer.stages.values()), 2),
    }
    METRICS.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8", newline="\n")
    write_board_summary(metrics, database)
    return metrics


def write_board_summary(metrics: dict, database: str | None) -> Path:
    """A flat CSV for the status board to read.

    The board is drawn with the standard library only — an AST test enforces it,
    the same way it does for the CDM board — so it cannot open a database
    connection. Handing it a CSV keeps that property intact and keeps the board
    a *view* of the pipeline's output rather than a second place numbers are
    computed.
    """
    import csv

    rows = [("metric", "label", "value")]
    counts = metrics["row_counts"]
    quality = metrics["quality"]

    rows += [
        ("kpi", "resources ingested", counts["raw.fhir_resource"]),
        ("kpi", "quarantined", counts["stg.ingest_rejects"]),
        ("kpi", "fact rows", sum(v for k, v in counts.items() if k.startswith("dw.Fact"))),
        ("kpi", "patients with history", quality["patients_with_multiple_versions"]),
    ]
    for resource, norm_table, dw_table in [
        ("Patient", "norm.patient", "dw.DimPatient"),
        ("Encounter", "norm.encounter", "dw.FactEncounter"),
        ("Condition", "norm.condition", "dw.FactEncounterDiagnosis"),
        ("Observation", "norm.observation", "dw.FactObservation"),
        ("Procedure", "norm.procedure", "dw.FactProcedure"),
        ("Medication", "norm.medication_request", "dw.FactMedicationOrder"),
    ]:
        rows.append(("norm", resource, counts[norm_table]))
        rows.append(("dw", resource, counts[dw_table]))

    for stage, seconds in metrics["timings_seconds"].items():
        rows.append(("timing", stage, seconds))

    with connect(database) as cn:
        cur = cn.cursor()
        cur.execute("""
            SELECT et.care_setting, COUNT_BIG(*)
            FROM dw.FactEncounter AS f
            JOIN dw.DimEncounterType AS et ON et.encounter_type_key = f.encounter_type_key
            GROUP BY et.care_setting ORDER BY COUNT_BIG(*) DESC
        """)
        for setting, count in cur.fetchall():
            rows.append(("setting", setting, int(count)))

        cur.execute("SELECT criterion, patients FROM dw.vw_trial_feasibility "
                    "ORDER BY criterion_order")
        for criterion, patients in cur.fetchall():
            rows.append(("feasibility", criterion, int(patients)))

    path = ROOT / "output" / "warehouse_summary.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        csv.writer(handle, lineterminator="\n").writerows(rows)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the clinical warehouse end to end.")
    parser.add_argument("--database", default=None)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--extract", type=Path, default=EXTRACT)
    parser.add_argument("--no-changefeed", action="store_true")
    args = parser.parse_args(argv)

    try:
        metrics = build(args.database, args.reset, args.extract, not args.no_changefeed)
    except SqlServerUnavailable as exc:
        print(f"SQL Server unavailable: {exc}", file=sys.stderr)
        return 2

    print(f"\ntotal {metrics['total_seconds']}s")
    print(f"  raw.fhir_resource        {metrics['row_counts']['raw.fhir_resource']:>12,}")
    print(f"  dw.FactObservation       {metrics['row_counts']['dw.FactObservation']:>12,}")
    print(f"  dw.FactEncounter         {metrics['row_counts']['dw.FactEncounter']:>12,}")
    versioned = metrics["quality"]["patients_with_multiple_versions"]
    print(f"  patients with 2 versions {versioned:>12,}")
    print(f"\nwrote {METRICS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
