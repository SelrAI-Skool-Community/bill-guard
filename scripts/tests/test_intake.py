"""Reading real invoices, the way a business owner actually has them."""

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from harness import test, eq, true, false, main
from billguard import intake
from billguard import cli
from billguard.model import Channel
from billguard.qr import DecodedCode, PaymentPayload

FIXTURES = Path(__file__).resolve().parents[2] / "examples" / "intake"

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
def text_fixtures_have_exactly_one_outcome_each():
    expected = {
        "clean.txt": intake.IntakeOutcome.INVOICE,
        "truncated.txt": intake.IntakeOutcome.PARTIAL,
        "rubbish.txt": intake.IntakeOutcome.NOT_INVOICE,
    }
    for name, outcome in expected.items():
        ex = intake.from_file(str(FIXTURES / name))
        eq(ex.outcome, outcome, name)
        eq(sum(ex.outcome is candidate for candidate in intake.IntakeOutcome),
           1, f"{name} must land in exactly one outcome")


@test
def every_clean_fixture_field_has_confidence():
    ex = intake.from_file(str(FIXTURES / "clean.txt"))
    expected = {
        "supplier_name", "supplier_abn", "invoice_number", "issue_date",
        "due_date", "currency", "subtotal_cents", "tax_cents",
        "total_cents", "balance_due_cents", "payment.bsb",
        "payment.account_number",
    }
    eq(set(ex.confidence), expected)
    true(all(0 < score <= 1 for score in ex.confidence.values()))


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


@test
def a_pdf_text_layer_is_extracted_via_pdftotext():
    completed = CompletedProcess([], 0, stdout=CLEAN.encode(), stderr=b"")
    with patch.object(intake, "_pdftotext_path", return_value="/bin/pdftotext"), \
            patch.object(intake.subprocess, "run", return_value=completed) as run:
        text, gaps = intake.text_from_pdf("invoice.pdf")

    eq(text, CLEAN)
    eq(gaps, [])
    eq(run.call_args.args[0],
       ["/bin/pdftotext", "-layout", "-q", "invoice.pdf", "-"])
    eq(run.call_args.kwargs["timeout"], 60)


@test
def a_text_pdf_normalises_to_a_document_through_file_intake():
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    completed = CompletedProcess([], 0, stdout=CLEAN.encode(), stderr=b"")
    try:
        with patch.object(intake, "_pdftotext_path",
                          return_value="/bin/pdftotext"), \
                patch.object(intake.subprocess, "run", return_value=completed), \
                patch.object(intake, "_decode_codes", return_value=[]):
            ex = intake.from_file(path)
        eq(ex.outcome, intake.IntakeOutcome.INVOICE)
        eq(ex.document.invoice_number, "INV-19092")
        eq(ex.document.channel, Channel.UPLOAD)
        eq(ex.source_path, path)
    finally:
        os.unlink(path)


@test
def a_scanned_pdf_names_the_missing_text_layer():
    completed = CompletedProcess([], 0, stdout=b"\f", stderr=b"")
    with patch.object(intake, "_pdftotext_path", return_value="/bin/pdftotext"), \
            patch.object(intake.subprocess, "run", return_value=completed):
        text, gaps = intake.text_from_pdf("scan.pdf")

    eq(text, "\f")
    true(any(g.startswith(intake.PDF_TEXT_LAYER_MISSING) for g in gaps))


@test
def a_broken_pdf_is_not_misreported_as_a_scan():
    completed = CompletedProcess([], 1, stdout=b"", stderr=b"syntax error")
    with patch.object(intake, "_pdftotext_path", return_value="/bin/pdftotext"), \
            patch.object(intake.subprocess, "run", return_value=completed):
        text, gaps = intake.text_from_pdf("broken.pdf")

    eq(text, "")
    true(any(g.startswith(intake.PDF_EXTRACTION_FAILED) for g in gaps))
    false(any(g.startswith(intake.PDF_TEXT_LAYER_MISSING) for g in gaps))


