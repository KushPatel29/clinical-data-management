"""
Measure three slow queries, fix them, and measure again.

Everything in docs/performance.md comes from this script. Nothing in it is
estimated, and no percentage in it was chosen because it sounded good — the
numbers are whatever the engine reported on the run recorded in the file, on the
hardware named in the file.

How the measurement works, and why each part is there:

  *  `SET STATISTICS IO, TIME ON` makes SQL Server emit logical reads and CPU
     and elapsed milliseconds as *informational messages*, not as a result set.
     pyodbc surfaces them on `cursor.messages`, which is the only way to get at
     them without going through the plan cache.

  *  Logical reads is the primary number, not elapsed time. Elapsed time on a
     laptop moves with whatever else the machine is doing; logical reads is a
     count of 8 KB pages the engine touched and is the same on every run for the
     same plan. A change that halves elapsed time and leaves logical reads
     alone did not make the query cheaper, it made the machine quieter.

  *  `DROPCLEANBUFFERS` before each run. Without it the second measurement of a
     query reads from a buffer pool the first measurement filled, physical reads
     go to zero, and every "after" looks faster than every "before" regardless
     of what changed. This needs sysadmin, which is fine on a Developer Edition
     instance and is skipped with a warning if it is not available.

  *  The *actual* execution plan, not the estimated one. `SET STATISTICS XML ON`
     returns the plan with real row counts after the query has run. An estimated
     plan is a prediction; the interesting failures are where prediction and
     reality disagree.

    python analytics/measure_performance.py
    python analytics/measure_performance.py --revert   # drop the indexes again
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import SqlServerUnavailable, connect, server_description  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PLAN_DIR = ROOT / "docs" / "plans"
RESULTS = ROOT / "docs" / "performance_results.json"
INDEX_DIR = ROOT / "sql" / "indexes"

SHOWPLAN_NS = "{http://schemas.microsoft.com/sqlserver/2004/07/showplan}"

# ---------------------------------------------------------------------------
# The queries.
#
# Q1 and Q2 are real reporting questions, not synthetic scans. Q3 is the load
# itself, which is where this warehouse actually spends its time. Each is paired
# with exactly ONE change, so the measurement attributes the difference to
# something specific rather than to "we tuned it".
# ---------------------------------------------------------------------------

QUERIES = {
    "Q1": {
        "title": "Result trend for one observation type over a date range",
        "question": (
            "Monthly mean and volume for a single LOINC code across a two-year "
            "window, split by patient age band — the shape behind every "
            "'is this measure drifting' tile on a clinical dashboard."
        ),
        "fix": "covering nonclustered index",
        "fix_file": "IX_FactObservation_code_date.sql",
        "sql": """
            SELECT d.month_year,
                   p.age_band,
                   COUNT_BIG(*)        AS results,
                   AVG(f.value_numeric) AS mean_value
            FROM dw.FactObservation AS f
            JOIN dw.DimDate         AS d ON d.date_key = f.effective_date_key
            JOIN dw.DimPatient      AS p ON p.patient_key = f.patient_key
            WHERE f.observation_code_key = (
                      SELECT TOP (1) observation_code_key
                      FROM dw.FactObservation
                      GROUP BY observation_code_key
                      ORDER BY COUNT_BIG(*) DESC)
              AND f.is_numeric = 1
              AND d.full_date >= '2025-01-01'
            GROUP BY d.month_year, p.age_band;
        """,
    },
    "Q2": {
        "title": "Whole-warehouse aggregate across every observation code",
        "question": (
            "Volume, mean and spread for every observation code by fiscal "
            "quarter — the extract behind a Power BI import model, which reads "
            "the whole fact table rather than a slice of it."
        ),
        "fix": "nonclustered columnstore index",
        "fix_file": "NCCI_FactObservation.sql",
        "sql": """
            SELECT c.code,
                   c.code_display,
                   d.fiscal_year,
                   d.fiscal_quarter,
                   COUNT_BIG(*)          AS results,
                   AVG(f.value_numeric)  AS mean_value,
                   MIN(f.value_numeric)  AS min_value,
                   MAX(f.value_numeric)  AS max_value
            FROM dw.FactObservation     AS f
            JOIN dw.DimObservationCode  AS c ON c.observation_code_key = f.observation_code_key
            JOIN dw.DimDate             AS d ON d.date_key = f.effective_date_key
            WHERE f.is_numeric = 1
            GROUP BY c.code, c.code_display, d.fiscal_year, d.fiscal_quarter;
        """,
    },
    "Q3": {
        "title": "Reference resolution across the whole raw table",
        "question": (
            "The shape of every shredding statement: pull a FHIR reference out "
            "of a million JSON documents and turn it into a local key. Not a "
            "reporting query — the load itself, which is where this warehouse "
            "spends most of its time. The fix here is a rewrite rather than an "
            "index, and it is the largest single change in the repository."
        ),
        "fix": "replace the scalar UDF with an inline table-valued function",
        "fix_file": "fn_reference_id_inlining.sql",
        "rewrite": True,
        "sql": """
            SELECT COUNT_BIG(*) AS resolved
            FROM raw.fhir_resource AS r
            WHERE r.resource_type = 'Observation'
              AND dbo.fn_reference_id_multi_statement(
                      JSON_VALUE(r.payload, '$.subject.reference')) IS NOT NULL;
        """,
        "sql_after": """
            SELECT COUNT_BIG(*) AS resolved
            FROM raw.fhir_resource AS r
            CROSS APPLY norm.tvf_reference_id(
                JSON_VALUE(r.payload, '$.subject.reference')) AS subj
            WHERE r.resource_type = 'Observation'
              AND subj.resource_id IS NOT NULL;
        """,
    },
}

# The multi-statement form, kept only so the "before" can be measured. It is not
# used by anything else and is dropped after the measurement.
SETUP = {
    "Q3": """
        CREATE OR ALTER FUNCTION dbo.fn_reference_id_multi_statement (@reference NVARCHAR(400))
        RETURNS VARCHAR(64) WITH SCHEMABINDING AS
        BEGIN
            IF @reference IS NULL OR CHARINDEX('?', @reference) > 0 RETURN NULL;
            RETURN CAST(RIGHT(@reference, CHARINDEX('/', REVERSE(@reference)) - 1) AS VARCHAR(64));
        END
    """,
}
TEARDOWN = {
    "Q3": "DROP FUNCTION IF EXISTS dbo.fn_reference_id_multi_statement;",
}

FIXES = {
    "Q1": """
        CREATE NONCLUSTERED INDEX IX_FactObservation_code_date
            ON dw.FactObservation (observation_code_key, effective_date_key)
            INCLUDE (patient_key, value_numeric, is_numeric);
    """,
    "Q2": """
        CREATE NONCLUSTERED COLUMNSTORE INDEX NCCI_FactObservation
            ON dw.FactObservation
               (observation_code_key, effective_date_key, patient_key,
                value_numeric, is_numeric);
    """,
    # Q3's fix is the query itself, not a DDL change.
    "Q3": "",
}

DROPS = {
    "Q1": "DROP INDEX IF EXISTS IX_FactObservation_code_date ON dw.FactObservation;",
    "Q2": "DROP INDEX IF EXISTS NCCI_FactObservation ON dw.FactObservation;",
    "Q3": "DROP FUNCTION IF EXISTS dbo.fn_reference_id_multi_statement;",
}


@dataclass
class Measurement:
    logical_reads: dict[str, int] = field(default_factory=dict)
    total_logical_reads: int = 0
    cpu_ms: int = 0
    elapsed_ms: int = 0
    wall_seconds: float = 0.0
    rows: int = 0
    plan_xml: str = ""
    operators: list[tuple[str, str, float]] = field(default_factory=list)
    used_batch_mode: bool = False
    estimated_subtree_cost: float = 0.0
    degree_of_parallelism: int = 1
    non_parallel_reason: str = ""


_IO_RE = re.compile(
    r"Table '(?P<table>[^']+)'\.\s+Scan count \d+, logical reads (?P<reads>\d+)", re.IGNORECASE)
_TIME_RE = re.compile(
    r"CPU time = (?P<cpu>\d+) ms,\s+elapsed time = (?P<elapsed>\d+) ms", re.IGNORECASE)


def _drain_messages(cur) -> str:
    """pyodbc collects informational messages here; they are not result sets."""
    text = "\n".join(str(m[1]) for m in (cur.messages or []))
    cur.messages.clear()
    return text


def _parse_io_and_time(text: str) -> tuple[dict[str, int], int, int]:
    reads = {}
    for match in _IO_RE.finditer(text):
        table = match.group("table")
        reads[table] = reads.get(table, 0) + int(match.group("reads"))
    cpu = elapsed = 0
    # The last "SQL Server Execution Times" block is the statement's own.
    for match in _TIME_RE.finditer(text):
        cpu, elapsed = int(match.group("cpu")), int(match.group("elapsed"))
    return reads, cpu, elapsed


def _parse_plan(plan_xml: str):
    """Operators in cost order, batch mode, subtree cost, DOP, and — the useful
    one — the optimizer's own stated reason for refusing to parallelise."""
    if not plan_xml:
        return [], False, 0.0, 1, ""
    root = ET.fromstring(plan_xml)
    operators: list[tuple[str, str, float]] = []
    batch_mode = False
    subtree_cost = 0.0

    for statement in root.iter(f"{SHOWPLAN_NS}StmtSimple"):
        subtree_cost = max(subtree_cost, float(statement.get("StatementSubTreeCost", 0) or 0))

    for node in root.iter(f"{SHOWPLAN_NS}RelOp"):
        physical = node.get("PhysicalOp", "")
        logical = node.get("LogicalOp", "")
        cost = float(node.get("EstimatedTotalSubtreeCost", 0) or 0)
        operators.append((physical, logical, cost))
        for run_mode in node.iter(f"{SHOWPLAN_NS}RunTimeInformation"):
            for thread in run_mode.iter(f"{SHOWPLAN_NS}RunTimeCountersPerThread"):
                if thread.get("ActualExecutionMode") == "Batch":
                    batch_mode = True
        if node.get("EstimatedExecutionMode") == "Batch":
            batch_mode = True

    dop = 1
    reason = ""
    match = re.search(r'DegreeOfParallelism="(\d+)"', plan_xml)
    if match:
        dop = int(match.group(1))
    match = re.search(r'NonParallelPlanReason="([^"]+)"', plan_xml)
    if match:
        reason = match.group(1)
        dop = 1

    operators.sort(key=lambda o: -o[2])
    return operators, batch_mode, subtree_cost, dop, reason


