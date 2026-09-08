"""
The measurement harness, tested on its own output format.

`docs/performance.md` opens by promising that every number on it was measured
and none was estimated. It shipped, once, with this in it:

    | Logical reads | 0 | 0 | n/a |
    | CPU (ms)      | 0 | 0 | n/a |
    | Elapsed (ms)  | 0 | 0 | n/a |

`SET STATISTICS IO` output arrives as informational messages on
`cursor.messages`, and pyodbc *clears that list* as it advances through result
sets. The harness drained every result set and then read the messages, by which
point there were none. Every measurement recorded zero, the generator rendered
the zeros faithfully, and nothing anywhere objected — because no test asserted
that a measurement had actually measured something.

These tests are that assertion. The parsing ones run anywhere, on captured
message text, with no database: the format is what the code depends on, so the
format is what gets pinned.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analytics.measure_performance import (  # noqa: E402
    _dedupe,
    _parse_io_and_time,
    _parse_plan,
)

RESULTS = ROOT / "docs" / "performance_results.json"

# Captured verbatim from SQL Server 2022 via pyodbc. The compile-time block is
# included on purpose: it is the trap that makes the naive regex overstate.
REAL_MESSAGES = [
    "[Microsoft][ODBC Driver 17 for SQL Server][SQL Server]SQL Server parse and "
    "compile time:     CPU time = 579 ms, elapsed time = 620 ms.",
    "[Microsoft][ODBC Driver 17 for SQL Server][SQL Server]Table 'fhir_resource'. "
    "Scan count 12, logical reads 11814, physical reads 0, page server reads 0",
    "[Microsoft][ODBC Driver 17 for SQL Server][SQL Server]  SQL Server Execution "
    "Times:    CPU time = 546 ms,  elapsed time = 95 ms.",
]


def test_logical_reads_are_parsed():
    reads, _cpu, _elapsed = _parse_io_and_time("\n".join(REAL_MESSAGES))
    assert reads == {"fhir_resource": 11814}


def test_execution_time_is_taken_not_compile_time():
    """The compile block has the same shape and, on a cold plan cache, is often
    the larger number. Reporting it would overstate every query by its compile."""
    _reads, cpu, elapsed = _parse_io_and_time("\n".join(REAL_MESSAGES))
    assert (cpu, elapsed) == (546, 95), "picked up the parse-and-compile block"


def test_reads_from_several_tables_are_summed():
    messages = [
        "Table 'FactObservation'. Scan count 5, logical reads 4200, physical reads 0",
        "Table 'DimDate'. Scan count 1, logical reads 30, physical reads 0",
        "  SQL Server Execution Times:    CPU time = 10 ms,  elapsed time = 4 ms.",
    ]
    reads, _cpu, _elapsed = _parse_io_and_time("\n".join(messages))
    assert reads == {"FactObservation": 4200, "DimDate": 30}
    assert sum(reads.values()) == 4230


def test_a_repeated_message_is_not_counted_twice():
    """Double-counting a table's reads makes a benchmark look better than it is."""
    duplicated = REAL_MESSAGES + REAL_MESSAGES
    reads, _cpu, _elapsed = _parse_io_and_time("\n".join(_dedupe(duplicated)))
    assert reads == {"fhir_resource": 11814}


def test_dedupe_preserves_order_and_drops_only_exact_repeats():
    assert _dedupe(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]


def test_no_io_output_parses_as_no_measurement_not_as_zero():
    """The distinction the harness got wrong.

    An empty reads dict is what `measure()` refuses to record. If this ever
    returns `{"": 0}` or similar, the guard stops firing and zeros ship again.
    """
    reads, cpu, elapsed = _parse_io_and_time("")
    assert reads == {}
    assert (cpu, elapsed) == (0, 0)


# ---------------------------------------------------------------------------
# Plan parsing
# ---------------------------------------------------------------------------