@test
def pdf_page_rendering_keeps_numeric_page_order():
    import os

    def render_pages(command, **kwargs):
        prefix = command[-1]
        for number in (10, 2, 1):
            open(f"{prefix}-{number}.png", "wb").close()
        return CompletedProcess(command, 0, stdout=b"", stderr=b"")

    with patch.object(intake.shutil, "which", return_value="/bin/pdftoppm"), \
            patch.object(intake.subprocess, "run", side_effect=render_pages):
        pages, tmpdir = intake._render_pdf_pages("invoice.pdf")
    try:
        eq([os.path.basename(path) for path, _ in pages],
           ["page-1.png", "page-2.png", "page-10.png"])
        eq([page for _, page in pages], [0, 1, 2])
    finally:
        if tmpdir:
            intake.shutil.rmtree(tmpdir)


@test
def a_code_hidden_on_the_last_pdf_page_is_found():
    import tempfile, os
    from billguard import qr

    tempdir = tempfile.mkdtemp(prefix="billguard-test-pages-")
    pdf_fd, pdf_path = tempfile.mkstemp(suffix=".pdf")
    os.close(pdf_fd)
    pages = []
    for page in range(3):
        image_path = os.path.join(tempdir, f"page-{page + 1}.png")
        open(image_path, "wb").close()
        pages.append((image_path, page))

    def decode_only_last(path, page=0):
        if page == 2:
            return [DecodedCode("https://pay.example/final", page=page,
                                decoder="fixture")]
        return []

    try:
        with patch.object(intake, "_render_pdf_pages",
                          return_value=(pages, None)), \
                patch.object(qr, "decode_image", side_effect=decode_only_last):
            codes = intake._decode_codes(pdf_path)
        eq(len(codes), 1)
        eq(codes[0]["page"], 2)
        eq(codes[0]["payload"], "https://pay.example/final")
        eq(codes[0]["scheme"], "url")
    finally:
        os.unlink(pdf_path)
        intake.shutil.rmtree(tempdir)


@test
def an_image_is_fed_directly_to_the_code_decoder():
    import tempfile, os
    from billguard import qr

    image_fd, image_path = tempfile.mkstemp(suffix=".png")
    os.close(image_fd)
    decoded = DecodedCode("plain fixture payload", page=0,
                          decoder="fixture")
    try:
        with patch.object(qr, "decode_image", return_value=[decoded]) as decode:
            codes = intake._decode_codes(image_path)
        eq(decode.call_args.args, (image_path, 0))
        eq(codes[0]["payload"], "plain fixture payload")
        eq(codes[0]["page"], 0)
    finally:
        os.unlink(image_path)


@test
def two_codes_attach_as_artifacts_and_hold_through_the_cli():
    """A file check must exercise D04 without prepared JSON artifacts."""
    image_path = FIXTURES / "two-payment-codes.png"
    decoded = [
        DecodedCode("fixture-code-one", page=0, decoder="offline-fixture"),
        DecodedCode("fixture-code-two", page=0, decoder="offline-fixture"),
    ]
    parsed = [
        PaymentPayload("emvco", destination="payid:first@example.test"),
        PaymentPayload("emvco", destination="payid:second@example.test"),
    ]
    output = StringIO()
    with patch.object(intake, "read_file", return_value=(CLEAN, [], {
            "channel": Channel.PHOTO,
    })), patch("billguard.qr.decode_image", return_value=decoded), \
            patch("billguard.qr.parse_payload", side_effect=parsed), \
            redirect_stdout(output):
        exit_code = cli.main(["check", str(image_path), "--json"])

    verdict = json.loads(output.getvalue())
    d04 = next(check for check in verdict["checks"]
               if check["check_id"] == "D04")
    eq(exit_code, cli.EXIT_HOLD)
    eq(verdict["outcome"], "HOLD")
    eq(d04["status"], "fail")
    true("2 separate payment codes" in d04["evidence"])


@test
def failed_pdf_rendering_returns_no_partial_pages():
    def failed_render(command, **kwargs):
        open(f"{command[-1]}-1.png", "wb").close()
        return CompletedProcess(command, 1, stdout=b"", stderr=b"bad pdf")

    with patch.object(intake.shutil, "which", return_value="/bin/pdftoppm"), \
            patch.object(intake.subprocess, "run", side_effect=failed_render):
        pages, tmpdir = intake._render_pdf_pages("broken.pdf")
    eq(pages, [])
    eq(tmpdir, None)


if __name__ == "__main__":
    main("test_intake")
