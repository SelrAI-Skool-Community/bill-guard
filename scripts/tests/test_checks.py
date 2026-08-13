"""The check register, and above all the one control that matters.

D01 -- did the payee bank details change since we last PAID this supplier --
outranks every other technique in this system combined. It gets the most
tests.
"""

from harness import test, eq, true, false, main
from billguard import assess
from billguard.checks import run_all
from billguard.ledger import Ledger
from billguard.lookups import LookupResult
from billguard.model import (
    Channel, Document, FPRisk, LineItem, PaymentInstruction, Severity, Status,
)
from billguard.verdict import HOLD, QUERY, SAFE, SETTLED


def _doc(**kw) -> Document:
    base = dict(
        doc_id="doc-1",
        channel=Channel.MAILBOX,
        supplier_name="Tiles by Morrissey Trust",
        supplier_abn="98273029681",
        invoice_number="INV-19092",
        issue_date="2026-07-23",
        currency="AUD",
        subtotal_cents=97100,
        tax_cents=9710,
        total_cents=106810,
        balance_due_cents=106810,
        raw_text="TAX INVOICE",
        doc_type_words="TAX INVOICE",
    )
    base.update(kw)
    doc = Document(**base)
    if "payment" not in kw:
        doc.payment = PaymentInstruction(
            bsb="062-000", account_number="12345678",
            account_name="Tiles by Morrissey", confidence=0.99, source="text")
    return doc


def _by_id(results, check_id):
    for r in results:
        if r.check_id == check_id:
            return r
    return None


def _ledger_with_history(fp_paid: str | None = None) -> Ledger:
    led = Ledger(":memory:")
    key = "abn:98273029681"
    led.upsert_supplier(key, "Tiles by Morrissey Trust", "98273029681",
                        "2026-01-05")
    led.record_sender(key, "post.xero.com", "2026-01-05", is_relay=True)
    if fp_paid:
        led.record_destination(key, fp_paid, {"bsb": "062-000"}, "2026-01-05")
        led.mark_destination_paid(key, fp_paid, "2026-01-10")
    return led


class _FixedLookup:
    def __init__(self, status="found", data=None):
        self.result = LookupResult(
            status, "offline ABR fixture", "2026-08-13T00:00:00+00:00",
            data or {"status": "Active"}, "fresh")

    def lookup(self, value):
        return self.result


# ===========================================================================
# D01 -- the control
# ===========================================================================

@test
def unchanged_bank_details_pass():
    doc = _doc()
    led = _ledger_with_history(doc.payment.fingerprint())
    r = _by_id(run_all(doc, led), "D01")
    eq(r.status, Status.PASS)
    led.close()


@test
def changed_bank_details_are_a_hold():
    led = _ledger_with_history("au:062000:12345678")
    doc = _doc()
    doc.payment = PaymentInstruction(
        bsb="083-004", account_number="99887766", confidence=0.99)
    r = _by_id(run_all(doc, led), "D01")
    eq(r.status, Status.FAIL)
    eq(r.severity, Severity.HOLD)
    true("do not match" in r.evidence.lower())
    true("phone" in r.detail["action"].lower(),
         "must route to out-of-band verification")
    true("never one printed on this document" in r.detail["action"],
         "must forbid the number on the invoice itself")
    led.close()


@test
def a_seen_but_never_paid_destination_is_not_a_baseline():
    """An attacker's account is 'seen' the instant their invoice lands."""
    led = Ledger(":memory:")
    key = "abn:98273029681"
    led.upsert_supplier(key, "Tiles", "98273029681", "2026-01-05")
    led.record_destination(key, "au:062000:12345678", {}, "2026-01-05")
    doc = _doc()
    r = _by_id(run_all(doc, led), "D01")
    eq(r.status, Status.UNKNOWN, "seen-but-unpaid must not be treated as known")
    true("never recorded as paid" in r.evidence)
    led.close()


