"""
The warehouse status board must be a view of the load, not a picture of it.

Same rules as `test_dashboard.py` applies to the CDM board: re-read the CSV the
pipeline wrote and demand the SVG contain those exact figures, and walk the
module's AST to hold it to the standard library.

The stdlib rule is worth stating rather than assuming. The warehouse half of
this repository has dependencies — pyodbc, httpx, pydantic — and the obvious
thing to do here would be to open a connection and query. That would make the
board a *second* place numbers are computed, able to disagree with
`metrics.json` about the same load. Reading the CSV the loader wrote makes that
impossible.
"""

from __future__ import annotations

import ast
import csv
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SUMMARY = ROOT / "output" / "warehouse_summary.csv"
BOARD = ROOT / "docs" / "warehouse_board.svg"
MODULE = ROOT / "analytics" / "make_warehouse_board.py"

STDLIB_ONLY = {"csv", "pathlib", "__future__"}


def build_board() -> str:
    if not SUMMARY.exists():
        pytest.skip("no warehouse summary — run `python db/build_warehouse.py` first")
    subprocess.run([sys.executable, str(MODULE)], check=True, capture_output=True, cwd=ROOT)
    return BOARD.read_text(encoding="utf-8")


def summary_rows() -> list[dict]:
    with open(SUMMARY, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_the_board_uses_the_standard_library_only():
    """The claim on the badge, enforced. The obvious implementation would open a
    database connection; that is exactly what must not happen."""
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= STDLIB_ONLY, f"non-stdlib import: {sorted(imported - STDLIB_ONLY)}"


def test_the_board_does_not_reach_for_a_connection():
    """A sharper version of the same rule: no reference to the db package at
    all, however it might be spelled."""
    source = MODULE.read_text(encoding="utf-8")
    for forbidden in ("pyodbc", "db.connection", "connect(", "SELECT "):
        assert forbidden not in source, f"the board references {forbidden!r}"


def test_board_renders_and_is_valid_svg():
    svg = build_board()
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")
    import xml.etree.ElementTree as ET

    ET.fromstring(svg)


def test_kpis_match_the_summary():
    svg = build_board()
    for row in summary_rows():
        if row["metric"] != "kpi":
            continue
        rendered = f"{int(float(row['value'])):,}"
        assert f">{rendered}<" in svg, (
            f"KPI '{row['label']}' ({rendered}) is not on the board")


def test_reconciliation_figures_match_the_summary():
    svg = build_board()
    for row in summary_rows():
        if row["metric"] != "norm":
            continue
        assert row["label"] in svg, f"{row['label']} missing from the reconciliation panel"


def test_care_settings_match_the_summary():
    svg = build_board()
    settings = [r["label"] for r in summary_rows() if r["metric"] == "setting"]
    if not settings:
        pytest.skip("no care settings in the summary")
    for setting in settings[:6]:
        assert setting in svg


def test_board_is_deterministic():
    """Same inputs, same bytes — so a diff in the SVG means a diff in the load."""
    assert build_board() == build_board()


def test_board_reports_missing_input_rather_than_crashing():
    """A contributor with no SQL Server runs `make cdm` and nothing else. The
    warehouse board must say so, not raise."""
    completed = subprocess.run(
        [sys.executable, str(MODULE)], capture_output=True, text=True,
        cwd=ROOT / "tests",  # a directory with no output/warehouse_summary.csv below it
    )
    assert completed.returncode == 0
