"""
A FHIR REST client, written for the way real servers behave rather than the way
the happy path looks in a tutorial.

Four things this handles that a `requests.get(url).json()["entry"]` does not:

**Pagination.** A FHIR search returns a Bundle holding one page and a `link`
array. The next page is the entry whose `relation` is `"next"`, and its `url` is
absolute and already carries an opaque continuation token. You do not construct
it, you do not add `&page=2` to it, and you do not assume it exists — its
absence is how the server says "that was the last page".

**Backoff.** 429 means slow down and 503 means come back; both are routine on a
shared server and neither is a reason to fail a load. Retries are exponential
with full jitter, they honour `Retry-After` when the server sends one, and they
are bounded — a client that retries forever is a client that hangs a nightly
batch instead of failing it.

**Search parameters.** `_count` is a page-size *hint*, not a promise, and a
server that caps it at 50 will hand back 50 no matter what you ask for; code
that assumes it got what it asked for miscounts. `_since` is what makes an
incremental pull incremental. `_include` pulls referenced resources into the
same Bundle, which turns N+1 round trips into one — and adds entries whose
`search.mode` is `"include"` rather than `"match"`, which must not be counted as
results.

**Absent optional elements.** Almost every element in FHIR is optional. This
client returns raw dicts and lets the models decide what is missing; the one
place it takes a position is that a Bundle with no `entry` is an empty page, not
an error, because that is what a search matching nothing returns.
"""

from __future__ import annotations

import random
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://hapi.fhir.org/baseR4"
FHIR_JSON = "application/fhir+json"

# 429 is rate limiting; 5xx is the server having a bad time. 4xx other than 429
# means the request is wrong and retrying it will keep being wrong.
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


@dataclass
class RetryPolicy:
    max_attempts: int = 5
    base_delay: float = 0.5
    max_delay: float = 30.0
    # Full jitter. Without it every client that hit the same rate limit retries
    # at the same moment and the thundering herd re-creates the outage it is
    # backing off from.
    jitter: bool = True

    def delay_for(self, attempt: int, retry_after: float | None = None) -> float:
        """Seconds to wait before attempt number `attempt` (1-based)."""
        if retry_after is not None:
            return min(retry_after, self.max_delay)
        window = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        return random.uniform(0, window) if self.jitter else window


@dataclass
class FetchStats:
    requests: int = 0
    retries: int = 0
    pages: int = 0
    matched: int = 0
    included: int = 0
    outcomes: int = 0
    status_counts: dict[int, int] = field(default_factory=dict)

    def record_status(self, status: int) -> None:
        self.status_counts[status] = self.status_counts.get(status, 0) + 1


class FhirServerError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """`Retry-After` is either delay-seconds or an HTTP-date. Only the numeric
    form is honoured; parsing the date form and getting the timezone wrong is a
    worse failure than falling back to exponential backoff."""
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        return None