@test
def first_ever_invoice_is_onboarding_not_fraud():
    led = Ledger(":memory:")
    doc = _doc()
    r = _by_id(run_all(doc, led), "D01")
    eq(r.status, Status.NOT_APPLICABLE)
    true("onboarding" in r.evidence)
    led.close()


@test
def missing_payment_details_is_unknown_not_pass():
    led = _ledger_with_history("au:062000:12345678")
    doc = _doc(payment=PaymentInstruction())
    r = _by_id(run_all(doc, led), "D01")
    eq(r.status, Status.UNKNOWN)
    led.close()


@test
def fingerprint_ignores_formatting_but_not_the_account():
    a = PaymentInstruction(bsb="062-000", account_number="12345678")
    b = PaymentInstruction(bsb="062000", account_number="12 345 678")
    c = PaymentInstruction(bsb="062-000", account_number="12345679")
    eq(a.fingerprint(), b.fingerprint(), "formatting must not matter")
    true(a.fingerprint() != c.fingerprint(), "a changed account must matter")


# ===========================================================================
# payment block confidence and codes
# ===========================================================================

@test
def low_confidence_payment_block_escalates():
    doc = _doc()
    doc.payment.confidence = 0.55
    r = _by_id(run_all(doc, None), "D03")
    eq(r.status, Status.FAIL)
    eq(r.severity, Severity.QUERY)
    true("single wrong digit" in r.evidence)


@test
def two_payment_codes_on_a_page_is_a_hold():
    doc = _doc()
    doc.artifacts["qr_codes"] = [
        {"scheme": "emvco", "destination": "a"},
        {"scheme": "emvco", "destination": "b"},
    ]
    r = _by_id(run_all(doc, None), "D04")
    eq(r.status, Status.FAIL)
    eq(r.severity, Severity.HOLD)
    true("pasted over" in r.evidence)


@test
def one_payment_code_is_fine():
    doc = _doc()
    doc.artifacts["qr_codes"] = [{"scheme": "emvco"}]
    eq(_by_id(run_all(doc, None), "D04").status, Status.PASS)


@test
def a_broken_code_checksum_is_a_hold():
    doc = _doc()
    doc.artifacts["qr_codes"] = [{"scheme": "emvco", "checksum_ok": False}]
    r = _by_id(run_all(doc, None), "D05b")
    eq(r.status, Status.FAIL)
    eq(r.severity, Severity.HOLD)


# ===========================================================================
# identity and legal
# ===========================================================================

@test
def invalid_abn_is_a_hold():
    doc = _doc(supplier_abn="12345678901")
    r = _by_id(run_all(doc, None), "B01")
    eq(r.status, Status.FAIL)
    eq(r.severity, Severity.HOLD)
    eq(r.fp_risk, FPRisk.NONE)


@test
def missing_abn_warns_against_a_name_lookup():
    doc = _doc(supplier_abn=None)
    r = _by_id(run_all(doc, None), "B01")
    eq(r.status, Status.FAIL)
    true("by name" in r.evidence,
         "must warn against the name lookup that returned a wrong company")


@test
def buyer_identity_required_above_one_thousand():
    doc = _doc(total_cents=100000, buyer_name=None, buyer_abn=None)
    r = _by_id(run_all(doc, None), "B02")
    eq(r.status, Status.FAIL)

    doc = _doc(total_cents=99999, buyer_name=None, buyer_abn=None)
    eq(_by_id(run_all(doc, None), "B02").status, Status.NOT_APPLICABLE)

    doc = _doc(total_cents=100000, buyer_abn="40156575753")
    eq(_by_id(run_all(doc, None), "B02").status, Status.PASS)


@test
def totals_that_do_not_add_up_are_flagged():
    doc = _doc(subtotal_cents=97100, tax_cents=9710, total_cents=120000)
    r = _by_id(run_all(doc, None), "F02")
    eq(r.status, Status.FAIL)
    eq(r.fp_risk, FPRisk.NONE)


