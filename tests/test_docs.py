"""
The documentation cannot drift from the schema.

`docs/data-dictionary.md`, `docs/data-map.md` and `docs/erd.md` are generated,
so they cannot go stale on their own. What can go stale is `docs/lineage.py` —
the hand-written half, holding the FHIR element paths and the reasons. These
tests hold it to the database in both directions:

  * every `norm` and `dw` column named in the lineage exists;
  * every `norm` and `dw` column in the database is named in the lineage.

The second direction is the one that matters. Without it, adding a column and
forgetting to document it is invisible, which is how every data dictionary in
every organisation ends up describing the schema of two years ago.

There is also a set of link and consistency checks over the Markdown itself,
because a document full of broken anchors is a document nobody reads twice.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db.connection import available, connect  # noqa: E402
from docs.lineage import MAPPINGS, UNMAPPED_ALLOWED  # noqa: E402

DOCS = ROOT / "docs"


def _lineage_columns(prefix: str) -> set[str]:
    columns = set()
    for mapping in MAPPINGS:
        for target in (mapping.norm_column, mapping.dw_column):
            if target and target.startswith(prefix) and target.count(".") == 2:
                columns.add(target.lower())
    return columns


def _lineage_tables() -> set[str]:
    tables = set()
    for mapping in MAPPINGS:
        for target in (mapping.norm_column, mapping.dw_column):
            if target:
                parts = target.split(".")
                if len(parts) >= 2:
                    tables.add(f"{parts[0]}.{parts[1]}".lower())
    return tables


# ---------------------------------------------------------------------------
# Lineage vs the database
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def db_columns():
    reachable, reason = available()
    if not reachable:
        pytest.skip(f"no SQL Server: {reason}")
    with connect() as cn:
        cur = cn.cursor()
        cur.execute("""
            SELECT LOWER(CONCAT(s.name, '.', t.name, '.', c.name))
            FROM sys.columns AS c
            JOIN sys.tables  AS t ON t.object_id = c.object_id
            JOIN sys.schemas AS s ON s.schema_id = t.schema_id
            WHERE s.name IN ('norm', 'dw')
        """)
        columns = {row[0] for row in cur.fetchall()}
    if not columns:
        pytest.skip("schema not applied — run `python db/migrate.py` first")
    return columns


@pytest.mark.warehouse
def test_every_documented_column_exists(db_columns):
    documented = _lineage_columns("norm.") | _lineage_columns("dw.")
    missing = sorted(documented - db_columns)
    assert missing == [], f"the data map names columns that do not exist: {missing}"


@pytest.mark.warehouse
def test_every_column_is_documented(db_columns):
    """The direction that catches a column added without a lineage entry."""
    documented = _lineage_columns("norm.") | _lineage_columns("dw.")
    allowed_tables = {t.lower() for t in UNMAPPED_ALLOWED}
    undocumented = sorted(
        column for column in db_columns - documented
        if ".".join(column.split(".")[:2]) not in allowed_tables
    )
    assert undocumented == [], (
        f"{len(undocumented)} columns have no entry in docs/lineage.py: {undocumented}")


@pytest.mark.warehouse
def test_lineage_tables_all_exist(db_columns):
    db_tables = {".".join(c.split(".")[:2]) for c in db_columns}
    named = {t for t in _lineage_tables() if t.split(".")[0] in ("norm", "dw")}
    missing = sorted(named - db_tables)
    assert missing == [], f"the data map names tables that do not exist: {missing}"


def test_every_mapping_names_something():
    for mapping in MAPPINGS:
        assert mapping.norm_column or mapping.dw_column, (
            f"mapping for {mapping.resource}.{mapping.fhir_path} has no target")


def test_tracked_scd2_attributes_are_marked():
    """The three Type 2 attributes must say so, because "which columns open a
    new version" is the first question anyone asks of a Type 2 dimension."""
    tracked = {"dw.dimpatient.marital_status", "dw.dimpatient.address_city",
               "dw.dimpatient.address_state", "dw.dimpatient.address_postal_code"}
    marked = {
        m.dw_column.lower() for m in MAPPINGS
        if m.dw_column and "TRACKED by SCD Type 2" in m.note
    }
    assert tracked <= marked, f"untracked in the data map: {sorted(tracked - marked)}"


# ---------------------------------------------------------------------------
# The generated documents
# ---------------------------------------------------------------------------

GENERATED = ["data-dictionary.md", "data-map.md", "erd.md"]


@pytest.mark.parametrize("name", GENERATED)
def test_generated_document_exists_and_says_so(name):
    path = DOCS / name
    if not path.exists():
        pytest.skip(f"{name} not generated yet — run `python docs/build_docs.py`")
    text = path.read_text(encoding="utf-8")
    assert "Generated by docs/build_docs.py" in text, (
        "a generated file must say it is generated, or someone will edit it")


@pytest.mark.parametrize("name", GENERATED + ["performance.md", "architecture.md"])
def test_document_has_no_broken_internal_links(name):
    """A relative link to a file that is not there is the most common rot in a
    docs directory, and the cheapest to prevent."""
    path = DOCS / name
    if not path.exists():
        pytest.skip(f"{name} not generated yet")
    text = path.read_text(encoding="utf-8")
    broken = []
    for target in re.findall(r"\]\(([^)#][^)]*)\)", text):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        resolved = (path.parent / target.split("#")[0]).resolve()
        if not resolved.exists():
            broken.append(target)
    assert broken == [], f"{name} links to missing files: {broken}"


def test_mermaid_blocks_are_balanced():
    """An unclosed fence swallows the rest of the document, and GitHub renders
    the result as one enormous code block."""
    for path in DOCS.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        assert text.count("```mermaid") <= text.count("```") - text.count("```mermaid"), (
            f"{path.name} has an unclosed mermaid fence")


def test_mermaid_avoids_the_dialect_github_rejects():
    """GitHub's mermaid renderer fails on a quoted label inside a cylinder
    shape — `[("text")]` — and reports only "Unable to render rich display",
    with no way to see the parse error. Unquoted labels render fine."""
    for path in DOCS.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        for block in re.findall(r"```mermaid\n(.*?)```", text, re.DOTALL):
            assert '[("' not in block, f"{path.name}: quoted label in a cylinder shape"
