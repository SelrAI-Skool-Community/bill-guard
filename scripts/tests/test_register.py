"""Nine-family register coverage and lookup-to-evidence integration."""

from harness import test, eq, true, main
from billguard.checks import REGISTRY, run_all
from billguard.lookups import LookupResult
from billguard.model import Channel, Document, PaymentInstruction, Status


class FakeLookup:
    def __init__(self, result):
        self.result = result
        self.values = []

    def lookup(self, value):
        self.values.append(value)
        return self.result


def _result(status, source, data=None, error=None):
    return LookupResult(status, source, "2026-08-13T00:00:00+00:00",
                        data or {}, "fresh", error)


def _doc(**changes):
    values = dict(
        doc_id="registry-test", channel=Channel.UPLOAD,
        supplier_name="Example Supplies Pty Ltd", supplier_abn="98273029681",
        invoice_number="INV-1", issue_date="2026-08-01",
        due_date="2026-08-20", subtotal_cents=10000, tax_cents=1000,
        total_cents=11000, balance_due_cents=11000, currency="AUD",
        doc_type_words="TAX INVOICE", raw_text="TAX INVOICE",
        text_source="document",
        payment=PaymentInstruction(bsb="062-000", account_number="12345678",
                                   confidence=1.0, source="text"))
    values.update(changes)
    return Document(**values)


def _by_id(results, check_id):
    return next(r for r in results if r.check_id == check_id)


@test
def register_has_a_real_check_in_every_named_family():
    eq({fn.check_id[0] for fn in REGISTRY}, set("ABCDEFGHI"))
    true(all(fn.family for fn in REGISTRY))


@test
def registry_success_becomes_source_attributed_pass_evidence():
    abr = FakeLookup(_result("found", "ABR fixture",
                             {"name": "Example Supplies", "status": "Active"}))
    bsb = FakeLookup(_result("found", "BSB fixture",
                             {"institution": "Example Bank"}))
    sanctions = FakeLookup(_result("not_found", "DFAT fixture"))
    results = run_all(_doc(), None, {"as_of": "2026-08-13",
                                     "lookup_clients": {
                                         "abr": abr, "bsb": bsb,
                                         "sanctions": sanctions}})
    for check_id, source in (("B03", "ABR fixture"),
                             ("D06", "BSB fixture"),
                             ("E01", "DFAT fixture")):
        result = _by_id(results, check_id)
        eq(result.status, Status.PASS)
        true(source in result.evidence)
        true(result.detail["lookup"]["observed_at"].startswith("2026-08-13"))


@test
def negative_registry_answers_fail_with_false_positive_limits():
    results = run_all(_doc(), None, {"lookup_clients": {
        "abr": FakeLookup(_result("not_found", "ABR fixture")),
        "bsb": FakeLookup(_result("not_found", "BSB fixture")),
        "sanctions": FakeLookup(_result("found", "DFAT fixture", {
            "exact_matches": [{"name": "Example Supplies Pty Ltd"}]}))}})
    for check_id in ("B03", "D06", "E01"):
        result = _by_id(results, check_id)
        eq(result.status, Status.FAIL)
        true(result.fp_risk.value in ("none", "low"))
        true(result.evidence)


@test
def missing_or_failed_registry_capability_is_unknown_not_pass():
    without = run_all(_doc(), None, {})
    for check_id in ("B03", "D06", "E01"):
        eq(_by_id(without, check_id).status, Status.UNKNOWN)

    failed = run_all(_doc(), None, {"lookup_clients": {
        "abr": FakeLookup(_result("unknown", "ABR fixture", error="timeout")),
        "bsb": FakeLookup(_result("unknown", "BSB fixture", error="timeout")),
        "sanctions": FakeLookup(_result("unknown", "DFAT fixture",
                                             error="timeout"))}})
    for check_id in ("B03", "D06", "E01"):
        result = _by_id(failed, check_id)
        eq(result.status, Status.UNKNOWN)
        true("timeout" in result.evidence)


@test
def deadline_family_has_pass_fail_and_unknown_evidence():
    passing = _by_id(run_all(_doc(), None, {"as_of": "2026-08-13"}), "I01")
    failing = _by_id(run_all(_doc(due_date="2026-08-01"), None,
                             {"as_of": "2026-08-13"}), "I01")
    missing = _by_id(run_all(_doc(due_date=None), None,
                             {"as_of": "2026-08-13"}), "I01")
    eq(passing.status, Status.PASS)
    eq(failing.status, Status.FAIL)
    eq(missing.status, Status.UNKNOWN)
    true(all(r.evidence for r in (passing, failing, missing)))


if __name__ == "__main__":
    main()
