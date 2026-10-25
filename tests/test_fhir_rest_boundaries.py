"""REST boundaries must fail clearly before networking or accepting bad pages."""
from contextlib import nullcontext
from types import SimpleNamespace

import httpx
import pytest

from fhir.client import FhirClient, FhirServerError

BASE = "https://fhir.example/baseR4"
PATIENT = {"resourceType": "Patient", "id": "p1"}


def bundle(*entries, **extra):
    return {"resourceType": "Bundle", "type": "searchset", "entry": list(entries), **extra}


def result(resource=PATIENT, mode="match"):
    return {"resource": resource, "search": {"mode": mode}}


@pytest.mark.parametrize("limit", ["max_pages", "max_resources"])
def test_zero_fetch_limit_performs_no_request(limit):
    requests = []
    with FhirClient(BASE, transport=httpx.MockTransport(
        lambda request: requests.append(request) or httpx.Response(200, json=bundle(result()))
    )) as client:
        assert list(client.search("Patient", **{limit: 0})) == []
        assert not requests
        assert client.stats.requests == client.stats.pages == client.stats.matched == 0


@pytest.mark.parametrize("parameter", ["count", "max_pages", "max_resources"])
@pytest.mark.parametrize("value", [-1, True, False, 1.5, "3", float("nan")])
def test_invalid_fetch_limits_fail_before_a_request(parameter, value):
    requests = []
    with FhirClient(BASE, transport=httpx.MockTransport(
        lambda request: requests.append(request) or httpx.Response(200, json=bundle(result()))
    )) as client:
        with pytest.raises(ValueError, match=parameter):
            list(client.search("Patient", **{parameter: value}))
        assert not requests


def test_count_only_query_remains_a_real_fhir_request():
    requests = []
    with FhirClient(BASE, transport=httpx.MockTransport(
        lambda request: requests.append(request) or httpx.Response(200, json=bundle(total=42))
    )) as client:
        assert list(client.search("Patient", count=0)) == []
        assert len(requests) == 1 and requests[0].url.params["_count"] == "0"


@pytest.mark.parametrize("parameter", ["count", "max_pages", "max_resources"])
def test_invalid_ingestion_limits_fail_before_opening_the_database(monkeypatch, parameter):
    from fhir import ingest

    def forbidden(*args):
        pytest.fail("invalid limits must be rejected before connecting to SQL Server")

    monkeypatch.setattr(ingest, "connect", forbidden)
    with pytest.raises(ValueError, match=parameter):
        ingest.ingest_rest("Patient", **{parameter: -1})


@pytest.mark.parametrize("args", [
    ["--rest", "--count", "-1"], ["--rest", "--max-pages", "-1"],
    ["--limit", "-1"], ["--rest", "--resource", ""],
    ["--rest", "--resource", ","], ["--rest", "--resource", " , "],
])
def test_invalid_cli_input_is_a_usage_error_without_database_access(monkeypatch, capsys, args):
    from fhir import ingest

    def forbidden(*args, **kwargs):
        pytest.fail("invalid CLI input must not open the database")

    monkeypatch.setattr(ingest, "connect", forbidden)
    with pytest.raises(SystemExit) as error:
        ingest.main(args)
    assert error.value.code == 2
    assert "error:" in capsys.readouterr().err


MALFORMED_PAGES = [
    [], None, "Bundle", 4,
    {"resourceType": "Bundle", "entry": {}},
    {"resourceType": "Bundle", "entry": "bad"},
    bundle(3), bundle(None), bundle(result(resource=[PATIENT])),
    bundle(result(resource=False)), bundle(result(resource="Patient")),
    bundle({"resource": PATIENT, "search": []}),
    bundle({"resource": PATIENT, "search": "match"}),
    bundle(result(mode=[])), bundle(result(mode="unexpected")),
    bundle(link={}), bundle(link="next"), bundle(link=[None]),
    bundle(link=[{"relation": "next", "url": 3}]),
]


@pytest.mark.parametrize("document", MALFORMED_PAGES)
def test_malformed_pages_raise_a_typed_protocol_error(document):
    with FhirClient(BASE, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=document)
    )) as client:
        with pytest.raises(FhirServerError):
            list(client.search("Patient"))


def test_json_depth_failure_is_a_typed_server_error():
    body = '[' * 1500 + '0' + ']' * 1500
    with FhirClient(BASE, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=body, headers={"content-type": "application/json"})
    )) as client, pytest.raises(FhirServerError):
        list(client.search("Patient"))


def test_invalid_page_is_rejected_before_any_of_its_records_are_yielded():
    with FhirClient(BASE, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=bundle(result(), 3))
    )) as client:
        with pytest.raises(FhirServerError):
            next(client.search("Patient"))
        assert client.stats.matched == client.stats.included == 0


def outcome(severity):
    return result({"resourceType": "OperationOutcome", "issue": [
        {"severity": severity, "code": "processing"},
    ]}, mode="outcome")


@pytest.mark.parametrize("severity", ["warning", "information"])
def test_search_diagnostics_are_not_included_clinical_rows_or_resource_limits(severity):
    with FhirClient(BASE, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=bundle(outcome(severity), result()))
    )) as client:
        assert list(client.search("Patient", max_resources=1)) == [(PATIENT, "match")]
        assert client.stats.matched == 1 and client.stats.included == 0
        assert client.stats.outcomes == 1


@pytest.mark.parametrize("severity", ["error", "fatal"])
def test_error_outcomes_are_failures_even_with_http_200(severity):
    with FhirClient(BASE, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=bundle(result(), outcome(severity)))
    )) as client:
        with pytest.raises(FhirServerError, match="OperationOutcome"):
            next(client.search("Patient"))
        assert client.stats.matched == client.stats.included == 0


@pytest.mark.parametrize("issues", [[], "bad", [None], [{"severity": "unknown"}]])
def test_unusable_diagnostics_cannot_be_treated_as_a_successful_search(issues):
    document = bundle(result({"resourceType": "OperationOutcome", "issue": issues}, "outcome"))
    with FhirClient(BASE, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=document)
    )) as client, pytest.raises(FhirServerError, match="OperationOutcome"):
        list(client.search("Patient"))


def test_warning_diagnostics_do_not_become_warehouse_rejects(monkeypatch):
    from fhir import client as client_module
    from fhir import ingest

    rows = []
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=bundle(outcome("warning"), result()))
    )
    class ScriptedClient(FhirClient):
        def __init__(self, *args):
            super().__init__(BASE, transport=transport)

    monkeypatch.setattr(client_module, "FhirClient", ScriptedClient)
    monkeypatch.setattr(ingest, "connect", lambda *args: nullcontext(None))
    monkeypatch.setattr(ingest, "Ingestor", lambda *args: SimpleNamespace(
        add=rows.append, finish=lambda: SimpleNamespace(elapsed_seconds=0),
        fail=lambda *args: pytest.fail("warning diagnostics must not fail ingestion"),
    ))
    _, stats = ingest.ingest_rest("Patient")
    assert len(rows) == 1 and rows[0].payload == PATIENT
    assert stats["matched"] == 1 and stats["included"] == 0 and stats["outcomes"] == 1
