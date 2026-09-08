"""
The performance figures in the README have to be the ones the harness recorded.

This file exists because of a specific failure. `docs/performance.md` shipped
with `| Logical reads | 0 | 0 | n/a |` for all three queries, under a heading
promising that every number on the page was measured and none was estimated.
Nothing objected, because nothing was comparing the document to the
measurement.

`test_performance_harness.py` catches an empty measurement where that can be
caught - in the job that has a database, because only there is the results file
this run's own output. This file guards the other direction: what the README is
allowed to say about it.

The first version of this file asserted the README quoted all twelve figures,
and CI rejected it twice for two different reasons, both correct. The repository
already has a rule that any README figure above ten thousand must appear in
metrics.json; these came from performance_results.json and were unverifiable by
it. And the effect is scale-dependent - on the committed 1.34M-row run the
covering index costs Q1 its parallelism and the query gets slower, while on
CI's 100-patient container the plan never had parallelism to lose and it simply
gets faster. Both measurements are real and neither generalises, so typing
either into prose makes the other a lie.

So the numbers live in the document the harness writes, beside the engine
build, host and row counts that produced them, and the README carries the
finding and the scale it holds at. What is asserted here is that arrangement:
that no volatile figure has crept back into the prose, that the finding and its
scale are both still stated, and that the generated document still agrees with
the measurement it was generated from.
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
    """Whatever measurement is on disk right now.

    Safe to compare the generated document against in any job, because
    `docs/build_docs.py` writes that document *from* this file - regenerate one
    and you regenerate the other, so they agree by construction and this is
    asserting that construction still holds."""
    if not RESULTS.exists():
        pytest.skip("no performance_results.json yet")
    return json.loads(RESULTS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def committed_results(results):
    """The measurement the README was written against.

    These two are not the same thing, and conflating them is what broke this
    file's first run in CI. The SQL Server job re-measures on a 100-patient
    container immediately before calling pytest, so `performance_results.json`
    there holds that run's numbers - while the README documents the committed
    measurement, taken on the native build at 1.34M observation rows. Comparing
    the prose to a fresh measurement asserts that two different warehouses
    produced identical logical reads, which is not a property anything should
    have.

    So this is the mirror image of the guard in `test_performance_harness.py`.
    That file asserts a measurement is non-empty and runs only WHERE THERE IS a
    database, because only there is the file this run's own output. This one
    asserts the prose matches the measurement and runs only where there is NOT,
    because only there is the file still the one the prose was written from.
    """
    try:
        from db.connection import SqlServerUnavailable, server_description
        server_description()
    except SqlServerUnavailable:
        return results
    except Exception:  # pragma: no cover - driver or import problems
        return results
    pytest.skip("a database is reachable, so performance_results.json may be "
                "this run's measurement rather than the one the README quotes "
                "- the prose assertion belongs to the jobs without one")


def quoted(prose, needle):
    """Match across line breaks: prose wraps, and a figure that lands either
    side of a wrap is still quoted."""
    return " ".join(needle.split()) in " ".join(prose.split())


def test_the_readme_is_the_real_one(prose):
    assert len(prose) > 10_000
    assert "## Performance" in prose


def test_the_readme_quotes_no_figure_that_a_re_measurement_would_falsify(
        committed_results, prose):
    """The README used to carry the six read counts and six elapsed times.

    It should not, for two reasons that only became visible once CI ran it. The
    repository already has a gate saying any README figure above ten thousand
    must appear in metrics.json - these came from performance_results.json, so
    they were unverifiable by its own rule. And the effect itself is
    scale-dependent: on the committed 1.34M-row run the covering index costs Q1
    its parallelism and the query gets slower, while on CI's 100-patient
    container the plan never had parallelism to lose and it simply gets faster.
    Both are real. Typing either into prose makes the other a lie.
    """
    volatile = []
    for query in committed_results["queries"].values():
        for side in ("before", "after"):
            for field in ("logical_reads_total", "elapsed_ms"):
                value = query[side][field]
                if value > 10_000 and quoted(prose, f"{value:,}"):
                    volatile.append(f"{value:,}")
    assert not volatile, (
        "the README quotes measurements that a re-measurement on different "
        f"hardware would falsify: {volatile}. They belong in the document the "
        "harness writes, beside the environment that produced them.")


def test_the_readme_states_the_finding_and_the_scale_it_holds_at(prose):
    """Removing the figures must not remove the point. The claim is that two of
    the three results disagree with themselves, and that this depends on the
    size of the warehouse - both have to survive."""
    assert quoted(prose, "Two of the three disagree with themselves at scale")
    assert quoted(prose, "1.34M observation rows")
    assert quoted(prose, "The effect is scale-dependent")
    assert quoted(prose, "100-patient container")


def test_the_committed_run_still_shows_the_direction_the_prose_claims(
        committed_results):
    """The README says two of the three disagree with themselves. That is a
    claim about the committed measurement, so it is checked against it: if a
    re-measurement on the same hardware ever flips one of those arrows, the
    sentence is wrong and has to be rewritten rather than left standing."""
    q1 = committed_results["queries"]["Q1"]
    q3 = committed_results["queries"]["Q3"]

    assert q1["after"]["logical_reads_total"] < q1["before"]["logical_reads_total"], (
        "Q1 no longer reads fewer pages; the prose about the covering index "
        "is stale")
    assert q1["after"]["elapsed_ms"] > q1["before"]["elapsed_ms"], (
        "Q1 is no longer slower; the DOP explanation no longer applies")
    assert q3["after"]["elapsed_ms"] < q3["before"]["elapsed_ms"], (
        "Q3 is no longer faster; the iTVF rewrite explanation is stale")


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
