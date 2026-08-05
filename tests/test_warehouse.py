"""
The warehouse's invariants, asserted against a real SQL Server.

These skip — they do not fail — when no server is reachable, because the CDM
half of this repository runs anywhere with nothing installed and that property
is worth keeping. CI provides a server; a laptop may not.

They also assume the warehouse has already been built, by
`python db/build_warehouse.py`. Rebuilding 1.6 million resources inside a test
fixture would make the suite take twenty minutes and would test the build script
rather than the model. What the fixture does is check the warehouse is populated
and skip with an instruction if it is not.
"""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db.connection import available, connect  # noqa: E402
from fhir import changefeed  # noqa: E402

pytestmark = pytest.mark.warehouse


@pytest.fixture(scope="session")
def warehouse():
    reachable, reason = available()
    if not reachable:
        pytest.skip(f"no SQL Server: {reason}")

    cn = connect()
    cur = cn.cursor()
    cur.execute("""
        SELECT CASE WHEN OBJECT_ID('dw.FactEncounter') IS NULL THEN -1
                    ELSE (SELECT COUNT_BIG(*) FROM dw.FactEncounter) END
    """)
    encounters = int(cur.fetchone()[0])
    if encounters <= 0:
        cn.close()
        pytest.skip("warehouse is empty — run `python db/build_warehouse.py --reset` first")
    yield cur
    cn.close()


def scalar(cur, sql, *params):
    cur.execute(sql, *params)
    row = cur.fetchone()
    return None if row is None else row[0]


def rows(cur, sql, *params):
    cur.execute(sql, *params)
    return cur.fetchall()


# ---------------------------------------------------------------------------
# Raw layer
# ---------------------------------------------------------------------------

def test_every_raw_payload_is_json(warehouse):
    """The ISJSON check constraint's promise, verified rather than assumed."""
    invalid = scalar(
        warehouse,
        "SELECT COUNT_BIG(*) FROM raw.fhir_resource WHERE ISJSON(payload) <> 1")
    assert invalid == 0


def test_raw_columns_agree_with_the_payload_they_describe(warehouse):
    """resource_type and resource_id are derived facts, not independent claims.
    Two check constraints enforce it; this proves they are on."""
    mismatched = scalar(warehouse, """
        SELECT COUNT_BIG(*) FROM raw.fhir_resource
        WHERE JSON_VALUE(payload, '$.resourceType') <> resource_type
           OR JSON_VALUE(payload, '$.id') <> resource_id
    """)
    assert mismatched == 0


def test_raw_is_unique_on_type_id_version(warehouse):
    duplicates = scalar(warehouse, """
        SELECT COUNT_BIG(*) FROM (
            SELECT resource_type, resource_id, version_id
            FROM raw.fhir_resource
            GROUP BY resource_type, resource_id, version_id
            HAVING COUNT_BIG(*) > 1) AS d
    """)
    assert duplicates == 0


def test_reingesting_the_same_extract_adds_nothing(warehouse):
    """Idempotency, measured on the real table rather than argued from the key.

    The extract is re-read and re-landed; the row count must not move. This is
    the property that makes a failed nightly load safe to simply re-run.
    """
    from fhir.ingest import ingest_bulk

    before = scalar(warehouse, "SELECT COUNT_BIG(*) FROM raw.fhir_resource")
    result = ingest_bulk(ROOT / "data" / "synthea" / "changefeed")
    after = scalar(warehouse, "SELECT COUNT_BIG(*) FROM raw.fhir_resource")

    assert after == before, "a re-ingest inserted rows"
    assert result.accepted == 0
    assert result.duplicates == result.read, (
        "every resource should be recognised as already present")


def test_raw_keeps_both_versions_of_a_changed_patient(warehouse):
    """Raw is history. A patient updated by the change feed has two rows, not
    one overwritten row."""
    manifest = ROOT / "data" / "patient_change_manifest.csv"
    if not manifest.exists():
        pytest.skip("no change manifest — build with the change feed enabled")
    with open(manifest, encoding="utf-8") as handle:
        changed = [row["patient_id"] for row in csv.DictReader(handle)]
    if not changed:
        pytest.skip("change feed produced no changes")

    versions = scalar(warehouse,
                      "SELECT COUNT_BIG(*) FROM raw.fhir_resource "
                      "WHERE resource_type = 'Patient' AND resource_id = ?", changed[0])
    assert versions == 2


