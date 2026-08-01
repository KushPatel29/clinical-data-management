"""
The status board must be a view of the pipeline's output, not a picture of it.

A dashboard that hard-codes its own numbers is a second source of truth waiting
to disagree with the first — so these tests re-read the CSVs and demand the SVG
contain those exact figures. They also enforce the repo's standing constraint:
no third-party import, anywhere, including in the chart code.
"""

from __future__ import annotations

import ast
import collections
import csv
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "docs" / "dm_status_board.svg"

STDLIB_ONLY = {"collections", "csv", "pathlib", "__future__"}


def read(name: str) -> list[dict]:
    with open(ROOT / "output" / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_board() -> str:
    subprocess.run(
        [sys.executable, str(ROOT / "analytics" / "make_dashboard.py")],
        check=True, capture_output=True, cwd=ROOT,
    )
    return SVG.read_text(encoding="utf-8")


def test_board_renders_and_is_valid_svg():
    svg = build_board()
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")
    # Well-formed enough for a browser and for GitHub's renderer.
    import xml.etree.ElementTree as ET

    ET.fromstring(svg)


def test_open_query_counts_match_the_query_log():
    svg = build_board()
    open_q = [r for r in read("query_log.csv") if r["status"] == "open"]
    aging = collections.Counter(r["age_band"] for r in open_q)
    assert f">{len(open_q)}<" in svg, "the open-query KPI is not the log's count"
    for band, n in aging.items():
        assert band in svg
        assert f">{n}<" in svg, f"count for {band} missing from the board"


def test_coding_counts_match_the_coding_results():
    svg = build_board()
    cod = collections.Counter(r["status"] for r in read("coding_results.csv"))
    for status, n in cod.items():
        assert status in svg
        assert f">{n}<" in svg, f"coding count for {status} missing"


def test_site_close_rates_match_the_performance_table():
    svg = build_board()
    for row in read("query_site_performance.csv"):
        assert row["site_id"] in svg
        assert f">{round(100 * float(row['close_rate']))}%<" in svg


def test_reconciliation_headline_is_present():
    """The repo's central claim has to survive onto the board."""
    svg = build_board()
    assert "49 / 49" in svg
    assert "missed" in svg and "false positives" in svg


def test_dashboard_uses_the_standard_library_only():
    """
    The 'stdlib only' badge is a claim, so it gets a test. Walk the AST of the
    chart module and assert every import is a standard-library module.
    """
    src = (ROOT / "analytics" / "make_dashboard.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= STDLIB_ONLY, f"non-stdlib import: {imported - STDLIB_ONLY}"


def test_board_is_deterministic():
    """Same inputs, same bytes — so a diff in the SVG means a diff in the data."""
    first = build_board()
    second = build_board()
    assert first == second
