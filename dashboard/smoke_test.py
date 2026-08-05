"""Fast, database-free render test for the deployed Streamlit entry point."""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from dashboard.data import load_repository_data, source_ledger

ROOT = Path(__file__).resolve().parents[1]


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
    print("dashboard smoke test passed: full cohort and SITE-102 filter rendered")


if __name__ == "__main__":
    main()