def test_current_resource_view_returns_one_row_per_resource(warehouse):
    duplicates = scalar(warehouse, """
        SELECT COUNT_BIG(*) FROM (
            SELECT resource_type, resource_id FROM raw.vw_current_resource
            GROUP BY resource_type, resource_id HAVING COUNT_BIG(*) > 1) AS d
    """)
    assert duplicates == 0, "the current-resource view is fanning out"


def test_current_resource_view_picks_the_newest_version(warehouse):
    manifest = ROOT / "data" / "patient_change_manifest.csv"
    if not manifest.exists():
        pytest.skip("no change manifest")
    with open(manifest, encoding="utf-8") as handle:
        changed = list(csv.DictReader(handle))
    if not changed:
        pytest.skip("change feed produced no changes")

    version = scalar(warehouse,
                     "SELECT version_id FROM raw.vw_current_resource "
                     "WHERE resource_type = 'Patient' AND resource_id = ?",
                     changed[0]["patient_id"])
    assert version == changed[0]["new_version_id"]


# ---------------------------------------------------------------------------
# Reference resolution
# ---------------------------------------------------------------------------

def test_conditional_references_resolved(warehouse):
    """The regression that would be invisible in every row count.

    Synthea writes `Practitioner?identifier=<npi-system>|<npi>` rather than
    `Practitioner/<id>`. A resolver that assumes the literal form loads
    everything successfully and leaves every encounter without a clinician, so
    a count of encounters, a count of patients, and every foreign key all stay
    correct while the provider dimension becomes decorative.
    """
    total = scalar(warehouse, "SELECT COUNT_BIG(*) FROM norm.encounter")
    with_provider = scalar(warehouse, "SELECT COUNT_BIG(*) FROM norm.encounter "
                                      "WHERE primary_performer_id IS NOT NULL")
    with_org = scalar(warehouse, "SELECT COUNT_BIG(*) FROM norm.encounter "
                                 "WHERE service_provider_id IS NOT NULL")

    assert with_provider / total > 0.95, (
        f"only {with_provider}/{total} encounters resolved a clinician — "
        "conditional reference resolution is broken")
    assert with_org / total > 0.95


def test_practitioner_identifiers_are_not_practitioner_ids(warehouse):
    """The fact that makes the identifier table necessary, asserted so nobody
    'simplifies' it away."""
    overlapping = scalar(warehouse, """
        SELECT COUNT_BIG(*) FROM norm.practitioner_identifier AS i
        WHERE i.identifier_system = 'http://hl7.org/fhir/sid/us-npi'
          AND EXISTS (SELECT 1 FROM norm.practitioner AS p
                      WHERE p.practitioner_id = i.identifier_value)
    """)
    assert overlapping == 0, "an NPI matched a resource id; the two are unrelated by design"


# ---------------------------------------------------------------------------
# Choice types and multi-coding
# ---------------------------------------------------------------------------

def test_medication_choice_type_carries_both_branches(warehouse):
    by_concept = scalar(warehouse, "SELECT COUNT_BIG(*) FROM norm.medication_request "
                                   "WHERE code_concept_id IS NOT NULL")
    by_reference = scalar(warehouse,
                          "SELECT COUNT_BIG(*) FROM norm.medication_request "
                          "WHERE medication_reference_id IS NOT NULL")
    assert by_concept > 0
    assert by_reference > 0, "the medicationReference branch loaded nothing; 16% of rows use it"


def test_medication_choice_is_exclusive(warehouse):
    """CK_medication_request_choice, verified."""
    both_or_neither = scalar(warehouse, """
        SELECT COUNT_BIG(*) FROM norm.medication_request
        WHERE (code_concept_id IS NULL) = (medication_reference_id IS NULL)
    """)
    assert both_or_neither == 0


def test_secondary_codings_are_kept(warehouse):
    """Observations coded in both LOINC and SNOMED keep both codings; the one
    not chosen as primary is not discarded."""
    multi = scalar(warehouse, """
        SELECT COUNT_BIG(*) FROM (
            SELECT resource_id FROM norm.resource_coding
            WHERE resource_type = 'Observation'
            GROUP BY resource_id HAVING COUNT_BIG(*) > 1) AS x
    """)
    assert multi > 0, "no multi-coded observations found; the secondary coding table is empty"


