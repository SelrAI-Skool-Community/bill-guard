"""Read-only, injectable registry lookup adapters with dated caching."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .ledger import Ledger

Transport = Callable[[str, float], bytes]


@dataclass(frozen=True)
class LookupResult:
    status: str                 # found | not_found | unknown
    source: str
    observed_at: str
    data: dict[str, Any] = field(default_factory=dict)
    cache: str = "miss"         # miss | fresh | stale
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def http_get(url: str, timeout: float) -> bytes:
    """Perform a read-only HTTP GET with an explicit timeout."""
    request = urllib.request.Request(
        url, headers={"User-Agent": "BillGuard/1 lookup (read-only)"},
        method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


class LookupClient(ABC):
    """Common result, cache, timeout, and failure contract."""
    namespace = "lookup"
    source = ""

    def __init__(self, ledger: Ledger, *, transport: Transport = http_get,
                 timeout: float = 5.0, max_age: timedelta = timedelta(days=7),
                 now: Callable[[], datetime] | None = None):
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.ledger, self.transport = ledger, transport
        self.timeout, self.max_age = timeout, max_age
        self.now = now or (lambda: datetime.now(timezone.utc))

    def lookup(self, value: str) -> LookupResult:
        at = self.now()
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        try:
            normalized = self.normalize(value)
        except (AttributeError, TypeError, ValueError) as exc:
            return LookupResult("unknown", self.source, at.isoformat(),
                                error=f"{type(exc).__name__}: {exc}")
        cache_key = f"{self.namespace}:{normalized}"
        cached = self.ledger.registry_answers(cache_key)
        if cached:
            age = at - _parse_datetime(cached[0]["asked_at"])
            if timedelta(0) <= age <= self.max_age:
                return _result_from_cache(cached[0], "fresh")
        try:
            payload = self.transport(self.url(normalized), self.timeout)
            status, data = self.parse(payload, normalized)
            if status not in ("found", "not_found"):
                raise ValueError("parser returned an invalid status")
            result = LookupResult(status, self.source, at.isoformat(), data)
            self.ledger.cache_registry(cache_key, result.observed_at,
                                       result.to_dict())
            return result
        except (AttributeError, TimeoutError, urllib.error.URLError, UnicodeError,
                json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            return LookupResult("unknown", self.source, at.isoformat(), {},
                                "stale" if cached else "miss",
                                f"{type(exc).__name__}: {exc}")

    def normalize(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("lookup value is empty")
        return value

    @abstractmethod
    def url(self, normalized: str) -> str:
        """Return the read-only lookup URL for a normalized value."""

    @abstractmethod
    def parse(self, payload: bytes, normalized: str) -> tuple[str, dict]:
        """Parse a registry response and verify it matches the request."""


class AbrClient(LookupClient):
    """ABR ABN Lookup JSON adapter; the user supplies its free GUID."""
    namespace = "abr"
    source = "Australian Business Register ABN Lookup"
    endpoint = "https://abr.business.gov.au/json/AbnDetails.aspx"

    def __init__(self, ledger: Ledger, *, guid: str, **kwargs):
        super().__init__(ledger, **kwargs)
        self.guid = guid.strip()

    def normalize(self, value: str) -> str:
        digits = re.sub(r"\D", "", value)
        if len(digits) != 11:
            raise ValueError("ABN must contain 11 digits")
        return digits

    def url(self, normalized: str) -> str:
        if not self.guid:
            raise ValueError("ABR lookup requires a free ABN Lookup GUID")
        return self.endpoint + "?" + urllib.parse.urlencode(
            {"abn": normalized, "guid": self.guid})

    def parse(self, payload: bytes, normalized: str) -> tuple[str, dict]:
        obj = _json_object(payload)
        if obj.get("Message"):
            return "not_found", {"abn": normalized, "message": obj["Message"]}
        abn = re.sub(r"\D", "", str(obj.get("Abn", "")))
        if abn != normalized:
            raise ValueError("ABR response ABN does not match the request")
        return "found", {"abn": abn,
                         "name": obj.get("EntityName") or obj.get("MainName"),
                         "status": obj.get("AbnStatus"),
                         "gst_from": obj.get("Gst") or None}


class BsbClient(LookupClient):
    """Adapter for a caller-selected free BSB-directory JSON endpoint."""
    namespace = "bsb"
    source = "Australian Payments Network BSB directory"

    def __init__(self, ledger: Ledger, *, endpoint: str, **kwargs):
        super().__init__(ledger, **kwargs)
        self.endpoint = endpoint.rstrip("/")

    def normalize(self, value: str) -> str:
        digits = re.sub(r"\D", "", value)
        if len(digits) != 6:
            raise ValueError("BSB must contain 6 digits")
        return digits

    def url(self, normalized: str) -> str:
        if not self.endpoint.startswith("https://"):
            raise ValueError("BSB endpoint must use HTTPS")
        return self.endpoint + "?" + urllib.parse.urlencode({"bsb": normalized})

    def parse(self, payload: bytes, normalized: str) -> tuple[str, dict]:
        obj = _json_object(payload)
        if obj.get("found") is False:
            return "not_found", {"bsb": normalized}
        bsb = re.sub(r"\D", "", str(obj.get("bsb", "")))
        if bsb != normalized:
            raise ValueError("BSB response does not match the request")
        return "found", {"bsb": bsb, "institution": obj.get("institution"),
                         "branch": obj.get("branch"), "address": obj.get("address")}


class SanctionsClient(LookupClient):
    """Exact-name adapter over a caller-selected free DFAT JSON endpoint."""
    namespace = "sanctions"
    source = "Australian DFAT Consolidated List"

    def __init__(self, ledger: Ledger, *, endpoint: str, **kwargs):
        super().__init__(ledger, **kwargs)
        self.endpoint = endpoint.rstrip("/")

    def normalize(self, value: str) -> str:
        value = " ".join(value.casefold().split())
        if not value:
            raise ValueError("sanctions name is empty")
        return value

    def url(self, normalized: str) -> str:
        if not self.endpoint.startswith("https://"):
            raise ValueError("sanctions endpoint must use HTTPS")
        return self.endpoint + "?" + urllib.parse.urlencode({"name": normalized})

    def parse(self, payload: bytes, normalized: str) -> tuple[str, dict]:
        obj = _json_object(payload)
        matches = obj.get("matches")
        if not isinstance(matches, list):
            raise ValueError("sanctions response has no matches list")
        exact = [m for m in matches if isinstance(m, dict) and
                 " ".join(str(m.get("name", "")).casefold().split()) == normalized]
        return ("found" if exact else "not_found",
                {"query": normalized, "exact_matches": exact})


def _json_object(payload: bytes) -> dict:
    obj = json.loads(payload.decode("utf-8-sig"))
    if not isinstance(obj, dict):
        raise ValueError("response must be a JSON object")
    return obj


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _result_from_cache(item: dict, cache: str) -> LookupResult:
    data = item["response"]
    return LookupResult(data["status"], data["source"], data["observed_at"],
                        data.get("data", {}), cache, data.get("error"))
