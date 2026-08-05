"""
Every number in the README comes from `metrics.json`, and the test count comes
from the collector.

This repository's README argues that a specification which can drift from its
implementation is the most common failure in a study build. The same argument
applies to itself: a README with hand-typed figures is a second source of truth
waiting to disagree with the first, and the disagreement is always discovered by
a reader rather than by the author.

So the numbers live in `metrics.json`, written by `db/build_warehouse.py` from
the load it just performed, and these tests demand the README agrees. Change the
population and rebuild, and a stale README fails the build.

The test-count assertion is the sharpest of them, because it is the one that has
gone wrong across this whole portfolio before: the badge is compared against
`pytest --collect-only`, not against a number someone remembered.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
METRICS = ROOT / "metrics.json"
README = ROOT / "README.md"


@pytest.fixture(scope="module")
def metrics() -> dict:
    if not METRICS.exists():
        pytest.skip("metrics.json not built — run `python db/build_warehouse.py --reset`")
    return json.loads(METRICS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def readme() -> str:
    return README.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def generation_evidence(metrics: dict) -> dict:
    """The portfolio-scale generation is retained when CI rebuilds a sample."""
    return metrics.get("full_generation") or metrics["source"]


def _numbers_in(text: str) -> set[int]:
    """Every integer written in the text, with thousands separators removed."""
    return {int(match.replace(",", "")) for match in re.findall(r"\b\d[\d,]*\b", text)}


# ---------------------------------------------------------------------------
# The test count
# ---------------------------------------------------------------------------

def test_the_badge_matches_the_collector():
    """The count on the badge is whatever pytest collects, not whatever was
    true last time someone looked."""
    badge = re.search(r"badge/tests-(\d+)", README.read_text(encoding="utf-8"))
    assert badge, "README has no test-count badge"

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        capture_output=True, text=True, cwd=ROOT,
    )
    collected = re.search(r"(\d+) tests? collected", completed.stdout)
    assert collected, f"could not read a count from pytest:\n{completed.stdout[-2000:]}"

    assert int(badge.group(1)) == int(collected.group(1)), (
        f"badge says {badge.group(1)} tests, pytest collects {collected.group(1)}")


def test_the_test_count_in_the_prose_matches_the_badge(readme):
    badge = int(re.search(r"badge/tests-(\d+)", readme).group(1))
    # Without the leading word boundary, "SCD2 tests" becomes a claim that
    # the repository contains two tests.
    prose = re.findall(r"\b(\d+)\s+(?:invariants|tests)\b", readme)
    assert prose, "the README never states its test count in prose"
    mismatched = [n for n in prose if int(n) != badge]
    assert mismatched == [], (
        f"badge says {badge}, prose says {sorted(set(mismatched))}")


# ---------------------------------------------------------------------------
# The warehouse numbers
# ---------------------------------------------------------------------------

def test_metrics_has_the_shape_the_readme_depends_on(metrics):
    for section in ("source", "server", "ingest", "row_counts", "quality", "timings_seconds"):
        assert section in metrics, f"metrics.json is missing '{section}'"


def test_population_in_the_readme_matches_the_generation(generation_evidence, readme):
    population = generation_evidence["population"]
    if population is None:
        pytest.skip("no provenance recorded for this build")
    assert population in _numbers_in(readme), (
        f"the README does not mention the population actually generated ({population:,})")


def test_resource_count_in_the_readme_matches_the_generation(generation_evidence, readme):
    generated = generation_evidence.get("resources_generated")
    if not generated:
        pytest.skip("no provenance recorded")
    assert generated in _numbers_in(readme), (
        f"the README does not mention the resource count actually generated ({generated:,})")


@pytest.mark.parametrize("table", [
    "raw.fhir_resource", "norm.patient", "norm.encounter", "norm.observation",
    "dw.FactEncounter", "dw.FactObservation",
])
def test_row_counts_quoted_in_the_readme_are_real(metrics, readme, table):
    """An explicit count next to a table name must agree with metrics.json.

    Merely documenting that a table exists is not a count claim. The old test
    treated any mention of ``norm.observation`` as a promise that its current
    row count also appeared somewhere in the README.
    """
    actual = metrics["row_counts"].get(table)
    if actual is None:
        pytest.skip(f"{table} not in metrics.json")
    names = [re.escape(table), re.escape(table.split(".")[-1])]
    quoted = []
    for name in names:
        quoted += re.findall(
            rf"(?:{name})[^\n.]{{0,80}}?([\d,]+)\s+rows\b",
            readme,
            flags=re.IGNORECASE,
        )
        quoted += re.findall(
            rf"\b([\d,]+)\s+rows\b[^\n.]{{0,80}}?(?:{name})",
            readme,
            flags=re.IGNORECASE,
        )
    if not quoted:
        pytest.skip(f"{table} has no explicit row-count claim in the README")
    assert {int(value.replace(",", "")) for value in quoted} == {actual}, (
        f"README quotes {table} as {quoted}; metrics.json says {actual:,}")


def test_readme_does_not_quote_a_row_count_that_is_off_by_a_stale_build(metrics, readme):
    """The specific failure this whole file exists to prevent: a number that was
    true for a previous population and was never updated.

    Any figure in the README above ten thousand must appear somewhere in
    metrics.json. Small numbers are prose ("five sites", "two layers"); large
    ones are measurements, and a measurement that is not in metrics.json came
    from somewhere unverifiable.
    """
    metrics_numbers = _numbers_in(json.dumps(metrics))
    # Numbers that are part of the repository's fixed vocabulary rather than
    # measurements of this build.
    allowed = {
        2022, 2026, 2035, 1900, 16, 8, 4, 30, 24, 21, 11,   # versions, years, cardinalities
        13150, 49674,                                        # DimDate, both ranges
        10, 100, 1000, 10000, 250000,                        # round figures in prose
        9999979393,                                           # example NPI, not a row count
    }
    suspicious = sorted(
        n for n in _numbers_in(readme)
        if n > 10_000 and n not in metrics_numbers and n not in allowed
    )
    assert suspicious == [], (
        f"the README quotes figures that are in no metric: {suspicious}")


# ---------------------------------------------------------------------------
# Claims that must stay true
# ---------------------------------------------------------------------------

def test_conditional_reference_resolution_is_near_total(metrics):
    """The claim the README makes about provider resolution, measured."""
    encounters = metrics["row_counts"]["norm.encounter"]
    resolved = metrics["quality"]["encounters_with_resolved_provider"]
    assert resolved / encounters > 0.95, (
        f"only {resolved:,}/{encounters:,} encounters resolved a clinician")


def test_no_fact_landed_on_the_unknown_patient(metrics):
    assert metrics["quality"]["facts_on_unknown_patient"] == 0


def test_scd2_produced_more_than_one_version_for_someone(metrics):
    assert metrics["quality"]["patients_with_multiple_versions"] > 0, (
        "no patient has history — the Type 2 dimension is demonstrating nothing")
    assert metrics["quality"]["scd2_max_versions_per_patient"] >= 2


def test_both_medication_choice_branches_are_represented(metrics):
    assert metrics["quality"]["medication_orders_via_reference"] > 0


def test_multi_coded_observations_exist(metrics):
    assert metrics["quality"]["observations_with_multiple_codings"] > 0


def test_the_server_the_numbers_came_from_is_recorded(metrics):
    """A measured number without the engine it was measured on is not
    reproducible."""
    assert "SQL Server" in metrics["server"]["edition"] or metrics["server"]["edition"]
    assert metrics["server"]["product_version"]


# ---------------------------------------------------------------------------
# The honest-failure section
# ---------------------------------------------------------------------------

def test_the_honest_failure_section_survives(readme):
    """The CDM half's "bug the manifest caught" is the most valuable paragraph
    in this README. Rewrites must add to it, not replace it."""
    assert "The bug the manifest caught" in readme or "manifest caught" in readme
    assert "visit-window check" in readme, (
        "the original honest-failure story was removed rather than added to")


def test_limitations_are_stated(readme):
    lowered = readme.lower()
    for required in ("synthetic", "no epic", "certification"):
        assert required in lowered, f"the Limitations section does not mention {required!r}"


def test_stdlib_claim_is_scoped_not_global(readme):
    """The CDM pipeline is still standard-library-only and a test enforces it.
    The repository as a whole is not, and saying so unqualified would be false
    the moment the warehouse half exists.
    """
    scoping_words = ("cdm", "trial side", "pipeline", "board", "chart", "half",
                     "dashboard", "analytics")
    # Checked per paragraph, not per line: a claim and the thing it is scoped to
    # are routinely on different lines of the same sentence, and a line-based
    # check reports those as unscoped while missing a genuinely bare claim
    # sitting alone in a paragraph.
    for paragraph in readme.split("\n\n"):
        lowered = paragraph.lower()
        if "standard library" in lowered or "stdlib" in lowered:
            assert any(word in lowered for word in scoping_words), (
                f"unscoped standard-library claim:\n{paragraph.strip()[:300]}")
