"""Fast, database-free render test for the deployed Streamlit entry point."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from streamlit.testing.v1 import AppTest

from dashboard.data import load_repository_data, source_ledger

ROOT = Path(__file__).resolve().parents[1]
METRICS = ROOT / "metrics.json"


def render_without_retained_metrics() -> None:
    """The app must survive a metrics.json with the retained blocks absent.

    `full_generation` and `live_rest` are carried forward from a previous
    metrics.json by db/build_warehouse.py, which writes None when there is
    nothing to carry — so a fresh clone rebuilt at any population under 10,000
    produces both as null. Indexing them killed the deployed app outright with
    "'NoneType' object is not subscriptable", and nothing caught it: the local
    metrics.json always had the blocks, so every test passed.

    This renders the app in that state and puts the file back whatever happens.
    """
    backup = Path(tempfile.gettempdir()) / "metrics.smoke-backup.json"
    shutil.copy2(METRICS, backup)
    try:
        stripped = json.loads(METRICS.read_text(encoding="utf-8"))
        stripped["full_generation"] = None
        stripped["live_rest"] = None
        METRICS.write_text(json.dumps(stripped, indent=2) + "\n",
                           encoding="utf-8", newline="\n")

        app = AppTest.from_file(str(ROOT / "dashboard" / "app.py"), default_timeout=60)
        app.run()
        assert not app.exception, app.exception
        assert len(app.tabs) == 5
    finally:
        shutil.copy2(backup, METRICS)
        backup.unlink(missing_ok=True)


def main() -> None:
    data = load_repository_data(ROOT)
    assert len(data["subjects"]) == 120
    assert len(data["queries"]) == len(data["trial_defects"]) == 49
    assert len(data["fhir_defects"]) == 24
    assert data["sdtm_conformance"].empty
    assert source_ledger(data)["source"].nunique() == 15

    app = AppTest.from_file(str(ROOT / "dashboard" / "app.py"), default_timeout=60)
    app.run()
    assert not app.exception, app.exception
    assert len(app.tabs) == 5
    assert len(app.sidebar.multiselect) == 2

    app.sidebar.multiselect[0].set_value(["SITE-102"])
    app.run()
    assert not app.exception, app.exception
    assert len(app.tabs) == 5

    # Streamlit Community Cloud executes with dashboard/ as the working
    # directory. Exercise that exact import shape in a fresh interpreter so a
    # root-only package assumption cannot pass locally and fail after deploy.
    cloud_check = """
from streamlit.testing.v1 import AppTest
app = AppTest.from_file('app.py', default_timeout=60).run()
assert not app.exception, app.exception
assert len(app.tabs) == 5
"""
    subprocess.run(
        [sys.executable, "-c", cloud_check],
        cwd=ROOT / "dashboard",
        check=True,
        timeout=90,
    )

    render_without_retained_metrics()

    print(
        "dashboard smoke test passed: full cohort, SITE-102, Cloud import shape, "
        "and a metrics.json with no retained blocks"
    )


if __name__ == "__main__":
    main()
