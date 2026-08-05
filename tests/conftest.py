"""Rebuild the study data and every downstream artefact once per session, so
tests always run against freshly generated, mutually consistent output rather
than whatever happens to be committed."""

import csv
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def build_outputs():
    for script in (
        ROOT / "data_generator" / "generate_edc_data.py",
        ROOT / "dvs" / "edit_checks.py",
        ROOT / "dvs" / "query_management.py",
        ROOT / "sdtm" / "map_to_sdtm.py",
        ROOT / "coding" / "code_terms.py",
        ROOT / "uat" / "generate_uat_plan.py",
    ):
        subprocess.run([sys.executable, str(script)], check=True,
                       capture_output=True)


def read(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="session")
def manifest():
    return read(ROOT / "data" / "injected_defects.csv")


@pytest.fixture(scope="session")
def queries():
    return read(ROOT / "output" / "queries_raised.csv")


@pytest.fixture(scope="session")
def item_data():
    return read(ROOT / "data" / "edc_item_data.csv")


@pytest.fixture(scope="session")
def subjects():
    return read(ROOT / "data" / "subjects.csv")


# ---------------------------------------------------------------------------
# Options and markers for the warehouse half of the suite.
#
# The CDM half above runs anywhere with nothing installed. The warehouse tests
# need SQL Server, and the live-server test needs the network. Neither is a
# reason for the suite to fail on a laptop that has neither — they skip, with
# the reason printed, and CI provides both.
# ---------------------------------------------------------------------------

def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", default=False,
                     help="run tests that call the public HAPI FHIR server")


def pytest_configure(config):
    config.addinivalue_line("markers", "live: hits a real network service")
    config.addinivalue_line("markers", "warehouse: needs a reachable SQL Server")
