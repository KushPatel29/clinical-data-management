"""A faulty continuation must fail before the same page is fetched again."""

import httpx
import pytest

from fhir.client import FhirClient, FhirServerError


@pytest.mark.parametrize("tokens", [("a",), ("a", "b")])
def test_cyclic_continuations_stop_before_replaying_a_page(tokens):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if len(calls) > len(tokens) + 1:
            raise RuntimeError("test guard: unbounded pagination")
        token = tokens[(len(calls) - 1) % len(tokens)]
        return httpx.Response(200, json={
            "resourceType": "Bundle",
            "entry": [{"resource": {"resourceType": "Patient", "id": str(len(calls))}}],
            "link": [{"relation": "next", "url": f"https://fhir.example/Patient?cursor={token}"}],
        })

    with FhirClient("https://fhir.example", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FhirServerError, match="repeated pagination"):
            list(client.search("Patient"))
        assert len(calls) == len(tokens) + 1
        assert len(calls) == len(set(calls))


def test_explicit_page_limit_stops_without_following_a_bad_continuation():
    def handler(request):
        return httpx.Response(200, json={
            "resourceType": "Bundle", "entry": [],
            "link": [{"relation": "next", "url": str(request.url)}],
        })

    with FhirClient("https://fhir.example", transport=httpx.MockTransport(handler)) as client:
        assert list(client.search("Patient", max_pages=1)) == []
        assert client.stats.requests == 1