def measure(cur, sql: str, clear_cache: bool = True) -> Measurement:
    if clear_cache:
        try:
            cur.execute("CHECKPOINT; DBCC DROPCLEANBUFFERS WITH NO_INFOMSGS;")
            cur.messages.clear()
        except Exception as exc:  # pragma: no cover - permissions dependent
            print(f"    (cache not cleared: {exc})")
    # A cached plan built for different statistics is not the plan this
    # measurement is about.
    cur.execute("DBCC FREEPROCCACHE WITH NO_INFOMSGS;")
    cur.messages.clear()

    result = Measurement()

    # Pass 1: the actual plan. STATISTICS XML returns the plan as a result set,
    # so it cannot be collected in the same pass as the IO messages without the
    # plan row confusing the row count.
    cur.execute("SET STATISTICS XML ON;")
    cur.messages.clear()
    cur.execute(sql)
    while True:
        if cur.description and cur.description[0][0] in ("Microsoft SQL Server 2005 XML Showplan",):
            row = cur.fetchone()
            if row:
                result.plan_xml = row[0]
        elif cur.description:
            result.rows = len(cur.fetchall())
        if not cur.nextset():
            break
    cur.execute("SET STATISTICS XML OFF;")
    cur.messages.clear()

    # Pass 2: IO and time, on a cold cache again so the two passes are
    # comparable to every other measurement rather than to each other.
    if clear_cache:
        try:
            cur.execute("CHECKPOINT; DBCC DROPCLEANBUFFERS WITH NO_INFOMSGS;")
            cur.messages.clear()
        except Exception:
            pass
    cur.execute("SET STATISTICS IO ON; SET STATISTICS TIME ON;")
    cur.messages.clear()
    started = time.perf_counter()
    cur.execute(sql)
    while True:
        if cur.description:
            cur.fetchall()
        if not cur.nextset():
            break
    result.wall_seconds = round(time.perf_counter() - started, 3)
    messages = _drain_messages(cur)
    cur.execute("SET STATISTICS IO OFF; SET STATISTICS TIME OFF;")
    cur.messages.clear()

    result.logical_reads, result.cpu_ms, result.elapsed_ms = _parse_io_and_time(messages)
    result.total_logical_reads = sum(result.logical_reads.values())
    (result.operators, result.used_batch_mode, result.estimated_subtree_cost,
     result.degree_of_parallelism, result.non_parallel_reason) = _parse_plan(result.plan_xml)
    return result


