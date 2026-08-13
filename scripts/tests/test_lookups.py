import json
from datetime import datetime, timedelta, timezone

from harness import eq, test, true
from billguard.ledger import Ledger
from billguard.lookups import AbrClient, BsbClient, SanctionsClient

NOW = datetime(2026, 8, 13, 2, 0, tzinfo=timezone.utc)


class FakeTransport:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def __call__(self, url, timeout):
        self.calls.append((url, timeout))
        if self.error:
            raise self.error
        return json.dumps(self.response).encode()


@test
def abr_success_is_attributed_and_cached_fresh():
    ledger = Ledger(":memory:")
    fake = FakeTransport({"Abn": "51824753556", "EntityName": "Example Pty Ltd",
                          "AbnStatus": "Active", "Gst": "2010-01-01"})
    client = AbrClient(ledger, guid="free-guid", transport=fake, timeout=1.25,
                       now=lambda: NOW)
    first = client.lookup("51 824 753 556")
    second = client.lookup("51824753556")
    eq(first.status, "found")
    eq(first.source, "Australian Business Register ABN Lookup")
    eq(first.data["name"], "Example Pty Ltd")
    eq(second.cache, "fresh")
    eq(len(fake.calls), 1)
    eq(fake.calls[0][1], 1.25)
    true("abn=51824753556" in fake.calls[0][0])


@test
def stale_cache_is_refetched():
    ledger = Ledger(":memory:")
    AbrClient(ledger, guid="g", transport=FakeTransport(
        {"Abn": "51824753556", "EntityName": "Old"}),
        now=lambda: NOW).lookup("51824753556")
    fake = FakeTransport({"Abn": "51824753556", "EntityName": "New"})
    result = AbrClient(ledger, guid="g", transport=fake,
                       max_age=timedelta(days=7),
                       now=lambda: NOW + timedelta(days=8)).lookup("51824753556")
    eq(result.data["name"], "New")
    eq(result.cache, "miss")
    eq(len(fake.calls), 1)


@test
def timeout_and_malformed_responses_are_unknown_not_cached():
    ledger = Ledger(":memory:")
    timeout = BsbClient(ledger, endpoint="https://directory.example/bsb",
                        transport=FakeTransport(error=TimeoutError("late")),
                        now=lambda: NOW)
    eq(timeout.lookup("123-456").status, "unknown")
    malformed = BsbClient(ledger, endpoint="https://directory.example/bsb",
                          transport=lambda url, limit: b"not-json", now=lambda: NOW)
    eq(malformed.lookup("123456").status, "unknown")
    wrong_type = BsbClient(ledger, endpoint="https://directory.example/bsb",
                           transport=lambda url, limit: "not-bytes", now=lambda: NOW)
    eq(wrong_type.lookup("123456").status, "unknown")
    eq(ledger.registry_answers("bsb:123456"), [])


@test
def stale_cache_does_not_turn_transport_failure_into_success():
    ledger = Ledger(":memory:")
    BsbClient(ledger, endpoint="https://directory.example/bsb",
              transport=FakeTransport({"bsb": "123456", "institution": "Bank"}),
              now=lambda: NOW).lookup("123456")
    result = BsbClient(ledger, endpoint="https://directory.example/bsb",
                       transport=FakeTransport(error=TimeoutError("late")),
                       now=lambda: NOW + timedelta(days=8)).lookup("123456")
    eq(result.status, "unknown")
    eq(result.cache, "stale")


@test
def malformed_cache_is_ignored_and_transport_failure_is_unknown():
    ledger = Ledger(":memory:")
    ledger.cache_registry("bsb:123456", "not-a-date", {"status": "found"})
    result = BsbClient(
        ledger, endpoint="https://directory.example/bsb",
        transport=FakeTransport(error=TimeoutError("late")),
        now=lambda: NOW).lookup("123456")
    eq(result.status, "unknown")
    eq(result.cache, "miss")
    true("TimeoutError" in result.error)


@test
def invalid_json_cache_is_ignored_and_fresh_response_is_used():
    ledger = Ledger(":memory:")
    ledger._conn.execute(
        "INSERT INTO registry_cache(key, asked_at, response_json) VALUES(?,?,?)",
        ("bsb:123456", NOW.isoformat(), "{truncated"))
    ledger._conn.commit()
    fake = FakeTransport({"bsb": "123456", "institution": "Bank"})
    result = BsbClient(
        ledger, endpoint="https://directory.example/bsb", transport=fake,
        now=lambda: NOW).lookup("123456")
    eq(result.status, "found")
    eq(result.data["institution"], "Bank")
    eq(result.cache, "miss")
    eq(len(fake.calls), 1)


@test
def structurally_invalid_cache_is_ignored():
    ledger = Ledger(":memory:")
    ledger.cache_registry("bsb:123456", NOW.isoformat(), {
        "status": "found",
        "source": "Australian Payments Network BSB directory",
        "observed_at": NOW.isoformat(),
        "data": 42,
    })
    fake = FakeTransport({"bsb": "123456", "institution": "Bank"})
    result = BsbClient(
        ledger, endpoint="https://directory.example/bsb", transport=fake,
        now=lambda: NOW).lookup("123456")
    eq(result.status, "found")
    eq(result.data["institution"], "Bank")
    eq(result.cache, "miss")
    eq(len(fake.calls), 1)


@test
def future_dated_cache_is_not_used_as_evidence():
    ledger = Ledger(":memory:")
    future = NOW + timedelta(days=1)
    ledger.cache_registry("bsb:123456", future.isoformat(), {
        "status": "found",
        "source": "Australian Payments Network BSB directory",
        "observed_at": future.isoformat(),
        "data": {"bsb": "123456", "institution": "Future Bank"},
    })
    result = BsbClient(
        ledger, endpoint="https://directory.example/bsb",
        transport=FakeTransport(error=TimeoutError("late")),
        now=lambda: NOW).lookup("123456")
    eq(result.status, "unknown")
    eq(result.cache, "miss")


@test
def bsb_not_found_and_sanctions_exact_match_are_deterministic():
    ledger = Ledger(":memory:")
    bsb = BsbClient(ledger, endpoint="https://directory.example/bsb",
                    transport=FakeTransport({"found": False}), now=lambda: NOW)
    eq(bsb.lookup("000000").status, "not_found")
    sanctions = SanctionsClient(
        ledger, endpoint="https://dfat.example/list",
        transport=FakeTransport({"matches": [
            {"name": "Example Person", "reference": "42"},
            {"name": "Example Person Junior", "reference": "43"}]}),
        now=lambda: NOW)
    result = sanctions.lookup("  EXAMPLE   person ")
    eq(result.status, "found")
    eq([m["reference"] for m in result.data["exact_matches"]], ["42"])


if __name__ == "__main__":
    from harness import main
    main("test_lookups")
