"""Command line for Bill Guard.

One entry point, subcommand dispatched. Every command speaks JSON on
request, because the point of this thing is that anything can call it: a
person at a terminal, a scheduled routine, an agent's tool call, or another
system entirely.

    billguard check invoice.json            human-readable verdict
    billguard check invoice.json --json     machine-readable verdict
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
    return doc


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_check(args) -> int:
    try:
        data = json.loads(Path(args.document).read_text())
    except FileNotFoundError:
        print(f"no such file: {args.document}", file=sys.stderr)
        return EXIT_ERROR
    except json.JSONDecodeError as exc:
        print(f"not valid JSON: {exc}", file=sys.stderr)
        return EXIT_ERROR

    try:
        doc = document_from_dict(data)
    except ValueError as exc:
        print(f"could not read the document: {exc}", file=sys.stderr)
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
        print(json.dumps(out, indent=2))
    else:
        print(render_text(verdict, doc))
    return _EXIT[verdict.outcome]


def _remember(ledger: Ledger, doc: Document, verdict) -> None:
    """Record what we saw. Seeing is not paying: D01 depends on the difference."""
    when = doc.received_at or doc.issue_date or ""
    key = doc.supplier_key()
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
        payload={"payment_fingerprint": doc.payment.fingerprint()})
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


def cmd_ledger(args) -> int:
    with Ledger(args.ledger) as led:
        if args.ledger_command == "stats":
            print(json.dumps(led.stats(), indent=2))
        elif args.ledger_command == "record-payment":
            led.record_destination(args.supplier_key, args.fingerprint, {},
                                   args.when)
            led.mark_destination_paid(args.supplier_key, args.fingerprint,
                                      args.when)
            led.add_evidence(args.when, "payment-recorded",
                             detail={"supplier": args.supplier_key,
                                     "destination": args.fingerprint})
            print(f"recorded a paid destination for {args.supplier_key}")
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
    c.add_argument("document", help="path to a JSON document")
    c.add_argument("--ledger", help="path to the supplier ledger database")
    c.add_argument("--json", action="store_true", help="machine-readable output")
    c.add_argument("--remember", action="store_true",
                   help="record this document in the ledger")
    c.set_defaults(func=cmd_check)

    s = sub.add_parser("scan-codes", help="decode every code on an image")
    s.add_argument("image")
    s.add_argument("--jurisdiction", default="AU")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_scan_codes)

    lg = sub.add_parser("ledger", help="inspect or update the supplier ledger")
    lg.add_argument("ledger_command",
                    choices=["stats", "record-payment", "suppliers"])
    lg.add_argument("--ledger", required=True)
    lg.add_argument("--supplier-key", dest="supplier_key")
    lg.add_argument("--fingerprint")
    lg.add_argument("--when", default="")
    lg.set_defaults(func=cmd_ledger)

    st = sub.add_parser("selftest", help="run the test suite")
    st.set_defaults(func=cmd_selftest)

    cp = sub.add_parser("capabilities",
                        help="what this installation can and cannot do")
    cp.set_defaults(func=cmd_capabilities)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