def _write_plan(name: str, plan_xml: str) -> str | None:
    if not plan_xml:
        return None
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    path = PLAN_DIR / f"{name}.sqlplan"
    path.write_text(plan_xml, encoding="utf-8", newline="\n")
    return str(path.relative_to(ROOT)).replace("\\", "/")


def run(database: str | None = None, revert_first: bool = True) -> dict:
    results = {
        "measured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "server": server_description(),
        "host": {
            "platform": platform.platform(),
            "processor": platform.processor(),
            "python": platform.python_version(),
        },
        "queries": {},
    }

    with connect(database) as cn:
        cn.autocommit = True
        cur = cn.cursor()

        cur.execute("SELECT COUNT_BIG(*) FROM dw.FactObservation")
        results["fact_observation_rows"] = int(cur.fetchone()[0])
        cur.execute("SELECT COUNT_BIG(*) FROM dw.FactEncounter")
        results["fact_encounter_rows"] = int(cur.fetchone()[0])
        cur.messages.clear()

        if revert_first:
            for drop in DROPS.values():
                cur.execute(drop)
                cur.messages.clear()

        for key, spec in QUERIES.items():
            print(f"  {key}: {spec['title']}")

            if key in SETUP:
                cur.execute(SETUP[key])
                cur.messages.clear()

            print("    before ...", end="", flush=True)
            before = measure(cur, spec["sql"])
            print(f" {before.total_logical_reads:,} logical reads, {before.elapsed_ms} ms, "
                  f"DOP {before.degree_of_parallelism}")

            print(f"    applying {spec['fix']} ...", end="", flush=True)
            index_started = time.perf_counter()
            if spec.get("rewrite"):
                # The fix is the query itself, so there is no DDL to time.
                index_seconds = 0.0
            elif FIXES[key].strip():
                cur.execute(FIXES[key])
                cur.messages.clear()
                index_seconds = round(time.perf_counter() - index_started, 2)
            print(f" {index_seconds}s")

            print("    after  ...", end="", flush=True)
            after = measure(cur, spec.get("sql_after", spec["sql"]))
            print(f" {after.total_logical_reads:,} logical reads, {after.elapsed_ms} ms, "
                  f"DOP {after.degree_of_parallelism}")

            if key in TEARDOWN:
                cur.execute(TEARDOWN[key])
                cur.messages.clear()

            results["queries"][key] = {
                "title": spec["title"],
                "question": spec["question"],
                "sql": " ".join(spec["sql"].split()),
                "sql_after": " ".join(spec.get("sql_after", spec["sql"]).split()),
                "fix": spec["fix"],
                "fix_sql": " ".join(FIXES[key].split()),
                "fix_build_seconds": index_seconds,
                "before": _serialise(before, _write_plan(f"{key}_before", before.plan_xml)),
                "after": _serialise(after, _write_plan(f"{key}_after", after.plan_xml)),
            }

    RESULTS.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8", newline="\n")
    return results