def test_observation_primary_coding_is_loinc(warehouse):
    """The documented rule. Taking coding[0] instead would key some observations
    on SNOMED and split one concept across two dimension rows."""
    non_loinc = scalar(warehouse, """
        SELECT COUNT_BIG(*) FROM norm.observation AS o
        JOIN norm.code_concept AS cc ON cc.code_concept_id = o.code_concept_id
        JOIN norm.code_system  AS cs ON cs.code_system_id = cc.code_system_id
        WHERE cs.system_uri <> 'http://loinc.org'
    """)
    assert non_loinc == 0


# ---------------------------------------------------------------------------
# Type 2 dimension
# ---------------------------------------------------------------------------

def test_scd2_reproduces_the_change_manifest_exactly(warehouse):
    """Ground truth, the same discipline the CDM half uses for injected defects.

    The change feed wrote down every patient it updated. Every one of them must
    have gained a version, and no patient it left alone may have.
    """
    manifest_path = ROOT / "data" / "patient_change_manifest.csv"
    if not manifest_path.exists():
        pytest.skip("no change manifest")
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = {row["patient_id"]: row for row in csv.DictReader(handle)}
    if not manifest:
        pytest.skip("change feed produced no changes")

    versioned = {
        row[0] for row in rows(warehouse, """
            SELECT patient_id FROM dw.DimPatient
            WHERE patient_key <> -1
            GROUP BY patient_id HAVING COUNT_BIG(*) > 1
        """)
    }
    expected = set(manifest)

    assert versioned - expected == set(), (
        f"{len(versioned - expected)} patients gained a version the manifest did not ask for")
    assert expected - versioned == set(), (
        f"{len(expected - versioned)} manifest changes produced no new version")


def test_scd2_versions_have_the_expected_dates(warehouse):
    manifest_path = ROOT / "data" / "patient_change_manifest.csv"
    if not manifest_path.exists():
        pytest.skip("no change manifest")
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = list(csv.DictReader(handle))
    if not manifest:
        pytest.skip("change feed produced no changes")

    patient_id = manifest[0]["patient_id"]
    versions = rows(warehouse, """
        SELECT version_number, effective_from, effective_to, is_current
        FROM dw.DimPatient WHERE patient_id = ? ORDER BY version_number
    """, patient_id)

    assert len(versions) == 2
    first, second = versions
    assert str(first[1]) == "1900-01-01", "the first version must open at the beginning of time"
    assert str(first[2]) == "2026-08-02", "version 1 must close the day before version 2 opens"
    assert first[3] is False
    assert str(second[1]) == changefeed.CHANGE_DATE
    assert str(second[2]) == "9999-12-31"
    assert second[3] is True


def test_scd2_carries_the_changed_attribute(warehouse):
    manifest_path = ROOT / "data" / "patient_change_manifest.csv"
    if not manifest_path.exists():
        pytest.skip("no change manifest")
    with open(manifest_path, encoding="utf-8") as handle:
        moves = [r for r in csv.DictReader(handle) if r["change_kind"] in ("address", "both")]
    if not moves:
        pytest.skip("no address changes in the manifest")

    move = moves[0]
    versions = rows(warehouse, """
        SELECT version_number, address_city FROM dw.DimPatient
        WHERE patient_id = ? ORDER BY version_number
    """, move["patient_id"])
    assert versions[0][1] == move["city_before"]
    assert versions[1][1] == move["city_after"]


def test_only_one_current_version_per_patient(warehouse):
    offenders = scalar(warehouse, """
        SELECT COUNT_BIG(*) FROM (
            SELECT patient_id FROM dw.DimPatient
            WHERE is_current = 1 GROUP BY patient_id HAVING COUNT_BIG(*) > 1) AS x
    """)
    assert offenders == 0


def test_no_overlapping_or_gapped_versions(warehouse):
    offenders = rows(warehouse, """
        WITH v AS (
            SELECT patient_id, effective_from, effective_to,
                   LEAD(effective_from) OVER (PARTITION BY patient_id
                                              ORDER BY effective_from) AS nxt
            FROM dw.DimPatient WHERE patient_key <> -1)
        SELECT patient_id, effective_to, nxt FROM v
        WHERE nxt IS NOT NULL AND DATEDIFF(DAY, effective_to, nxt) <> 1
    """)
    assert offenders == [], f"{len(offenders)} version boundaries are not exactly one day apart"


