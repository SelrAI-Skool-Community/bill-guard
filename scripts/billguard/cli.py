"""Command line for Bill Guard.

One entry point, subcommand dispatched. Every command speaks JSON on
request, because the point of this thing is that anything can call it: a
person at a terminal, a scheduled routine, an agent's tool call, or another
system entirely.

    billguard check invoice.pdf             human-readable verdict
    billguard check invoice.pdf --json      machine-readable verdict
    billguard read invoice.pdf              show what was read off it
    pbpaste | billguard check -             check pasted text
    billguard scan-codes invoice.png        decode and explain every code
    billguard ledger stats
    billguard ledger record-payment ...
    billguard selftest
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, assess
from .ledger import Ledger
from .model import (
    Channel, Document, LineItem, PaymentInstruction, to_cents,
)
from .verdict import HOLD, QUERY, SAFE, SETTLED, render_text

EXIT_SAFE = 0
EXIT_QUERY = 1
EXIT_HOLD = 2
EXIT_ERROR = 3

# Public JSON input contract. Version 1 remains tolerant of additive fields so
# newer producers can talk to older Bill Guard readers without failing.
DOCUMENT_SCHEMA_VERSION = 1

_EXIT = {SAFE: EXIT_SAFE, QUERY: EXIT_QUERY, HOLD: EXIT_HOLD,
         SETTLED: EXIT_SAFE}


# ---------------------------------------------------------------------------
# document loading
# ---------------------------------------------------------------------------

def document_from_dict(data: dict) -> Document:
    """Build a Document from plain JSON.

    Money may be given as `total`, a decimal string, or `total_cents`, an
    integer. Decimal strings are converted through integer cents so nothing
    ever touches a float.
    """
    if not isinstance(data, dict):
        raise ValueError("document JSON must be an object")
    schema_version = data.get("schema_version", DOCUMENT_SCHEMA_VERSION)
    if (not isinstance(schema_version, int) or
            isinstance(schema_version, bool) or
            schema_version != DOCUMENT_SCHEMA_VERSION):
        raise ValueError(
            f"unsupported document schema_version {schema_version!r}; "
            f"supported version is {DOCUMENT_SCHEMA_VERSION}")

    def money(name):
        if f"{name}_cents" in data and data[f"{name}_cents"] is not None:
            return int(data[f"{name}_cents"])
        if data.get(name) is not None:
            return to_cents(data[name], name)
        return None

    pay_in = data.get("payment") or {}
    payment = PaymentInstruction(
        bsb=pay_in.get("bsb"),
        account_number=pay_in.get("account_number"),
        account_name=pay_in.get("account_name"),
        iban=pay_in.get("iban"),
        swift=pay_in.get("swift"),
        payid=pay_in.get("payid"),
        bpay_biller=pay_in.get("bpay_biller"),
        bpay_reference=pay_in.get("bpay_reference"),
        crypto_address=pay_in.get("crypto_address"),
        pay_url=pay_in.get("pay_url"),
        confidence=float(pay_in.get("confidence", 1.0)),
        source=pay_in.get("source", "manual"),
    )

    lines = []
    for li in data.get("line_items") or []:
        amt = li.get("amount_cents")
        if amt is None and li.get("amount") is not None:
            amt = to_cents(li["amount"], "line amount")
        unit = li.get("unit_price_cents")
        if unit is None and li.get("unit_price") is not None:
            unit = to_cents(li["unit_price"], "unit price")
        lines.append(LineItem(
            description=li.get("description", ""),
            quantity=li.get("quantity"),
            unit_price_cents=unit,
            amount_cents=amt,
            tax_code=li.get("tax_code"),
            confidence=float(li.get("confidence", 1.0)),
        ))

    try:
        channel = Channel(data.get("channel", "unknown"))
    except ValueError:
        channel = Channel.UNKNOWN

    unknown_keys = sorted(set(data) - DOCUMENT_V1_FIELDS)

    doc = Document(
        doc_id=data.get("doc_id") or "",
        channel=channel,
        received_at=data.get("received_at"),
        supplier_name=data.get("supplier_name"),
        supplier_abn=data.get("supplier_abn"),
        supplier_domain=data.get("supplier_domain"),
        buyer_name=data.get("buyer_name"),
        buyer_abn=data.get("buyer_abn"),
        invoice_number=data.get("invoice_number"),
        issue_date=data.get("issue_date"),
        due_date=data.get("due_date"),
        po_reference=data.get("po_reference"),
        currency=data.get("currency"),
        subtotal_cents=money("subtotal"),
        tax_cents=money("tax"),
        total_cents=money("total"),
        balance_due_cents=money("balance_due"),
        line_items=lines,
        payment=payment,
        doc_type_words=data.get("doc_type_words", ""),
        raw_text=data.get("raw_text", ""),
        text_source=data.get("text_source", "unknown"),
        supplier_country=data.get("supplier_country"),
        artifacts=data.get("artifacts") or {},
        jurisdiction=data.get("jurisdiction", "AU"),
    )
    if not doc.doc_id:
        doc.doc_id = doc.content_hash()
    doc.artifacts.setdefault("_unknown_keys", unknown_keys)
    return doc


#: The frozen version-one input shape. Additive fields are ignored for forward
#: compatibility, but the CLI reports their names so likely typos stay visible.
DOCUMENT_V1_FIELDS = frozenset({
    "schema_version",
    "doc_id", "channel", "received_at", "supplier_name", "supplier_abn",
    "supplier_domain", "supplier_country", "buyer_name", "buyer_abn",
    "invoice_number", "issue_date", "due_date", "po_reference", "currency",
    "subtotal", "subtotal_cents", "tax", "tax_cents", "total", "total_cents",
    "balance_due", "balance_due_cents", "line_items", "payment",
    "doc_type_words", "raw_text", "text_source", "artifacts", "jurisdiction",
})


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_check(args) -> int:
    """Assess whatever the user actually has: a PDF, a photo, an email,
    a text file, pasted text, or a prepared JSON document."""
    from . import intake

    path = Path(args.document)
    gaps: list = []

    if args.document == "-":
        text = sys.stdin.read()
        if not text.strip():
            print("Nothing was piped in. Paste the invoice text and press "
                  "Ctrl-D, or give a file instead.", file=sys.stderr)
            return EXIT_ERROR
        ex = intake.from_text(text)
        doc, gaps = ex.document, ex.gaps
        if not ex.looks_like_an_invoice:
            print(_not_an_invoice("what you pasted"), file=sys.stderr)
            return EXIT_ERROR
    elif not path.exists():
        print(f"There is no file at {args.document}.\n"
              f"Give the path to an invoice: a PDF, a photo, a saved email, "
              f"a text file, or paste the text with:  billguard check -",
              file=sys.stderr)
        return EXIT_ERROR
    elif path.suffix.lower() == ".json":
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            print(f"That file ends in .json but is not valid JSON "
                  f"({exc.msg} at line {exc.lineno}).\n"
                  f"If it is an invoice, rename it or pass the PDF instead.",
                  file=sys.stderr)
            return EXIT_ERROR
        try:
            doc = document_from_dict(data)
        except ValueError as exc:
            print(f"That JSON could not be read as an invoice: {exc}",
                  file=sys.stderr)
            return EXIT_ERROR
        stray = doc.artifacts.get("_unknown_keys") or []
        if stray:
            gaps.append(
                "These fields were not recognised and were ignored: "
                + ", ".join(stray)
                + ". Check them for typos, because anything misspelled here "
                  "was simply not read.")
    else:
        ex = intake.from_file(args.document)
        doc, gaps = ex.document, ex.gaps
        if ex.ok and not ex.looks_like_an_invoice:
            print(_not_an_invoice(args.document), file=sys.stderr)
            for g in gaps:
                print(f"  {g}", file=sys.stderr)
            return EXIT_ERROR
        if not ex.ok and not doc.artifacts.get("qr_codes"):
            print(f"Nothing could be read from {args.document}.",
                  file=sys.stderr)
            for g in gaps:
                print(f"  {g}", file=sys.stderr)
            return EXIT_ERROR

    ledger = Ledger(args.ledger) if args.ledger else None
    try:
        verdict = assess(doc, ledger, {})
        if ledger and args.remember:
            _remember(ledger, doc, verdict)
    finally:
        if ledger:
            ledger.close()

    if args.json:
        out = verdict.to_dict()
        out["doc_id"] = doc.doc_id
        out["version"] = __version__
        out["read_problems"] = gaps
        print(json.dumps(out, indent=2))
    else:
        print(render_text(verdict, doc))
        if gaps:
            print()
            print("Trouble reading this document:")
            for g in gaps:
                print(f"  - {g}")
    return _EXIT[verdict.outcome]


def _not_an_invoice(what: str) -> str:
    return (
        f"{what} does not look like an invoice or a bill.\n"
        f"Nothing in it asks for money: no amount, no invoice number, no "
        f"ABN and no payment details.\n"
        f"Checking it anyway would produce an official-looking verdict about "
        f"a document that was never a bill, so nothing was checked.\n"
        f"Run 'billguard read <file>' to see exactly what was found on it.")


def _remember(ledger: Ledger, doc: Document, verdict) -> None:
    """Record what we saw. Seeing is not paying: D01 depends on the difference."""
    when = doc.received_at or doc.issue_date or ""
    key = doc.supplier_key()
    metadata = doc.artifacts.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    if key:
        ledger.upsert_supplier(key, doc.supplier_name, doc.supplier_abn, when)
        if doc.supplier_domain:
            ledger.record_sender(key, doc.supplier_domain, when)
        fp = doc.payment.fingerprint()
        if fp:
            ledger.record_destination(key, fp, {
                "bsb": doc.payment.bsb,
                "account_number": doc.payment.account_number,
                "account_name": doc.payment.account_name,
                "iban": doc.payment.iban,
            }, when)
    ledger.record_document(
        doc.doc_id, doc.content_hash(), key, doc.invoice_number,
        doc.issue_date, doc.total_cents, doc.currency, doc.channel.value,
        doc.received_at, verdict.outcome,
        payload={
            "payment_fingerprint": doc.payment.fingerprint(),
            "producer_tool": (
                doc.artifacts.get("producer_tool")
                or metadata.get("producer_tool")
                or metadata.get("producer")
            ),
        })
    ledger.add_evidence(when, "assessed", doc.doc_id,
                        detail={"outcome": verdict.outcome})


def cmd_scan_codes(args) -> int:
    from . import qr
    decoders = qr.available_decoders()
    if not decoders:
        print("No barcode decoder is installed on this machine, so no code "
              "could be read. This is reported as not-checked, never as a "
              "pass. Install one with:  pip install opencv-python pyzbar",
              file=sys.stderr)
        if args.json:
            print(json.dumps({"decoders": [], "codes": [],
                              "status": "unknown"}, indent=2))
        return EXIT_ERROR

    import os
    if not os.path.isfile(args.image):
        print(f"There is no file at {args.image}. Nothing was scanned.\n"
              f"Reporting this as clean would be a silent pass.",
              file=sys.stderr)
        return EXIT_ERROR
    if os.path.splitext(args.image)[1].lower() == ".pdf":
        print("This is a PDF. Use 'billguard check' or 'billguard read' on "
              "it, which renders every page and scans them all.",
              file=sys.stderr)
        return EXIT_ERROR

    codes = qr.decode_image(args.image)
    parsed = []
    for c in codes:
        p = qr.parse_payload(c.payload, args.jurisdiction)
        parsed.append({
            "payload": c.payload,
            "symbology": c.symbology,
            "decoder": c.decoder,
            "scheme": p.scheme,
            "destination": p.destination,
            "payee_name": p.payee_name,
            "amount": p.amount,
            "currency": p.currency,
            "country": p.country,
            "reference": p.reference,
            "url": p.url,
            "checksum_ok": p.checksum_ok,
            "findings": p.findings,
        })

    if args.json:
        print(json.dumps({"decoders": decoders, "codes": parsed}, indent=2))
        return EXIT_SAFE

    if not parsed:
        print(f"No codes found. Decoders available: {', '.join(decoders)}")
        return EXIT_SAFE

    payment_like = [c for c in parsed if c["scheme"] in ("emvco", "epc", "upi")]
    print(f"{len(parsed)} code(s) found, {len(payment_like)} payment-like.\n")
    if len(payment_like) > 1:
        print("HOLD: more than one payment code on this document. A genuine")
        print("      invoice carries one. This is the signature of a payment")
        print("      code pasted over the original.\n")
    for i, c in enumerate(parsed, 1):
        print(f"  [{i}] {c['scheme']}  via {c['decoder']}")
        if c["destination"]:
            print(f"      money goes to: {c['destination']}")
        if c["payee_name"]:
            print(f"      payee name:    {c['payee_name']}")
        if c["amount"]:
            print(f"      amount:        {c['currency'] or ''} {c['amount']}")
        if c["url"]:
            print(f"      url:           {c['url']}")
        if c["checksum_ok"] is False:
            print("      checksum:      FAILS -- the payload was altered")
        for f in c["findings"]:
            print(f"      ! {f}")
        print()
    return EXIT_HOLD if len(payment_like) > 1 else EXIT_SAFE


def _record_payment(led: Ledger, args) -> int:
    """Record that a payment actually went out.

    This is the single most important thing a person ever tells the tool,
    because every bank-change check is measured against it. It used to
    accept anything at all, and a one-character typo in the supplier key
    silently downgraded a fraud HOLD to a shrug: the check could no longer
    find any history, so it reported unknown and a smaller finding took the
    headline. Garbage in here disarms the product quietly, which is the
    worst possible failure. So it validates, and it refuses.
    """
    if not args.supplier_key or not args.fingerprint:
        print("Tell it which supplier and which account was paid:\n"
              "  billguard ledger record-payment --ledger <file> \\\n"
              "      --supplier-key abn:98273029681 \\\n"
              "      --fingerprint au:062000:12345678 --when 2026-08-11\n\n"
              "Run 'billguard check <invoice> --json' and look at "
              "checks -> D01 -> detail for the exact values for an invoice.",
              file=sys.stderr)
        return EXIT_ERROR

    key = args.supplier_key.strip()
    if key.startswith("abn:"):
        from . import au
        digits = au.digits(key[4:])
        if not au.abn_is_valid(digits):
            print(f"'{key}' is not a valid supplier key: the ABN in it fails "
                  f"its checksum, so it is not a real ABN.\n"
                  f"A mistyped key means the tool cannot find this supplier's "
                  f"history, and the bank-detail check stops working for "
                  f"them. Nothing was recorded.", file=sys.stderr)
            return EXIT_ERROR
        key = "abn:" + digits
    elif not key.startswith("name:"):
        print(f"'{key}' is not a supplier key. It must start with 'abn:' "
              f"followed by the supplier's ABN, or 'name:' if they have "
              f"none. Nothing was recorded.", file=sys.stderr)
        return EXIT_ERROR

    fp = args.fingerprint.strip()
    if not _fingerprint_is_sane(fp):
        print(f"'{fp}' is not a payment destination this tool recognises.\n"
              f"It should look like  au:062000:12345678  (BSB then account), "
              f"or iban:DE89..., payid:..., or bpay:12345.\n"
              f"Nothing was recorded, because a wrong value here silently "
              f"switches off the check that catches changed bank details.",
              file=sys.stderr)
        return EXIT_ERROR

    known = led.is_known_supplier(key)
    if not known and not args.force:
        print(f"'{key}' has never been seen on any invoice you have checked.\n"
              f"Check that supplier's invoice first so the tool knows them, "
              f"or pass --force if you are certain. Nothing was recorded.",
              file=sys.stderr)
        return EXIT_ERROR

    when = args.when or _today()
    led.record_destination(key, fp, {"recorded_by": "human"}, when)
    led.mark_destination_paid(key, fp, when)
    led.add_evidence(when, "payment-recorded",
                     detail={"supplier": key, "destination": fp})
    print(f"Recorded: you paid {key} at {fp} on {when}.\n"
          f"From now on any invoice from them naming a different account "
          f"will be held.")
    return EXIT_SAFE


def _fingerprint_is_sane(fp: str) -> bool:
    import re
    if fp.startswith("au:"):
        return bool(re.fullmatch(r"au:\d{6}:\d{5,10}", fp))
    if fp.startswith("iban:"):
        from .qr import iban_checksum_ok
        return iban_checksum_ok(fp[5:])
    if fp.startswith("bpay:"):
        return fp[5:].isdigit() and 4 <= len(fp[5:]) <= 6
    if fp.startswith(("payid:", "crypto:")):
        return len(fp.split(":", 1)[1]) >= 3
    return False


def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()


def cmd_paid(args) -> int:
    """Tell it you paid an invoice, by pointing at the invoice.

    The supplier key and the destination fingerprint are internal
    vocabulary. Nobody outside this codebase should ever have to type
    "au:062000:12345678", and asking them to was the single biggest thing
    standing between a business owner and the one check that matters.
    """
    from . import intake

    path = Path(args.document)
    if not path.exists():
        print(f"There is no file at {args.document}. Point this at the "
              f"invoice you paid.", file=sys.stderr)
        return EXIT_ERROR

    if path.suffix.lower() == ".json":
        try:
            doc = document_from_dict(json.loads(path.read_text()))
        except Exception as exc:
            print(f"That file could not be read: {exc}", file=sys.stderr)
            return EXIT_ERROR
    else:
        doc = intake.from_file(args.document).document

    key = doc.supplier_key()
    fp = doc.payment.fingerprint()
    if key is None:
        print("Could not tell who this invoice is from, so there is nothing "
              "to remember. Run 'billguard read' on it to see what was "
              "found.", file=sys.stderr)
        return EXIT_ERROR
    if fp is None:
        print("No payment details could be read off this invoice, so there "
              "is no account to remember. Run 'billguard read' on it to see "
              "what was found.", file=sys.stderr)
        return EXIT_ERROR

    when = args.when or _today()
    with Ledger(args.ledger) as led:
        if not led.is_known_supplier(key):
            led.upsert_supplier(key, doc.supplier_name, doc.supplier_abn, when)
        led.record_destination(key, fp, {
            "bsb": doc.payment.bsb,
            "account_number": doc.payment.account_number,
            "account_name": doc.payment.account_name,
            "iban": doc.payment.iban,
        }, when)
        led.mark_destination_paid(key, fp, when)
        if doc.doc_id:
            led.mark_document_paid(doc.doc_id, when)
        led.add_evidence(when, "payment-recorded", doc.doc_id,
                         actor="human",
                         detail={"supplier": key, "destination": fp})

    who = doc.supplier_name or key
    where = (f"BSB {doc.payment.bsb} account {doc.payment.account_number}"
             if doc.payment.bsb else fp)
    print(f"Noted: you paid {who} at {where} on {when}.\n"
          f"If an invoice from them ever names a different account, it will "
          f"be held.")
    return EXIT_SAFE


def cmd_read(args) -> int:
    """Show what was actually read off a document, and what was not.

    Transparency matters more here than anywhere else: if a person cannot
    see what the tool read, they cannot tell a correct verdict from a
    confident wrong one.
    """
    from . import intake
    ex = intake.from_file(args.document)
    doc = ex.document

    if args.json:
        out = {
            "supplier_name": doc.supplier_name,
            "supplier_abn": doc.supplier_abn,
            "supplier_domain": doc.supplier_domain,
            "invoice_number": doc.invoice_number,
            "issue_date": doc.issue_date,
            "due_date": doc.due_date,
            "currency": doc.currency,
            "subtotal_cents": doc.subtotal_cents,
            "tax_cents": doc.tax_cents,
            "total_cents": doc.total_cents,
            "balance_due_cents": doc.balance_due_cents,
            "payment": {
                "bsb": doc.payment.bsb,
                "account_number": doc.payment.account_number,
                "bpay_biller": doc.payment.bpay_biller,
                "iban": doc.payment.iban,
                "confidence": doc.payment.confidence,
            },
            "confidence": ex.confidence,
            "problems": ex.gaps,
            "codes_found": len(doc.artifacts.get("qr_codes") or []),
        }
        print(json.dumps(out, indent=2))
        return EXIT_SAFE

    from .model import from_cents
    print(f"Read from {args.document}\n")

    def row(label, value, key=None):
        if value in (None, ""):
            print(f"  {label:<18} not found")
            return
        c = ex.confidence.get(key or "", None)
        mark = ""
        if c is not None:
            mark = "  (certain)" if c >= 0.95 else (
                "  (fairly sure)" if c >= 0.7 else "  (a guess, check it)")
        print(f"  {label:<18} {value}{mark}")

    row("Supplier", doc.supplier_name, "supplier_name")
    row("ABN", doc.supplier_abn, "supplier_abn")
    row("Invoice number", doc.invoice_number, "invoice_number")
    row("Issued", doc.issue_date, "issue_date")
    row("Due", doc.due_date, "due_date")
    row("Currency", doc.currency, "currency")
    for label, cents, key in (("Subtotal", doc.subtotal_cents, "subtotal_cents"),
                              ("GST", doc.tax_cents, "tax_cents"),
                              ("Total", doc.total_cents, "total_cents"),
                              ("Balance due", doc.balance_due_cents,
                               "balance_due_cents")):
        row(label, from_cents(cents) if cents is not None else None, key)

    print()
    if doc.payment.is_empty():
        print("  No bank details found on this document.")
    else:
        print(f"  Pay to           BSB {doc.payment.bsb or '-'}  "
              f"account {doc.payment.account_number or '-'}"
              f"{'  BPAY ' + doc.payment.bpay_biller if doc.payment.bpay_biller else ''}")
        print(f"                   read with confidence "
              f"{doc.payment.confidence:.0%}")

    codes = doc.artifacts.get("qr_codes") or []
    if codes:
        print(f"\n  {len(codes)} barcode(s) found on the page.")
        for c in codes:
            print(f"    page {c['page'] + 1}: {c['scheme']}"
                  f"{'  -> ' + c['destination'] if c.get('destination') else ''}")

    if ex.gaps:
        print("\nTrouble reading this document:")
        for g in ex.gaps:
            print(f"  - {g}")
    return EXIT_SAFE


def cmd_ledger(args) -> int:
    with Ledger(args.ledger) as led:
        if args.ledger_command == "stats":
            print(json.dumps(led.stats(), indent=2))
        elif args.ledger_command == "record-payment":
            return _record_payment(led, args)
        elif args.ledger_command == "suppliers":
            rows = led._conn.execute(
                "SELECT supplier_key, display_name, invoice_count, paid_count "
                "FROM supplier ORDER BY invoice_count DESC").fetchall()
            print(json.dumps([dict(r) for r in rows], indent=2))
    return EXIT_SAFE


def cmd_selftest(args) -> int:
    import subprocess
    runner = Path(__file__).resolve().parent.parent / "tests" / "run.py"
    return subprocess.call([sys.executable, str(runner)])


def cmd_capabilities(args) -> int:
    """What this installation can and cannot actually do.

    Honesty about missing capability is the whole design. A check that
    cannot run is reported, never assumed to pass.
    """
    from . import qr
    from .checks import REGISTRY
    caps = {
        "version": __version__,
        "checks_registered": len(REGISTRY),
        "families": sorted({fn.family for fn in REGISTRY}),
        "barcode_decoders": qr.available_decoders(),
        "cannot_do": [
            "Verify that a bank account belongs to the business named on the "
            "invoice. That needs a bank's own network. At payment time your "
            "banking app runs an account-name check; read what it says.",
            "Move money, approve a payment, or send email. No such capability "
            "exists in this codebase.",
        ],
    }
    print(json.dumps(caps, indent=2))
    return EXIT_SAFE


# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="billguard",
        description="Check an invoice before a cent moves.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("check", help="assess one invoice")
    c.add_argument("document",
                   help="an invoice: PDF, photo, saved email, text file, "
                        "prepared JSON, or - to paste text")
    c.add_argument("--ledger", help="path to the supplier ledger database")
    c.add_argument("--json", action="store_true", help="machine-readable output")
    c.add_argument("--remember", action="store_true",
                   help="record this document in the ledger")
    c.set_defaults(func=cmd_check)

    sr = sub.add_parser("scheduled-run", help="assess an inbox and write a digest")
    sr.add_argument("input_dir", help="folder containing bills to assess")
    sr.add_argument("--ledger", required=True,
                    help="Bill Guard ledger written by this run")
    sr.add_argument("--output", required=True, help="JSON digest to write")
    sr.set_defaults(func=cmd_scheduled_run)

    s = sub.add_parser("scan-codes", help="decode every code on an image")
    s.add_argument("image")
    s.add_argument("--jurisdiction", default="AU")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_scan_codes)

    pd = sub.add_parser("paid",
                        help="tell it you paid an invoice, by pointing at it")
    pd.add_argument("document", help="the invoice you paid")
    pd.add_argument("--ledger", required=True)
    pd.add_argument("--when", default="", help="e.g. 2026-08-11, default today")
    pd.set_defaults(func=cmd_paid)

    rd = sub.add_parser("read", help="show what was read off a document")
    rd.add_argument("document")
    rd.add_argument("--json", action="store_true")
    rd.set_defaults(func=cmd_read)

    lg = sub.add_parser("ledger", help="inspect or update the supplier ledger")
    lg.add_argument("ledger_command",
                    choices=["stats", "record-payment", "suppliers"])
    lg.add_argument("--ledger", required=True)
    lg.add_argument("--supplier-key", dest="supplier_key")
    lg.add_argument("--fingerprint")
    lg.add_argument("--when", default="",
                    help="the date it was paid, e.g. 2026-08-11")
    lg.add_argument("--force", action="store_true",
                    help="record a supplier the tool has never seen")
    lg.set_defaults(func=cmd_ledger)

    st = sub.add_parser("selftest", help="run the test suite")
    st.set_defaults(func=cmd_selftest)

    cp = sub.add_parser("capabilities",
                        help="what this installation can and cannot do")
    cp.set_defaults(func=cmd_capabilities)

    return p


def cmd_scheduled_run(args) -> int:
    from .scheduled import run_folder
    try:
        digest = run_folder(args.input_dir, ledger_path=args.ledger,
                            output_path=args.output)
    except (OSError, ValueError) as exc:
        print(f"Scheduled run failed: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(json.dumps(digest["summary"], sort_keys=True))
    return EXIT_ERROR if digest["summary"]["errors"] else EXIT_SAFE


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