def _serialise(m: Measurement, plan_path: str | None) -> dict:
    return {
        "logical_reads_total": m.total_logical_reads,
        "logical_reads_by_table": m.logical_reads,
        "cpu_ms": m.cpu_ms,
        "elapsed_ms": m.elapsed_ms,
        "wall_seconds": m.wall_seconds,
        "rows_returned": m.rows,
        "estimated_subtree_cost": round(m.estimated_subtree_cost, 4),
        "batch_mode": m.used_batch_mode,
        "degree_of_parallelism": m.degree_of_parallelism,
        "non_parallel_reason": m.non_parallel_reason,
        "top_operators": [
            {"physical": p, "logical": lg, "subtree_cost": round(c, 4)}
            for p, lg, c in m.operators[:6]
        ],
        "plan": plan_path,
    }


def write_index_files() -> None:
    """One file per index, each carrying the reason it exists.

    An index with no recorded rationale is an index nobody will ever dare drop,
    and unused indexes are not free — every one of them is written on every
    insert.
    """
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    for key, spec in QUERIES.items():
        if spec.get("rewrite"):
            continue   # no index to record
        (INDEX_DIR / spec["fix_file"]).write_text(
            f"-- {spec['fix']} for {key}: {spec['title']}\n"
            f"--\n"
            f"-- Why: {spec['question']}\n"
            f"-- Measured effect: see docs/performance.md, generated from\n"
            f"-- analytics/measure_performance.py.\n"
            f"{DROPS[key].strip()}\n"
            f"{FIXES[key].strip()}\n",
            encoding="utf-8", newline="\n",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure query performance before and after a fix.")
    parser.add_argument("--database", default=None)
    parser.add_argument("--revert", action="store_true", help="drop the indexes and exit")
    args = parser.parse_args(argv)

    try:
        if args.revert:
            with connect(args.database) as cn:
                cn.autocommit = True
                cur = cn.cursor()
                for drop in DROPS.values():
                    cur.execute(drop)
            print("indexes dropped")
            return 0

        print("measuring")
        results = run(args.database)
        write_index_files()
    except SqlServerUnavailable as exc:
        print(f"SQL Server unavailable: {exc}", file=sys.stderr)
        return 2

    print(f"\nwrote {RESULTS.relative_to(ROOT)}")
    for key, q in results["queries"].items():
        before, after = q["before"], q["after"]
        print(f"  {key}: {before['logical_reads_total']:,} -> {after['logical_reads_total']:,} "
              f"logical reads, {before['elapsed_ms']} -> {after['elapsed_ms']} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