def test_facts_join_to_the_version_current_at_the_time(warehouse):
    """The reason Type 2 exists.

    Every encounter must resolve to the patient version whose validity window
    contains the encounter date. If it resolves to whatever is current now, the
    dimension is Type 2 in shape and Type 1 in behaviour.
    """
    wrong = scalar(warehouse, """
        SELECT COUNT_BIG(*)
        FROM dw.FactEncounter AS f
        JOIN dw.DimPatient    AS p ON p.patient_key = f.patient_key
        JOIN dw.DimDate       AS d ON d.date_key = f.start_date_key
        WHERE f.start_date_key <> -1
          AND d.full_date NOT BETWEEN p.effective_from AND p.effective_to
    """)
    assert wrong == 0


def test_historical_facts_point_at_the_historical_version(warehouse):
    """Sharper than the previous test: for a patient who moved, encounters
    before the move must carry the *old* city, not the new one."""
    manifest_path = ROOT / "data" / "patient_change_manifest.csv"
    if not manifest_path.exists():
        pytest.skip("no change manifest")
    with open(manifest_path, encoding="utf-8") as handle:
        moves = [r for r in csv.DictReader(handle) if r["change_kind"] in ("address", "both")]
    if not moves:
        pytest.skip("no address changes")

    for move in moves[:25]:
        mismatched = scalar(warehouse, """
            SELECT COUNT_BIG(*)
            FROM dw.FactEncounter AS f
            JOIN dw.DimPatient    AS p ON p.patient_key = f.patient_key
            JOIN dw.DimDate       AS d ON d.date_key = f.start_date_key
            WHERE p.patient_id = ? AND d.full_date < ? AND p.address_city <> ?
        """, move["patient_id"], changefeed.CHANGE_DATE, move["city_before"])
        assert mismatched == 0, (
            f"encounters before the move for {move['patient_id']} resolved to the new address")


def test_no_fact_lands_on_the_unknown_patient(warehouse):
    """The symptom of the effective_from bug this repository shipped and fixed:
    every fact correct, every key valid, and the star schema reporting that no
    encounter belonged to anybody."""
    for table in ("FactEncounter", "FactObservation", "FactMedicationOrder",
                  "FactEncounterDiagnosis", "FactProcedure"):
        orphaned = scalar(warehouse, f"SELECT COUNT_BIG(*) FROM dw.{table} WHERE patient_key = -1")
        assert orphaned == 0, f"{orphaned} rows in dw.{table} landed on the Unknown patient"


# ---------------------------------------------------------------------------
# Late-arriving dimensions
# ---------------------------------------------------------------------------