@test
def legal_rounding_does_not_flag():
    doc = _doc(subtotal_cents=97100, tax_cents=9710, total_cents=106813)
    eq(_by_id(run_all(doc, None), "F02").status, Status.PASS)


@test
def gst_arithmetic_on_a_wholly_taxable_invoice():
    eq(_by_id(run_all(_doc(), None), "F04").status, Status.PASS)
    doc = _doc(tax_cents=5000)
    eq(_by_id(run_all(doc, None), "F04").status, Status.FAIL)


@test
def gst_not_applicable_in_foreign_currency():
    doc = _doc(currency="USD")
    eq(_by_id(run_all(doc, None), "F04").status, Status.NOT_APPLICABLE)


@test
def withholding_computed_when_no_abn():
    doc = _doc(supplier_abn=None, subtotal_cents=100000)
    r = _by_id(run_all(doc, None), "F05")
    eq(r.status, Status.FAIL)
    eq(r.detail["withhold_cents"], 47000)


@test
def no_withholding_under_the_threshold():
    doc = _doc(supplier_abn=None, subtotal_cents=5000, total_cents=5500,
               tax_cents=500)
    eq(_by_id(run_all(doc, None), "F05").status, Status.PASS)


@test
def line_items_must_sum_to_subtotal():
    doc = _doc()
    doc.line_items = [LineItem("tiles", amount_cents=50000),
                      LineItem("labour", amount_cents=47100)]
    eq(_by_id(run_all(doc, None), "F03").status, Status.PASS)

    doc.line_items = [LineItem("tiles", amount_cents=50000)]
    eq(_by_id(run_all(doc, None), "F03").status, Status.FAIL)


# ===========================================================================
# duplicates
# ===========================================================================

@test
def a_zero_balance_document_is_a_receipt_not_a_bill():
    """A receipt must never read as a fraud hold.

    Found on real data: 8 of 15 genuine invoices from Luke's inbox were
    payment confirmations, and every one came back HOLD. That is how a
    checker teaches its user to ignore it.
    """
    doc = _doc(balance_due_cents=0)
    r = _by_id(run_all(doc, None), "G02")
    eq(r.status, Status.FAIL)
    eq(r.severity, Severity.SETTLED, "a receipt is settled, not a hold")
    true("nothing to action" in r.evidence)

    v = assess(doc, None)
    eq(v.outcome, SETTLED)


@test
def a_receipt_with_a_real_hold_still_holds():
    """Settled outranks noise, never a genuine hold."""
    led = _ledger_with_history("au:062000:12345678")
    doc = _doc(balance_due_cents=0)
    doc.payment = PaymentInstruction(bsb="083-004", account_number="99887766",
                                     confidence=0.99)
    v = assess(doc, led)
    eq(v.outcome, HOLD, "an altered receipt is still worth stopping on")
    led.close()


@test
def an_overseas_supplier_is_not_asked_for_an_abn():
    """Applying the Australian rule to a US or Dutch company raises a
    finding on every single overseas invoice."""
    for name in ("Anthropic, PBC", "Vercel Inc.", "Framer B.V.",
                 "Eleven Labs Inc."):
        doc = _doc(supplier_name=name, supplier_abn=None, currency=None)
        eq(_by_id(run_all(doc, None), "B01").status, Status.NOT_APPLICABLE,
           name)
        eq(_by_id(run_all(doc, None), "F05").status, Status.NOT_APPLICABLE,
           name)


@test
def an_australian_supplier_with_no_abn_is_still_a_finding():
    doc = _doc(supplier_name="Bob's Plumbing Pty Ltd", supplier_abn=None)
    eq(_by_id(run_all(doc, None), "B01").status, Status.FAIL)


@test
def foreign_currency_alone_marks_a_supplier_overseas():
    doc = _doc(supplier_name="Ambiguous Trading", supplier_abn=None,
               currency="USD")
    eq(_by_id(run_all(doc, None), "B01").status, Status.NOT_APPLICABLE)


