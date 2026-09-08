"""
The performance figures in the README have to be the ones the harness recorded.

This file exists because of a specific failure. `docs/performance.md` shipped
with `| Logical reads | 0 | 0 | n/a |` for all three queries, under a heading
promising that every number on the page was measured and none was estimated.
Nothing objected, because nothing was comparing the document to the
measurement.

`test_performance_harness.py` now catches an empty measurement where it can be
caught - in the job that has a database. This catches the other direction, and
it needs no database at all: the README quotes six read counts and six elapsed
times in its summary table, and every one of them is read back out of
`docs/performance_results.json` and looked for in the document **as formatted**,
thousands separators and all.

When a re-measurement legitimately moves a number, this fails and names the
document that needs editing - which is the point. The alternative is what
happened before: the numbers move, the document does not, and the build stays
green.
"""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
RESULTS = ROOT / "docs" / "performance_results.json"
DOC = ROOT / "docs" / "performance.md"


@pytest.fixture(scope="module")
def prose():
    return README.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def results():
    if not RESULTS.exists():
        pytest.skip("no performance_results.json yet")
    return json.loads(RESULTS.read_text(encoding="utf-8"))


def quoted(prose, needle):
    """Match across line breaks: prose wraps, and a figure that lands either
    side of a wrap is still quoted."""
    return " ".join(needle.split()) in " ".join(prose.split())


def test_the_readme_is_the_real_one(prose):
    assert len(prose) > 10_000
    assert "## Performance" in prose


def test_every_read_count_in_the_readme_is_the_recorded_one(results, prose):
    """Six numbers, one per query per side."""
    for key, query in results["queries"].items():
        for side in ("before", "after"):
            value = query[side]["logical_reads_total"]
            assert quoted(prose, f"{value:,}"), (
                f"{key}.{side} recorded {value:,} logical reads; the README "
                "does not quote it")


def test_every_elapsed_time_in_the_readme_is_the_recorded_one(results, prose):
    for key, query in results["queries"].items():
        for side in ("before", "after"):
            value = query[side]["elapsed_ms"]
            assert quoted(prose, f"{value:,}"), (
                f"{key}.{side} recorded {value:,} ms; the README does not "
                "quote it")


def test_the_readme_states_the_direction_each_query_actually_moved(results, prose):
    """The summary table is an arrow per query. If a re-measurement ever flips
    one of those arrows, the sentence explaining it is wrong and has to be
    rewritten rather than left standing beside new numbers."""
    q1, q3 = results["queries"]["Q1"], results["queries"]["Q3"]
    # Q1: far fewer reads, and slower. Q3: reads flat, and much faster.
    assert q1["after"]["logical_reads_total"] < q1["before"]["logical_reads_total"]
    assert q1["after"]["elapsed_ms"] > q1["before"]["elapsed_ms"]
    assert q3["after"]["elapsed_ms"] < q3["before"]["elapsed_ms"]
    assert quoted(prose, "Two of these three disagree with themselves")


def test_the_generated_document_agrees_with_the_recorded_measurement(results):
    """The README is not the only reader of these numbers."""
    if not DOC.exists():
        pytest.skip("docs/performance.md has not been generated")
    doc = DOC.read_text(encoding="utf-8")
    for key, query in results["queries"].items():
        for side in ("before", "after"):
            value = query[side]["logical_reads_total"]
            assert f"{value:,}" in doc, f"{key}.{side} missing from performance.md"


def test_the_document_never_typesets_an_empty_measurement(results):
    """A row of zeros under 'none of them is an estimate' reads as a result.
    The generator refuses to print one; this is the assertion behind that."""
    if not DOC.exists():
        pytest.skip("docs/performance.md has not been generated")
    doc = DOC.read_text(encoding="utf-8")
    empty = [q for q, v in results["queries"].items()
             if v["before"]["logical_reads_total"] <= 0
             and v["after"]["logical_reads_total"] <= 0]
    zero_rows = re.findall(r"^\| Logical reads \| 0 \| 0 \|", doc, re.M)
    assert not zero_rows, "performance.md typesets a measurement of nothing"
    if empty:
        assert doc.count("**Not measured.**") >= len(empty)