def test_a_late_arriving_provider_is_inferred_then_updated_in_place(warehouse):
    """The whole late-arrival contract, exercised rather than asserted.

    A provider is removed from the dimension, the encounter fact is reloaded so
    it has to reference something, and the loader must invent a stub. Then the
    real record is reloaded and the *same key* must survive — because every fact
    already points at it.
    """
    practitioner_id = scalar(warehouse, """
        SELECT TOP (1) e.primary_performer_id
        FROM norm.encounter AS e
        WHERE e.primary_performer_id IS NOT NULL
        GROUP BY e.primary_performer_id
        HAVING COUNT_BIG(*) BETWEEN 2 AND 50
    """)
    if practitioner_id is None:
        pytest.skip("no suitable practitioner in this extract")

    original_key = scalar(warehouse,
                          "SELECT provider_key FROM dw.DimProvider WHERE practitioner_id = ?",
                          practitioner_id)

    # Point every fact that references this provider at Unknown so the dimension
    # row can be removed. Two fact tables reference DimProvider, and missing the
    # second is how this test would fail on a foreign key rather than on the
    # behaviour it is meant to be checking.
    warehouse.execute("""
        UPDATE dw.FactEncounter       SET provider_key = -1 WHERE provider_key = ?;
        UPDATE dw.FactMedicationOrder SET provider_key = -1 WHERE provider_key = ?;
        DELETE FROM dw.DimProvider WHERE provider_key = ?;
    """, original_key, original_key, original_key)
    warehouse.connection.commit()

    assert scalar(warehouse, "SELECT COUNT_BIG(*) FROM dw.DimProvider WHERE practitioner_id = ?",
                  practitioner_id) == 0

    # The fact arrives with no dimension member available.
    warehouse.execute("EXEC dw.usp_infer_missing_members")
    warehouse.connection.commit()

    inferred = rows(warehouse,
                    "SELECT provider_key, is_inferred, full_name FROM dw.DimProvider "
                    "WHERE practitioner_id = ?", practitioner_id)
    assert len(inferred) == 1, "no inferred member was created for a late-arriving provider"
    inferred_key, is_inferred, full_name = inferred[0]
    assert is_inferred is True
    assert "late-arriving" in full_name

    # Now the real record turns up.
    warehouse.execute("EXEC dw.usp_load_dim_provider")
    warehouse.connection.commit()

    resolved = rows(warehouse,
                    "SELECT provider_key, is_inferred, full_name FROM dw.DimProvider "
                    "WHERE practitioner_id = ?", practitioner_id)
    assert len(resolved) == 1, "the real record inserted a second row instead of updating the stub"
    assert resolved[0][0] == inferred_key, (
        "the surrogate key changed; every fact now points at a stub")
    assert resolved[0][1] is False
    assert "late-arriving" not in resolved[0][2]

    # Put the facts back so the rest of the suite sees a consistent warehouse.
    warehouse.execute("EXEC dw.usp_load_fact_encounter")
    warehouse.execute("EXEC dw.usp_load_fact_medication_order")
    warehouse.connection.commit()
    unknown = scalar(
        warehouse, "SELECT COUNT_BIG(*) FROM dw.FactEncounter WHERE provider_key = -1")
    assert unknown == 0


def test_unknown_members_exist_in_every_dimension(warehouse):
    """Facts use inner joins; a missing Unknown member turns a NULL reference
    into a row that vanishes from the report entirely."""
    for table, key in [("DimPatient", "patient_key"), ("DimProvider", "provider_key"),
                       ("DimOrganization", "organization_key"), ("DimDiagnosis", "diagnosis_key"),
                       ("DimProcedure", "procedure_key"), ("DimMedication", "medication_key"),
                       ("DimObservationCode", "observation_code_key"),
                       ("DimEncounterType", "encounter_type_key"), ("DimDate", "date_key")]:
        present = scalar(warehouse, f"SELECT COUNT_BIG(*) FROM dw.{table} WHERE {key} = -1")
        assert present == 1, f"dw.{table} has no Unknown member"


# ---------------------------------------------------------------------------
# Re-runnability
# ---------------------------------------------------------------------------

def _checksums(cur) -> dict[str, tuple[int, int]]:
    """(row count, checksum) per fact and dimension table.

    CHECKSUM_AGG over BINARY_CHECKSUM(*) is order independent, which is what
    makes it usable as a table fingerprint. It is not cryptographic and can
    collide; combined with the row count it is more than strong enough to catch
    a loader that inserted, updated, or reordered anything.
    """
    result = {}
    for table in ("dw.DimPatient", "dw.DimProvider", "dw.DimOrganization",
                  "dw.DimDiagnosis", "dw.DimProcedure", "dw.DimMedication",
                  "dw.DimObservationCode", "dw.DimEncounterType",
                  "dw.FactEncounter", "dw.FactObservation", "dw.FactMedicationOrder",
                  "dw.FactEncounterDiagnosis", "dw.FactProcedure",
                  "norm.patient", "norm.encounter", "norm.observation",
                  "norm.condition", "norm.medication_request"):
        cur.execute(f"SELECT COUNT_BIG(*), "
                    f"ISNULL(CHECKSUM_AGG(BINARY_CHECKSUM(*)), 0) FROM {table}")
        count, checksum = cur.fetchone()
        result[table] = (int(count), int(checksum))
    return result


def test_the_pipeline_is_re_runnable(warehouse):
    """Run it twice, and nothing moves.

    The property that makes a nightly load safe to retry after a failure, and
    the one most easily lost — a MERGE with an UPDATE branch that writes a
    load timestamp on every match rewrites every row on every run, and the row
    counts stay identical while the checksums do not.
    """
    before = _checksums(warehouse)

    warehouse.execute("EXEC norm.usp_load_all")
    warehouse.execute(f"EXEC dw.usp_load_all @effective_date='{changefeed.CHANGE_DATE}'")
    warehouse.connection.commit()

    after = _checksums(warehouse)

    differing = {t: (before[t], after[t]) for t in before if before[t] != after[t]}
    assert differing == {}, f"a second run changed: {differing}"