PLAN_SERIAL = (
    '<ShowPlanXML xmlns="http://schemas.microsoft.com/sqlserver/2004/07/showplan">'
    '<BatchSequence><Batch><Statements>'
    '<StmtSimple StatementSubTreeCost="12.5" '
    'NonParallelPlanReason="TSQLUserDefinedFunctionsNotParallelizable">'
    '<QueryPlan><RelOp PhysicalOp="Clustered Index Scan" LogicalOp="Clustered Index Scan" '
    'EstimatedTotalSubtreeCost="12.5"/></QueryPlan>'
    '</StmtSimple></Statements></Batch></BatchSequence></ShowPlanXML>'
)
PLAN_PARALLEL = (
    '<ShowPlanXML xmlns="http://schemas.microsoft.com/sqlserver/2004/07/showplan">'
    '<BatchSequence><Batch><Statements>'
    '<StmtSimple StatementSubTreeCost="3.1">'
    '<QueryPlan DegreeOfParallelism="11">'
    '<RelOp PhysicalOp="Parallelism" LogicalOp="Gather Streams" '
    'EstimatedTotalSubtreeCost="3.1"/>'
    '<RelOp PhysicalOp="Index Seek" LogicalOp="Index Seek" '
    'EstimatedTotalSubtreeCost="0.9"/></QueryPlan>'
    '</StmtSimple></Statements></Batch></BatchSequence></ShowPlanXML>'
)


def test_a_serial_plan_reports_the_optimizers_own_reason():
    """The finding that drove the largest change in the repository came from this
    attribute, so it has to survive a refactor of the parser."""
    _ops, _batch, cost, dop, reason = _parse_plan(PLAN_SERIAL)
    assert reason == "TSQLUserDefinedFunctionsNotParallelizable"
    assert dop == 1
    assert cost == 12.5


def test_a_parallel_plan_reports_its_degree():
    _ops, _batch, _cost, dop, reason = _parse_plan(PLAN_PARALLEL)
    assert dop == 11
    assert reason == ""


def test_operators_come_back_most_expensive_first():
    operators, _batch, _cost, _dop, _reason = _parse_plan(PLAN_PARALLEL)
    assert [o[0] for o in operators] == ["Parallelism", "Index Seek"]


def test_an_absent_plan_is_not_an_exception():
    assert _parse_plan("") == ([], False, 0.0, 1, "")


# ---------------------------------------------------------------------------
# The recorded results
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def results() -> dict:
    """The recorded measurement, but only where this run could have produced it.

    The committed `performance_results.json` is whatever the last machine with a
    database wrote, and the one in the repository today is the empty run this
    file exists because of. Asserting against it anywhere would make the suite
    permanently red on every workstation without SQL Server, which trains people
    to ignore it - and a test people ignore is worse than no test.

    So this is scoped to where the assertion is meaningful: the CI job that has
    a database re-runs `analytics/measure_performance.py` and rebuilds the docs
    immediately before calling pytest, so there the file IS this run's output
    and an empty measurement is a live regression.
    """
    if not RESULTS.exists():
        pytest.skip("no performance_results.json — run analytics/measure_performance.py")
    try:
        from db.connection import SqlServerUnavailable, server_description
        server_description()
    except SqlServerUnavailable:
        pytest.skip("no SQL Server here, so performance_results.json cannot be "
                    "this run's output — the assertion belongs to the job that "
                    "measures")
    except Exception:  # pragma: no cover - driver or import problems
        pytest.skip("SQL Server not reachable from this environment")
    return json.loads(RESULTS.read_text(encoding="utf-8"))


def test_every_recorded_measurement_measured_something(results):
    """The regression that shipped, caught directly.

    A recorded run with zero logical reads on both sides is not a fast query, it
    is a harness that captured nothing — and it becomes a published table of
    zeros under a heading that promises measurements.
    """
    empty = []
    for query_id, query in results["queries"].items():
        for side in ("before", "after"):
            if query[side]["logical_reads_total"] <= 0:
                empty.append(f"{query_id}.{side}")
    assert empty == [], f"measurements with no logical reads recorded: {empty}"


def test_recorded_runs_name_the_engine_and_the_row_counts(results):
    """A number without the machine and the data volume behind it is not
    reproducible by anybody, including its author."""
    assert results["server"]["product_version"]
    assert results["fact_observation_rows"] > 0
    assert results["fact_encounter_rows"] > 0


def test_before_and_after_return_the_same_rows(results):
    """A tuning change that alters the answer is not a tuning change."""
    for query_id, query in results["queries"].items():
        assert query["before"]["rows_returned"] == query["after"]["rows_returned"], (
            f"{query_id} returned a different number of rows after the change")