class FhirClient:
    """Read-only FHIR REST client.

    Read-only on purpose. This warehouse pulls from a source system it does not
    own; a client that can POST to a public test server is a client that can
    write to a public test server by accident.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
        retry: RetryPolicy | None = None,
        sleep=time.sleep,
        user_agent: str = "clinical-data-management/1.0 (+https://github.com/KushPatel29/clinical-data-management)",
    ):
        self.base_url = base_url.rstrip("/")
        self.retry = retry or RetryPolicy()
        self.stats = FetchStats()
        # Injected so tests can drive the retry loop without sleeping through it.
        self._sleep = sleep
        self._client = httpx.Client(
            transport=transport,
            timeout=timeout,
            follow_redirects=True,
            headers={"Accept": FHIR_JSON, "User-Agent": user_agent},
        )

    def __enter__(self) -> FhirClient:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- transport ---------------------------------------------------------

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict:
        last_error: Exception | None = None
        for attempt in range(1, self.retry.max_attempts + 1):
            try:
                self.stats.requests += 1
                response = self._client.get(url, params=params)
                self.stats.record_status(response.status_code)
            except httpx.TransportError as exc:
                # A connection reset is as retryable as a 503 and arrives as an
                # exception rather than a status code.
                last_error = exc
                if attempt == self.retry.max_attempts:
                    raise FhirServerError(
                        f"transport error after {attempt} attempts: {exc}") from exc
                self.stats.retries += 1
                self._sleep(self.retry.delay_for(attempt))
                continue

            if response.status_code in RETRY_STATUS:
                if attempt == self.retry.max_attempts:
                    raise FhirServerError(
                        f"{response.status_code} after {attempt} attempts: {url}",
                        status_code=response.status_code,
                    )
                self.stats.retries += 1
                self._sleep(self.retry.delay_for(attempt, _retry_after_seconds(response)))
                continue

            if response.status_code >= 400:
                raise FhirServerError(
                    f"{response.status_code} {response.reason_phrase}: {url}",
                    status_code=response.status_code,
                )

            try:
                document = response.json()
            except (ValueError, RecursionError) as exc:
                # A 200 whose body is an HTML error page. Retrying is pointless;
                # the caller needs to see what actually came back.
                raise FhirServerError(
                    f"200 response was not JSON ({response.headers.get('content-type')}): "
                    f"{response.text[:200]!r}"
                ) from exc

            if not isinstance(document, dict):
                raise FhirServerError("expected a JSON object from the FHIR server")
            return document

        raise FhirServerError(f"exhausted retries: {last_error}")

    # -- search ------------------------------------------------------------

    @staticmethod
    def next_link(bundle: dict) -> str | None:
        """The absolute URL of the next page, or None if this was the last.

        `link` is 0..* and its entries are unordered, so this searches for the
        relation rather than indexing. A missing `link` array is normal.
        """
        links = bundle.get("link")
        if links is None:
            return None
        if not isinstance(links, list):
            raise FhirServerError("Bundle.link must be an array")
        next_url = None
        for link in links:
            if not isinstance(link, dict):
                raise FhirServerError("Bundle.link entries must be objects")
            url = link.get("url")
            if url is not None and not isinstance(url, str):
                raise FhirServerError("Bundle.link.url must be a string")
            if link.get("relation") == "next" and url and next_url is None:
                next_url = url
        return next_url

    @staticmethod
    def validate_search_limits(*, count: int, max_pages: int | None,
                               max_resources: int | None) -> None:
        """Reject invalid limits before HTTP requests or ingestion side effects.

        `_count=0` is a FHIR count-only query. Zero client fetch limits instead
        stop iteration without making that query. None leaves a client cap open.
        """
        for name, value in (("count", count), ("max_pages", max_pages),
                            ("max_resources", max_resources)):
            if value is None and name != "count":
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

    @staticmethod
    def _search_entries(bundle: dict) -> tuple[list[tuple[dict, str]], int]:
        """Validate the whole page before exposing records from that page.

        Search OperationOutcome entries are diagnostics, not clinical results.
        Warnings are counted; error/fatal outcomes fail even under HTTP 200.
        """
        entries = bundle.get("entry")
        if entries is None:
            return [], 0
        if not isinstance(entries, list):
            raise FhirServerError("Bundle.entry must be an array")
        records = []
        outcomes = 0
        for entry in entries:
            if not isinstance(entry, dict):
                raise FhirServerError("Bundle.entry entries must be objects")
            resource = entry.get("resource")
            if resource is None:
                continue
            if not isinstance(resource, dict):
                raise FhirServerError("Bundle.entry.resource must be an object")
            if not resource:
                continue
            search = entry.get("search")
            if search is None:
                search = {}
            if not isinstance(search, dict):
                raise FhirServerError("Bundle.entry.search must be an object")
            mode = search.get("mode", "match")
            if mode not in ("match", "include", "outcome"):
                raise FhirServerError("unexpected Bundle.entry.search.mode")
            if mode == "outcome":
                if resource.get("resourceType") != "OperationOutcome":
                    raise FhirServerError("search outcome must contain an OperationOutcome")
                issues = resource.get("issue", [])
                if (not isinstance(issues, list) or not issues
                        or any(not isinstance(i, dict) for i in issues)):
                    raise FhirServerError("OperationOutcome.issue must be a non-empty object array")
                if any(i.get("severity") not in ("fatal", "error", "warning", "information")
                       for i in issues):
                    raise FhirServerError("unexpected OperationOutcome issue severity")
                if any(i.get("severity") in ("error", "fatal") for i in issues):
                    raise FhirServerError("search returned an error/fatal OperationOutcome")
                outcomes += 1
            else:
                records.append((resource, mode))
        return records, outcomes

    def search(
        self,
        resource_type: str,
        *,
        count: int = 50,
        since: str | None = None,
        include: list[str] | None = None,
        max_pages: int | None = None,
        max_resources: int | None = None,
        extra_params: dict[str, Any] | None = None,
    ) -> Iterator[tuple[dict, str]]:
        """Yield (resource, search_mode) across every page of a search.

        `search_mode` is `"match"` for a result and `"include"` for a resource
        pulled in by `_include`. Counting includes as results is the classic way
        an `_include` query reports twice the rows it found.

        Diagnostic outcome entries are counted in stats.outcomes rather than
        yielded as clinical resources. max_resources counts matches and includes.
        """
        self.validate_search_limits(count=count, max_pages=max_pages, max_resources=max_resources)
        if max_pages == 0 or max_resources == 0:
            return
        params: dict[str, Any] = {"_count": count}
        if since:
            params["_since"] = since
        if include:
            params["_include"] = include
        if extra_params:
            params.update(extra_params)

        url: str | None = f"{self.base_url}/{resource_type}"
        page = 0
        emitted = 0
        seen_urls: set[str] = set()

        while url:
            request_url = str(self._client.build_request("GET", url, params=params).url)
            if request_url in seen_urls:
                raise FhirServerError(
                    "repeated pagination URL; search stopped before replaying a page"
                )
            seen_urls.add(request_url)
            bundle = self._get(url, params=params)
            # Every page after the first uses the server's continuation URL,
            # which already carries the search state. Re-sending the original
            # parameters alongside it is how a paginated pull silently restarts
            # from page one and loops forever.
            params = None
            page += 1
            self.stats.pages += 1

            if bundle.get("resourceType") != "Bundle":
                raise FhirServerError(
                    f"expected a Bundle, got {bundle.get('resourceType')!r}"
                )

            records, outcomes = self._search_entries(bundle)
            next_url = self.next_link(bundle)
            self.stats.outcomes += outcomes
            for resource, mode in records:
                if mode == "match":
                    self.stats.matched += 1
                else:
                    self.stats.included += 1
                yield resource, mode
                emitted += 1
                if max_resources is not None and emitted >= max_resources:
                    return

            if max_pages is not None and page >= max_pages:
                return
            url = next_url

    def capabilities(self) -> dict:
        """The server's CapabilityStatement — the honest way to find out what a
        server supports before assuming it supports what you need."""
        return self._get(f"{self.base_url}/metadata", params={"_summary": "true"})