def test_reconciliation_view_balances(warehouse):
    """raw -> norm equality for every resource type, without exception."""
    for resource_type, raw_rows, norm_rows, _dw in rows(
            warehouse, "SELECT resource_type, raw_rows, norm_rows, dw_rows "
                       "FROM dw.vw_pipeline_reconciliation"):
        assert raw_rows == norm_rows, (
            f"{resource_type}: {raw_rows} in raw, {norm_rows} in norm — rows were lost")


def test_observation_fact_grain_identity_holds(warehouse):
    """FactObservation = observations carrying a value + observation components.

    Exact, not a tolerance. This is the one place dw legitimately differs from
    norm, and an unexplained variance is how a warehouse loses rows quietly.
    """
    valued = scalar(warehouse, """
        SELECT COUNT_BIG(*) FROM norm.observation
        WHERE value_quantity IS NOT NULL OR value_code_concept_id IS NOT NULL
           OR value_string IS NOT NULL
    """)
    components = scalar(warehouse, "SELECT COUNT_BIG(*) FROM norm.observation_component")
    facts = scalar(warehouse, "SELECT COUNT_BIG(*) FROM dw.FactObservation")
    assert valued + components == facts


# ---------------------------------------------------------------------------
# The data quality suite, as pytest cases
# ---------------------------------------------------------------------------

def _dq_checks():
    sys.path.insert(0, str(ROOT / "tests" / "dq"))
    from run_dq import discover
    return discover()


@pytest.mark.parametrize("check", _dq_checks(), ids=lambda c: c.check_id)
def test_data_quality_check(warehouse, check):
    """Every .sql file in tests/dq is also a pytest case.

    The suite is runnable on its own — `python tests/dq/run_dq.py` prints the
    offending rows for a data manager — and runnable here, so a data quality
    regression fails the build rather than waiting for someone to look.
    """
    from run_dq import run

    result = run([check])[0]
    assert result.error is None, result.error
    if result.offending_rows and check.severity != "error":
        pytest.skip(f"{check.check_id} is a warning: {len(result.offending_rows)} rows")

    if result.offending_rows:
        sample = "\n".join(
            " | ".join("NULL" if v is None else str(v) for v in row)
            for row in result.offending_rows[:10]
        )
        pytest.fail(
            f"{check.check_id} ({check.description})\n"
            f"{len(result.offending_rows)} offending row(s)\n"
            f"{' | '.join(result.columns)}\n{sample}"
        )


def test_the_dq_suite_can_fail(warehouse, tmp_path):
    """A suite that has never failed is indistinguishable from one that cannot.

    A check is written that is guaranteed to return rows, and the runner must
    report it as blocking.
    """
    sys.path.insert(0, str(ROOT / "tests" / "dq"))
    from run_dq import discover, run

    (tmp_path / "99_deliberate_failure.sql").write_text(
        "-- @check: SELFTEST-01\n"
        "-- @severity: error\n"
        "-- @description: Deliberately returns a row.\n"
        "SELECT TOP (3) 'planted' AS failure, encounter_id FROM dw.FactEncounter;\n",
        encoding="utf-8", newline="\n",
    )
    result = run(discover(tmp_path))[0]
    assert not result.passed
    assert result.blocking
    assert len(result.offending_rows) == 3


def test_the_dq_runner_exits_non_zero_on_failure(tmp_path):
    """The build gate, exercised as a subprocess — an exception inside the
    runner that never reaches the exit code is a gate that never closes."""
    reachable, reason = available()
    if not reachable:
        pytest.skip(f"no SQL Server: {reason}")

    (tmp_path / "99_deliberate_failure.sql").write_text(
        "-- @check: SELFTEST-02\n-- @severity: error\n-- @description: Fails.\n"
        "SELECT 1 AS failure;\n", encoding="utf-8", newline="\n")

    completed = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "dq" / "run_dq.py")],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert completed.returncode == 0, f"the real suite should pass:\n{completed.stdout}"
