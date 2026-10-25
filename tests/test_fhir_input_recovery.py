"""Malformed resource shapes and encodings must quarantine without losing good rows."""

import json
from types import SimpleNamespace

import pytest

from fhir import bulk

NON_OBJECTS = [[], ["bad"], 42, "not-a-resource", True, None]
PATIENT = {"resourceType": "Patient", "id": "p1"}


@pytest.mark.parametrize("value", NON_OBJECTS)
def test_non_object_ndjson_is_quarantined_between_good_records(tmp_path, value):
    path = tmp_path / "Patient.ndjson"
    path.write_text("\n".join(map(json.dumps, [PATIENT, value, PATIENT])), encoding="utf-8")
    rows = list(bulk.read_file(path))
    assert len(rows) == 3
    assert rows[0].payload == rows[2].payload == PATIENT
    assert rows[1].payload is None and rows[1].parse_error
    assert rows[1].line_number == 2 and rows[1].raw_text == json.dumps(value)
    assert bulk.count_resources(tmp_path) == {"Patient": 2, "(unparsed)": 1}


@pytest.mark.parametrize("value", NON_OBJECTS)
def test_non_object_json_document_is_quarantined(tmp_path, value):
    path = tmp_path / "Patient.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    rows = list(bulk.read_file(path))
    assert len(rows) == 1
    assert rows[0].payload is None and rows[0].parse_error


@pytest.mark.parametrize("value", [[], 42, "bad", True, None])
def test_non_object_bundle_resource_keeps_sibling_records(tmp_path, value):
    path = tmp_path / "Patient.json"
    path.write_text(json.dumps({"resourceType": "Bundle", "entry": [
        {"resource": PATIENT}, {"resource": value}, {"resource": PATIENT},
    ]}), encoding="utf-8")
    rows = list(bulk.read_file(path))
    assert len(rows) == 3
    assert rows[0].payload == rows[2].payload == PATIENT
    assert rows[1].payload is None and rows[1].parse_error
    assert rows[1].line_number == 2


@pytest.mark.parametrize("value", [{}, "bad"])
def test_malformed_bundle_entry_container_is_one_quarantine_record(tmp_path, value):
    path = tmp_path / "Patient.json"
    path.write_text(json.dumps({"resourceType": "Bundle", "entry": value}), encoding="utf-8")
    rows = list(bulk.read_file(path))
    assert len(rows) == 1
    assert rows[0].payload is None and rows[0].parse_error


def test_invalid_utf8_ndjson_line_does_not_abort_valid_siblings(tmp_path):
    path = tmp_path / "Patient.ndjson"
    good = json.dumps(PATIENT).encode()
    path.write_bytes(good + b'\n{"id":"\xff"}\n' + good)
    rows = list(bulk.read_file(path))
    assert len(rows) == 3 and rows[0].payload == rows[2].payload == PATIENT
    assert rows[1].payload is None and "UTF-8" in rows[1].parse_error
    assert rows[1].line_number == 2 and rows[1].raw_text


def test_invalid_utf8_json_document_is_quarantined(tmp_path):
    path = tmp_path / "Patient.json"
    path.write_bytes(b'{"id":"\xff"}')
    rows = list(bulk.read_file(path))
    assert len(rows) == 1 and rows[0].payload is None
    assert "UTF-8" in rows[0].parse_error


@pytest.mark.parametrize("value", [[], 42, "bad", True])
def test_ingestion_defensively_quarantines_non_object_resources(value):
    # No database is opened: exercise the actual reject path with only its state.
    from fhir.ingest import Ingestor, IngestResult

    ingestor = object.__new__(Ingestor)
    ingestor.result = IngestResult()
    ingestor._pending_rejects = []
    ingestor.cur = SimpleNamespace()
    ingestor.add(bulk.ReadResource(value, "fixture", 2))
    assert ingestor.result.read == ingestor.result.rejected == 1
    assert len(ingestor._pending_rejects) == 1
    assert ingestor._pending_rejects[0][2:4] == ("fixture", 2)


@pytest.mark.parametrize("value", [["Patient"], {"type": "Patient"}])
def test_non_string_resource_type_is_countable_and_quarantined(tmp_path, value):
    from fhir.ingest import DefectInjector, Ingestor, IngestResult
    from fhir.models import FhirValidationError, validate_resource

    payload = {"resourceType": value, "id": "p1"}
    path = tmp_path / "Patient.ndjson"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert bulk.count_resources(tmp_path) == {"(untyped)": 1}
    assert DefectInjector(1).maybe_corrupt(payload, "fixture", 1) is payload
    with pytest.raises(FhirValidationError, match="resourceType"):
        validate_resource(payload)
    ingestor = object.__new__(Ingestor)
    ingestor.result = IngestResult()
    ingestor._pending_rejects = []
    ingestor.add(bulk.ReadResource(payload, "fixture", 1))
    assert ingestor.result.rejected == 1


@pytest.mark.parametrize("suffix", [".ndjson", ".json"])
@pytest.mark.parametrize("invalid", ["9" * 5000, "[" * 2000 + "]" * 2000],
                         ids=["integer-limit", "nesting-limit"])
def test_parser_resource_limits_quarantine_the_input(tmp_path, suffix, invalid):
    path = tmp_path / ("Patient" + suffix)
    text = json.dumps(PATIENT) + "\n" + invalid + "\n" + json.dumps(PATIENT)
    path.write_text(text if suffix == ".ndjson" else invalid, encoding="utf-8")
    rows = list(bulk.read_file(path))
    bad = rows[1] if suffix == ".ndjson" else rows[0]
    assert bad.payload is None and bad.parse_error
    if suffix == ".ndjson":
        assert rows[0].payload == rows[2].payload == PATIENT


@pytest.mark.warehouse
def test_real_bulk_load_commits_good_rows_and_quarantines_bad_rows(tmp_path):
    from uuid import uuid4

    from db.connection import available, connect
    from fhir.ingest import ingest_bulk

    ready, reason = available()
    if not ready:
        pytest.skip(reason)
    patients = [{"resourceType": "Patient", "id": "input-" + uuid4().hex} for _ in range(2)]
    path = tmp_path / "Patient.ndjson"
    path.write_bytes(json.dumps(patients[0]).encode() + b'\n[]\n{"id":"\xff"}\n'
                     + json.dumps(patients[1]).encode())
    result = ingest_bulk(tmp_path)
    with connect() as cn:
        cur = cn.cursor()
        try:
            assert (result.read, result.accepted, result.rejected) == (4, 2, 2)
            cur.execute("SELECT COUNT(*) FROM raw.fhir_resource WHERE batch_id = ?",
                        result.batch_id)
            assert cur.fetchone()[0] == 2
            cur.execute("SELECT source_line, failure_stage FROM stg.ingest_rejects "
                        "WHERE batch_id = ? ORDER BY source_line", result.batch_id)
            assert [tuple(row) for row in cur.fetchall()] == [(2, "parse"), (3, "parse")]
            cur.execute("SELECT status FROM meta.load_batch WHERE batch_id = ?", result.batch_id)
            assert cur.fetchone()[0] == "succeeded"
        finally:
            cur.execute("DELETE FROM raw.fhir_resource WHERE batch_id = ?", result.batch_id)
            cur.execute("DELETE FROM stg.ingest_rejects WHERE batch_id = ?", result.batch_id)
            cur.execute("DELETE FROM meta.load_batch WHERE batch_id = ?", result.batch_id)
            cn.commit()
