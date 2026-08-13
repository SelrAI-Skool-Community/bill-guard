"""Reading real invoices, the way a business owner actually has them."""

from harness import test, eq, true, false, main
from billguard import intake
from billguard.model import Channel

CLEAN = """Tiles by Morrissey
ABN 98 273 029 681

TAX INVOICE

Invoice Number: INV-19092
Invoice Date: 23/07/2026
Due Date: 06/08/2026

Subtotal                               971.00
GST                                     97.10
Total incl GST                        1068.10
Amount Due                            1068.10

Payment details
BSB: 062-000
Account Number: 12345678
"""


@test
def reads_a_plain_invoice():
    ex = intake.from_text(CLEAN)
    d = ex.document
    eq(d.supplier_abn, "98273029681")
    eq(d.invoice_number, "INV-19092")
    eq(d.issue_date, "2026-07-23")
    eq(d.due_date, "2026-08-06")
    eq(d.subtotal_cents, 97100)
    eq(d.total_cents, 106810)
    eq(d.balance_due_cents, 106810)


@test
def a_total_line_mentioning_gst_is_not_the_gst():
    """'Total incl GST 1068.10' contains the word GST and is not the GST."""
    ex = intake.from_text(CLEAN)
    eq(ex.document.tax_cents, 9710, "must be 97.10, not the total")


@test
def arithmetic_overrides_a_misread_tax_line():
    text = CLEAN.replace("GST                                     97.10",
                         "GST                                    971.00")
    ex = intake.from_text(text)
    eq(ex.document.tax_cents, 9710, "subtotal and total imply the GST")
    true(any("arithmetic was used" in g for g in ex.gaps),
         "must say it overrode the printed figure")


@test
def a_valid_abn_is_read_with_certainty():
    ex = intake.from_text(CLEAN)
    eq(ex.confidence["supplier_abn"], 1.0)


@test
def an_abn_failing_its_checksum_is_reported():
    ex = intake.from_text(CLEAN.replace("98 273 029 681", "98 273 029 682"))
    true(any("fails its checksum" in g for g in ex.gaps))


@test
def payment_block_needs_both_halves():
    ex = intake.from_text(CLEAN.replace("Account Number: 12345678", ""))
    eq(ex.document.payment.bsb, None, "a BSB with no account is unusable")
    true(any("incomplete" in g for g in ex.gaps))


@test
def payment_block_confidence_is_reported():
    ex = intake.from_text(CLEAN)
    true(ex.document.payment.confidence >= 0.9)
    eq(ex.document.payment.bsb, "062-000")
    eq(ex.document.payment.account_number, "12345678")


@test
def a_receipt_is_recognised_by_its_words():
    ex = intake.from_text("Payment received. Thanks for your payment.\n"
                          "Invoice Number: INV-1\nTotal 50.00\nABN 98 273 029 681")
    eq(ex.document.balance_due_cents, 0)


@test
def junk_is_not_an_invoice():
    ex = intake.from_text("hello world, nothing here at all")
    false(ex.looks_like_an_invoice)


@test
def a_real_invoice_is_recognised_as_one():
    true(intake.from_text(CLEAN).looks_like_an_invoice)


@test
def a_missing_file_reports_plainly():
    text, gaps, meta = intake.read_file("/nonexistent/nope.pdf")
    eq(text, "")
    true(any("no file" in g for g in gaps))


@test
def an_unsupported_type_says_so():
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    try:
        text, gaps, meta = intake.read_file(path)
        true(any("not supported" in g for g in gaps))
    finally:
        os.unlink(path)


@test
def a_photo_reports_the_capability_gap_not_an_empty_pass():
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    try:
        text, gaps, meta = intake.read_file(path)
        eq(text, "")
        true(any("character recognition" in g for g in gaps),
             "must name what is missing, never silently return nothing")
        eq(meta.get("channel"), Channel.PHOTO)
    finally:
        os.unlink(path)


if __name__ == "__main__":
    main("test_intake")