@test
def an_email_body_cannot_answer_whether_it_is_a_tax_invoice():
    """The words live in the attached PDF, not the covering email."""
    doc = _doc(doc_type_words="", raw_text="Please find your invoice attached",
               text_source="email_body")
    eq(_by_id(run_all(doc, None), "F01").status, Status.UNKNOWN)

    doc = _doc(doc_type_words="", raw_text="Invoice 123 amount due",
               text_source="document")
    eq(_by_id(run_all(doc, None), "F01").status, Status.FAIL)


@test
def a_receipt_with_no_payment_details_is_not_the_phone_scam():
    """A card-charged receipt legitimately has no bank details."""
    doc = _doc(balance_due_cents=0, payment=PaymentInstruction(),
               raw_text="Thanks for your payment. Contact our support team "
                        "with any questions.")
    eq(_by_id(run_all(doc, None), "H02").status, Status.NOT_APPLICABLE)


@test
def a_renewal_notice_demanding_nothing_is_not_the_phone_scam():
    """Found on real data: a subscription renewal reminder with no amount,
    no invoice number and no payment request came back HOLD."""
    doc = _doc(total_cents=None, balance_due_cents=None, subtotal_cents=None,
               tax_cents=None, payment=PaymentInstruction(),
               raw_text="Your subscription will renew on August 16. Your "
                        "payment method on file will be charged. Contact our "
                        "support team with any questions.")
    eq(_by_id(run_all(doc, None), "H02").status, Status.NOT_APPLICABLE)
    eq(assess(doc, None).outcome, QUERY, "a notice is never a hold")


@test
def an_amount_owed_with_no_way_to_pay_still_fires():
    doc = _doc(balance_due_cents=49900, payment=PaymentInstruction(),
               raw_text="Your subscription renewed for $499.00. "
                        "To cancel please call our support team.")
    r = _by_id(run_all(doc, None), "H02")
    eq(r.status, Status.FAIL)
    eq(r.severity, Severity.HOLD)


@test
def missing_balance_field_is_unknown_not_pass():
    doc = _doc(balance_due_cents=None)
    eq(_by_id(run_all(doc, None), "G02").status, Status.UNKNOWN)


@test
def same_number_with_changed_payee_is_the_fraud_shape():
    led = _ledger_with_history("au:062000:12345678")
    key = "abn:98273029681"
    led.record_document("old-1", "hash-old", key, "INV-19092", "2026-07-23",
                        106810, "AUD", "mailbox", "2026-07-23",
                        payload={"payment_fingerprint": "au:062000:12345678"})
    doc = _doc(doc_id="doc-2")
    doc.payment = PaymentInstruction(bsb="083-004", account_number="99887766",
                                     confidence=0.99)
    r = _by_id(run_all(doc, led), "G01")
    eq(r.status, Status.FAIL)
    eq(r.severity, Severity.HOLD)
    true("altered destination" in r.evidence)
    led.close()


@test
def same_number_same_payee_is_only_a_query():
    fp = "au:062000:12345678"
    led = _ledger_with_history(fp)
    key = "abn:98273029681"
    led.record_document("old-1", "hash-old", key, "INV-19092", "2026-07-23",
                        106810, "AUD", "mailbox", "2026-07-23",
                        payload={"payment_fingerprint": fp})
    doc = _doc(doc_id="doc-2")
    r = _by_id(run_all(doc, led), "G01")
    eq(r.status, Status.FAIL)
    eq(r.severity, Severity.QUERY, "progress billing reuses numbers legitimately")
    led.close()


# ===========================================================================
# channel honesty
# ===========================================================================

@test
def an_inline_forward_cannot_claim_sender_checks():
    doc = _doc(channel=Channel.FORWARD_INLINE)
    r = _by_id(run_all(doc, None), "A01")
    eq(r.status, Status.UNKNOWN, "unavailable, not failed")
    true("forward as an attachment" in r.evidence)


