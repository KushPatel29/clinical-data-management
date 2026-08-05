"""
The REST client's behaviour, driven by a scripted transport.

No network. Every response is written by the test, which is the only way to
assert what happens on a 429, on a 503 that recovers on the fourth attempt, or
on a server that hands back an HTML error page with a 200 — none of which a
public test server produces on demand. `sleep` is injected and recorded, so the
backoff schedule is asserted rather than waited through: a test suite that
actually slept through five exponential retries would take half a minute to
prove one property.

There is one test that does hit the live HAPI server, marked `live` and skipped
by default. It is there because a client that has only ever met a mock is a
client that has never met a real Bundle.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fhir.client import (  # noqa: E402
    FhirClient,
    FhirServerError,
    RetryPolicy,
)

BASE = "https://fhir.example.org/baseR4"


def bundle(entries, next_url=None, total=None):
    document = {
        "resourceType": "Bundle",
        "type": "searchset",
        "entry": entries,
        "link": [{"relation": "self", "url": f"{BASE}/Patient"}],
    }
    if total is not None:
        document["total"] = total
    if next_url:
        document["link"].append({"relation": "next", "url": next_url})
    return document


def entry(resource_id, mode="match", resource_type="Patient"):
    return {
        "fullUrl": f"{BASE}/{resource_type}/{resource_id}",
        "resource": {"resourceType": resource_type, "id": resource_id},
        "search": {"mode": mode},
    }


class Recorder:
    """Collects the sleeps the retry policy asks for, instead of taking them."""

    def __init__(self):
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def client_for(handler, retry: RetryPolicy | None = None) -> tuple[FhirClient, Recorder]:
    recorder = Recorder()
    return (
        FhirClient(
            BASE,
            transport=httpx.MockTransport(handler),
            retry=retry or RetryPolicy(jitter=False),
            sleep=recorder,
        ),
        recorder,
    )


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def test_follows_the_next_link_across_pages():
    pages = {
        f"{BASE}/Patient":
            bundle([entry("p1"), entry("p2")], next_url=f"{BASE}/Patient?_getpages=tok2"),
        f"{BASE}/Patient?_getpages=tok2":
            bundle([entry("p3")], next_url=f"{BASE}/Patient?_getpages=tok3"),
        f"{BASE}/Patient?_getpages=tok3": bundle([entry("p4")]),
    }
    seen_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen_urls.append(url)
        key = url.split("&")[0] if "_getpages" in url else url.split("?")[0]
        return httpx.Response(200, json=pages[key])

    client, _ = client_for(handler)
    ids = [resource["id"] for resource, _ in client.search("Patient", count=2)]
    assert ids == ["p1", "p2", "p3", "p4"]
    assert client.stats.pages == 3


def test_stops_when_there_is_no_next_link():
    """A Bundle with no `next` is the last page. Not an error, not a retry."""
    def handler(request):
        return httpx.Response(200, json=bundle([entry("p1")]))

    client, _ = client_for(handler)
    assert len(list(client.search("Patient"))) == 1
    assert client.stats.pages == 1


def test_search_parameters_are_not_resent_with_the_continuation_url():
    """The bug that makes a paginated pull loop forever.

    The next-page URL already carries the server's search state. Re-sending
    _count and _since alongside it can restart the search from page one, and the
    loop never terminates because page one always has a next link.
    """
    requests: list[httpx.Request] = []

    def handler(request):
        requests.append(request)
        if "_getpages" in str(request.url):
            return httpx.Response(200, json=bundle([entry("p2")]))
        return httpx.Response(200, json=bundle([entry("p1")],
                                               next_url=f"{BASE}/Patient?_getpages=tok"))

    client, _ = client_for(handler)
    list(client.search("Patient", count=10, since="2026-01-01"))

    assert "_count=10" in str(requests[0].url)
    assert "_since=2026-01-01" in str(requests[0].url)
    assert "_count" not in str(requests[1].url), "params must not ride along with the next link"
    assert "_since" not in str(requests[1].url)


def test_empty_bundle_is_an_empty_result_not_an_error():
    def handler(request):
        return httpx.Response(200, json={"resourceType": "Bundle", "type": "searchset", "total": 0})

    client, _ = client_for(handler)
    assert list(client.search("Patient")) == []


def test_max_pages_bounds_the_pull():
    def handler(request):
        return httpx.Response(
            200, json=bundle([entry("p")], next_url=f"{BASE}/Patient?_getpages=x"))

    client, _ = client_for(handler)
    assert len(list(client.search("Patient", max_pages=3))) == 3


def test_non_bundle_response_is_an_error():
    def handler(request):
        return httpx.Response(200, json={"resourceType": "OperationOutcome",
                                         "issue": [{"severity": "error"}]})

    client, _ = client_for(handler)
    with pytest.raises(FhirServerError, match="expected a Bundle"):
        list(client.search("Patient"))


# ---------------------------------------------------------------------------
# Search parameters
# ---------------------------------------------------------------------------

def test_count_since_and_include_are_sent():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        return httpx.Response(200, json=bundle([]))

    client, _ = client_for(handler)
    list(client.search("Encounter", count=200, since="2026-08-01T00:00:00Z",
                       include=["Encounter:patient", "Encounter:practitioner"]))

    assert "_count=200" in captured["url"]
    assert "_since=2026-08-01" in captured["url"]
    assert captured["url"].count("_include=") == 2


def test_included_resources_are_not_counted_as_matches():
    """`_include` adds entries whose search.mode is 'include'. Counting them as
    results is how an _include query reports twice the rows it found."""
    def handler(request):
        return httpx.Response(200, json=bundle([
            entry("e1", mode="match", resource_type="Encounter"),
            entry("p1", mode="include", resource_type="Patient"),
            entry("e2", mode="match", resource_type="Encounter"),
        ]))

    client, _ = client_for(handler)
    results = list(client.search("Encounter", include=["Encounter:patient"]))
    assert [mode for _, mode in results] == ["match", "include", "match"]
    assert client.stats.matched == 2
    assert client.stats.included == 1


def test_count_is_a_hint_not_a_promise():
    """A server may cap _count. Asking for 500 and getting 50 is conforming
    behaviour, and code that assumes otherwise miscounts."""
    def handler(request):
        return httpx.Response(200, json=bundle([entry(f"p{i}") for i in range(50)]))

    client, _ = client_for(handler)
    assert len(list(client.search("Patient", count=500))) == 50


# ---------------------------------------------------------------------------
# Backoff
# ---------------------------------------------------------------------------

def test_retries_on_429_and_eventually_succeeds():
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(429, json={"resourceType": "OperationOutcome"})
        return httpx.Response(200, json=bundle([entry("p1")]))

    client, sleeps = client_for(handler)
    assert len(list(client.search("Patient"))) == 1
    assert client.stats.retries == 2
    assert len(sleeps.delays) == 2


def test_backoff_is_exponential():
    def handler(request):
        return httpx.Response(503)

    client, sleeps = client_for(handler, RetryPolicy(max_attempts=5, base_delay=0.5, jitter=False))
    with pytest.raises(FhirServerError):
        list(client.search("Patient"))
    assert sleeps.delays == [0.5, 1.0, 2.0, 4.0]


def test_backoff_is_capped():
    def handler(request):
        return httpx.Response(503)

    client, sleeps = client_for(
        handler, RetryPolicy(max_attempts=8, base_delay=1.0, max_delay=5.0, jitter=False))
    with pytest.raises(FhirServerError):
        list(client.search("Patient"))
    assert max(sleeps.delays) == 5.0


def test_jitter_keeps_the_delay_inside_the_window():
    """Full jitter picks uniformly in [0, window). Without it, every client that
    hit the same rate limit retries in the same millisecond."""
    policy = RetryPolicy(base_delay=1.0, jitter=True)
    delays = [policy.delay_for(3) for _ in range(200)]
    assert all(0 <= d <= 4.0 for d in delays)
    assert len(set(delays)) > 1, "jitter that returns a constant is not jitter"


def test_retry_after_header_is_honoured():
    """The server said how long to wait. Ignoring it and backing off on a
    private schedule is how a client gets rate limited twice."""
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json=bundle([]))

    client, sleeps = client_for(handler)
    list(client.search("Patient"))
    assert sleeps.delays == [7.0]


def test_unparseable_retry_after_falls_back_to_exponential():
    """`Retry-After` may be an HTTP-date. Rather than parse it and get the
    timezone wrong, fall back."""
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(503, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
        return httpx.Response(200, json=bundle([]))

    client, sleeps = client_for(handler)
    list(client.search("Patient"))
    assert sleeps.delays == [0.5]


def test_retries_are_bounded():
    def handler(request):
        return httpx.Response(503)

    client, _ = client_for(handler, RetryPolicy(max_attempts=3, jitter=False))
    with pytest.raises(FhirServerError) as caught:
        list(client.search("Patient"))
    assert caught.value.status_code == 503
    assert client.stats.requests == 3


def test_client_errors_are_not_retried():
    """404 will still be 404 in two seconds. Retrying it wastes the budget that
    a genuine 503 needs."""
    def handler(request):
        return httpx.Response(404)

    client, sleeps = client_for(handler)
    with pytest.raises(FhirServerError):
        list(client.search("Patient"))
    assert client.stats.requests == 1
    assert sleeps.delays == []


def test_transport_errors_are_retried():
    """A connection reset arrives as an exception, not a status code, and is as
    retryable as a 503."""
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise httpx.ConnectError("connection reset")
        return httpx.Response(200, json=bundle([entry("p1")]))

    client, _ = client_for(handler)
    assert len(list(client.search("Patient"))) == 1
    assert client.stats.retries == 2


def test_html_error_page_with_a_200_is_reported_not_retried():
    """The failure that looks like success. A proxy returns an HTML error page
    with status 200; `.json()` throws, and a bare except turns it into an empty
    result set that nobody notices."""
    def handler(request):
        return httpx.Response(200, text="<html><body>Gateway Timeout</body></html>",
                              headers={"content-type": "text/html"})

    client, _ = client_for(handler)
    with pytest.raises(FhirServerError, match="not JSON"):
        list(client.search("Patient"))


# ---------------------------------------------------------------------------
# Absent optional elements
# ---------------------------------------------------------------------------

def test_bundle_entries_without_a_resource_are_skipped():
    """A Bundle entry may carry only a `search` or a `response` block."""
    def handler(request):
        return httpx.Response(200, json={
            "resourceType": "Bundle", "type": "searchset",
            "entry": [{"search": {"mode": "match"}}, entry("p1")],
        })

    client, _ = client_for(handler)
    assert [r["id"] for r, _ in client.search("Patient")] == ["p1"]


def test_entry_without_a_search_mode_defaults_to_match():
    """`search.mode` is optional; a plain search result is a match."""
    def handler(request):
        return httpx.Response(200, json={
            "resourceType": "Bundle", "type": "searchset",
            "entry": [{"resource": {"resourceType": "Patient", "id": "p1"}}],
        })

    client, _ = client_for(handler)
    assert [mode for _, mode in client.search("Patient")] == ["match"]


def test_next_link_without_a_url_is_ignored():
    def handler(request):
        return httpx.Response(200, json={
            "resourceType": "Bundle", "type": "searchset",
            "entry": [entry("p1")],
            "link": [{"relation": "next"}],
        })

    client, _ = client_for(handler)
    assert len(list(client.search("Patient"))) == 1


def test_next_link_is_found_by_relation_not_position():
    """`link` is unordered. Indexing into it works until a server puts `self`
    second."""
    document = {
        "resourceType": "Bundle", "type": "searchset", "entry": [],
        "link": [{"relation": "previous", "url": "x"},
                 {"relation": "next", "url": "the-right-one"},
                 {"relation": "self", "url": "y"}],
    }
    assert FhirClient.next_link(document) == "the-right-one"
    assert FhirClient.next_link({"resourceType": "Bundle"}) is None


# ---------------------------------------------------------------------------
# Against the real server
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_live_hapi_server_paginates(request):
    """Skipped unless --live is passed. A client that has only met a mock has
    never met a real Bundle."""
    if not request.config.getoption("--live"):
        pytest.skip("needs --live and network access")

    with FhirClient() as client:
        results = list(client.search("Patient", count=10, max_pages=2))
    assert len(results) > 10, "pagination did not advance past the first page"
    assert client.stats.pages == 2
    assert all(r.get("resourceType") == "Patient" for r, mode in results if mode == "match")