@test
def a_photo_has_no_email_forensics_at_all():
    doc = _doc(channel=Channel.PHOTO)
    r = _by_id(run_all(doc, None), "A01")
    eq(r.status, Status.UNKNOWN)
    true("no email forensics" in r.evidence)


@test
def a_new_sender_domain_is_a_weak_signal_only():
    led = _ledger_with_history("au:062000:12345678")
    doc = _doc(supplier_domain="notification.intuit.com")
    r = _by_id(run_all(doc, led), "A02")
    eq(r.status, Status.FAIL)
    eq(r.fp_risk, FPRisk.HIGH, "suppliers switch platforms routinely")
    led.close()


# ===========================================================================
# scam shapes
# ===========================================================================

@test
def a_solicitation_disclaimer_is_a_hold():
    doc = _doc(raw_text="THIS IS NOT A BILL. THIS IS A SOLICITATION.")
    r = _by_id(run_all(doc, None), "H01")
    eq(r.status, Status.FAIL)
    eq(r.severity, Severity.HOLD)
    eq(r.fp_risk, FPRisk.NONE)


@test
def a_normal_invoice_has_no_disclaimer():
    eq(_by_id(run_all(_doc(), None), "H01").status, Status.PASS)


@test
def an_amount_with_no_way_to_pay_but_a_number_to_call():
    doc = _doc(payment=PaymentInstruction(),
               raw_text="Your subscription renewed for $499.00. "
                        "To cancel please call our support team.")
    r = _by_id(run_all(doc, None), "H02")
    eq(r.status, Status.FAIL)
    eq(r.severity, Severity.HOLD)
    true("wants to be paid" in r.evidence)


# ===========================================================================
# end to end
# ===========================================================================

@test
def a_clean_known_invoice_is_safe_to_pay():
    doc = _doc(buyer_abn="40156575753", supplier_domain="post.xero.com")
    led = _ledger_with_history(doc.payment.fingerprint())
    v = assess(doc, led, {"lookup_clients": {"abr": _FixedLookup()}})
    eq(v.outcome, SAFE, f"reasons: {v.reasons} unchecked: {v.not_checked}")
    led.close()


@test
def a_changed_account_produces_a_hold_end_to_end():
    led = _ledger_with_history("au:062000:12345678")
    doc = _doc(buyer_abn="40156575753")
    doc.payment = PaymentInstruction(bsb="083-004", account_number="99887766",
                                     confidence=0.99)
    v = assess(doc, led)
    eq(v.outcome, HOLD)
    true(any("do not match" in r.lower() for r in v.reasons))
    led.close()


@test
def every_verdict_states_the_bank_ownership_limit():
    """Silence about this would be the one dishonest thing in the product."""
    led = _ledger_with_history("au:062000:12345678")
    for doc in (_doc(buyer_abn="40156575753"),
                _doc(supplier_abn="12345678901"),
                _doc(balance_due_cents=0)):
        v = assess(doc, led)
        true(any("account ownership was not verified" in lim
                 for lim in v.limits), f"{v.outcome} verdict dropped the limit")
    led.close()


@test
def unknown_material_checks_block_a_green_verdict():
    doc = _doc(buyer_abn="40156575753")
    v = assess(doc, None)          # no ledger: D01 cannot run
    eq(v.outcome, QUERY, "a green verdict must not rest on checks that "
                         "never ran")
    true(v.not_checked)


@test
def checks_never_raise_even_on_a_nonsense_document():
    doc = Document()
    results = run_all(doc, None)
    true(len(results) > 0)
    for r in results:
        true(r.status in (Status.PASS, Status.FAIL, Status.UNKNOWN,
                          Status.NOT_APPLICABLE), f"{r.check_id}: {r.status}")


if __name__ == "__main__":
    main("test_checks")
