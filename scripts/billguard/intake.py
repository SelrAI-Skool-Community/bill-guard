"""Getting from a real invoice to a Document.

This is the difference between a developer's toy and something a business
owner can use. Before this existed the only way in was hand-writing JSON,
which is not a thing a plumber is ever going to do.

Takes what actually arrives: a PDF, a photo, an email file, a block of
pasted text. Returns a Document with a confidence on every field.

Three rules govern extraction:

  * Never guess the payment block. A one-digit error there sends money to
    the wrong place, so low confidence escalates to a person rather than
    producing a confident wrong answer.

  * A field that could not be read is absent, not zero and not empty
    string. Downstream checks distinguish "no total on this document" from
    "the total is nothing", and collapsing them produces wrong verdicts.

  * Say what could not be done. A scanned PDF with no text layer is a named
    capability gap, never an empty successful read.
"""
# resolver key sk-18atqw2 (skills-index lookup; keep)

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from enum import Enum

from . import au
from .model import Channel, Document, LineItem, PaymentInstruction, to_cents


class IntakeOutcome(str, Enum):
    """Mutually exclusive result of normalising a block of text."""
    INVOICE = "invoice"
    PARTIAL = "partial"
    NOT_INVOICE = "not_invoice"


@dataclass
class Extraction:
    """The result of reading a file, including what could not be read."""
    document: Document
    confidence: dict = field(default_factory=dict)
    gaps: list = field(default_factory=list)
    source_path: str | None = None
    text_chars: int = 0

    @property
    def ok(self) -> bool:
        return self.text_chars > 0

    @property
    def looks_like_an_invoice(self) -> bool:
        """Does this document ask anyone for money?

        Feeding a holiday photo or a contract to an invoice checker should
        say "this is not an invoice", not produce an official-looking
        verdict about it. A verdict on a document that was never a bill is
        worse than no verdict, because it looks like it means something.
        """
        d = self.document
        signals = 0
        if d.total_cents is not None or d.balance_due_cents is not None:
            signals += 1
        if d.invoice_number:
            signals += 1
        if d.supplier_abn:
            signals += 1
        if not d.payment.is_empty():
            signals += 1
        if d.doc_type_words:
            signals += 2
        return signals >= 2

    @property
    def outcome(self) -> IntakeOutcome:
        """Classify readable text without turning fragments into verdicts."""
        d = self.document
        has_financial_detail = any((
            d.total_cents is not None, d.balance_due_cents is not None,
            not d.payment.is_empty(),
        ))
        if self.looks_like_an_invoice and has_financial_detail:
            return IntakeOutcome.INVOICE
        has_extracted_field = any((
            d.invoice_number, d.supplier_abn, d.issue_date, d.due_date,
            d.total_cents is not None, d.balance_due_cents is not None,
            not d.payment.is_empty(), d.doc_type_words,
        ))
        if self.text_chars > 0 and has_extracted_field:
            return IntakeOutcome.PARTIAL
        return IntakeOutcome.NOT_INVOICE


# ---------------------------------------------------------------------------
# file to text
# ---------------------------------------------------------------------------

PDF_EXTRACTOR_UNAVAILABLE = "PDF_EXTRACTOR_UNAVAILABLE"
PDF_EXTRACTION_FAILED = "PDF_EXTRACTION_FAILED"
PDF_TEXT_LAYER_MISSING = "PDF_TEXT_LAYER_MISSING"

def _pdftotext_path() -> str | None:
    for candidate in ("pdftotext", "/opt/homebrew/bin/pdftotext",
                      "/usr/local/bin/pdftotext", "/usr/bin/pdftotext"):
        found = shutil.which(candidate) if os.path.basename(candidate) == candidate \
            else (candidate if os.path.isfile(candidate) else None)
        if found:
            return found
    return None


def text_from_pdf(path: str) -> tuple[str, list]:
    """Extract the text layer. A scanned page has none, and that is reported."""
    gaps = []
    exe = _pdftotext_path()
    if not exe:
        return "", [f"{PDF_EXTRACTOR_UNAVAILABLE}: No PDF text extractor is "
                    "installed, so this PDF could "
                    "not be read. Install poppler: brew install poppler"]
    try:
        out = subprocess.run([exe, "-layout", "-q", path, "-"],
                             capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        return "", [f"{PDF_EXTRACTION_FAILED}: The PDF took too long to read "
                    "and was abandoned."]
    except Exception as exc:
        return "", [f"{PDF_EXTRACTION_FAILED}: The PDF could not be read: "
                    f"{exc}"]
    if out.returncode != 0:
        detail = out.stderr.decode("utf-8", errors="replace").strip()
        suffix = f" ({detail})" if detail else ""
        return "", [f"{PDF_EXTRACTION_FAILED}: pdftotext could not read the "
                    f"PDF{suffix}."]
    text = out.stdout.decode("utf-8", errors="replace")
    if len(text.strip()) < 20:
        gaps.append(
            f"{PDF_TEXT_LAYER_MISSING}: This PDF has almost no text in it, "
            "which usually means it is a "
            "scan or a photo saved as a PDF. The words on it cannot be read "
            "without character recognition, which is not installed. Nothing "
            "was checked against the text of this document.")
    return text, gaps


def text_from_eml(path: str) -> tuple[str, list, dict]:
    """Read a saved email. The inner message wins if it was forwarded."""
    import email
    from email import policy
    meta = {}
    try:
        with open(path, "rb") as fh:
            msg = email.message_from_binary_file(fh, policy=policy.default)
    except Exception as exc:
        return "", [f"The email file could not be read: {exc}"], meta

    # A forward-as-attachment carries the original message intact. Every
    # check should run against that, not the covering note.
    inner = None
    for part in msg.walk():
        if part.get_content_type() == "message/rfc822":
            payload = part.get_payload()
            if payload:
                inner = payload[0] if isinstance(payload, list) else payload
                break
    target = inner or msg
    meta["channel"] = (Channel.FORWARD_ATTACHED if inner else Channel.MAILBOX)
    sender = target.get("From", "") or ""
    m = re.search(r"@([\w.-]+)", sender)
    if m:
        meta["supplier_domain"] = m.group(1).lower().rstrip(">").rstrip('"')
    meta["subject"] = target.get("Subject", "") or ""

    body = ""
    try:
        part = target.get_body(preferencelist=("plain", "html"))
        if part:
            body = part.get_content()
    except Exception:
        body = ""
    if not body:
        body = str(target.get_payload())
    return body, [], meta


def read_file(path: str) -> tuple[str, list, dict]:
    """Any supported file to text, plus what could not be done."""
    ext = os.path.splitext(path)[1].lower()
    if not os.path.isfile(path):
        return "", [f"There is no file at {path}."], {}
    if ext == ".pdf":
        text, gaps = text_from_pdf(path)
        return text, gaps, {"channel": Channel.UPLOAD}
    if ext == ".eml":
        return text_from_eml(path)
    if ext in (".txt", ".md", ".text", ""):
        try:
            return open(path, encoding="utf-8", errors="replace").read(), [], \
                {"channel": Channel.UPLOAD}
        except Exception as exc:
            return "", [f"The file could not be read: {exc}"], {}
    if ext in (".png", ".jpg", ".jpeg", ".heic", ".tif", ".tiff", ".webp"):
        return "", [
            "This is a photo or a scan. Reading the words in an image needs "
            "character recognition, which is not installed, so nothing on it "
            "was checked. Any barcode on it can still be decoded."
        ], {"channel": Channel.PHOTO}
    return "", [f"Files of type {ext or 'unknown'} are not supported yet."], {}


# ---------------------------------------------------------------------------
# text to fields
# ---------------------------------------------------------------------------

_ABN_RE = re.compile(r"\bA\.?B\.?N\.?[:\s#]*((?:\d[\s-]*){11})", re.I)
_ANY_11_RE = re.compile(r"\b((?:\d[\s-]*){11})\b")
_INV_NO_RE = re.compile(
    r"(?:tax\s+)?invoice\s*(?:no\.?|number|#|:)\s*([A-Za-z0-9][\w./-]{0,24})", re.I)
_INV_NO_ALT_RE = re.compile(r"\b(INV[-/ ]?\d{2,12})\b", re.I)
_BSB_RE = re.compile(r"\bBSB[:\s#]*(\d{3}[\s-]?\d{3})\b", re.I)
_ACCT_RE = re.compile(
    r"\b(?:acc(?:ount)?|a/c)\s*(?:no\.?|number|#)?[:\s]*(\d[\d\s-]{4,14})\b", re.I)
_BPAY_RE = re.compile(r"biller\s*code[:\s#]*(\d{4,6})", re.I)
_BPAY_REF_RE = re.compile(r"\bref(?:erence)?[:\s#]*(\d{4,20})\b", re.I)
_IBAN_RE = re.compile(r"\b([A-Z]{2}\d{2}[A-Z0-9]{10,30})\b")

_TOTAL_RE = re.compile(
    r"(?:^|\n)[^\n]*?\b(total\s+due|amount\s+due|balance\s+due|total\s+"
    r"(?:inc|incl|including)[^\n]{0,12}|grand\s+total|total)\b[^\n\d\-]{0,20}"
    r"\$?\s*(-?[\d,]+\.\d{2})", re.I)
_SUBTOTAL_RE = re.compile(
    r"\b(sub\s*-?\s*total|total\s+ex(?:cl)?[^\n]{0,10})\b[^\n\d\-]{0,20}"
    r"\$?\s*(-?[\d,]+\.\d{2})", re.I)
#: A tax line, NOT a total line that happens to mention tax. "Total incl
#: GST  1068.10" contains the word GST and is emphatically not the GST.
_GST_RE = re.compile(
    r"\b(gst|tax|vat)\b[^\n\d\-]{0,24}\$?\s*(-?[\d,]+\.\d{2})", re.I)
_TOTALISH_RE = re.compile(r"\b(total|amount\s+due|balance|grand)\b", re.I)
_BALANCE_RE = re.compile(
    r"\b(balance\s+due|amount\s+due|amount\s+payable|due\s+now)\b"
    r"[^\n\d\-]{0,20}\$?\s*(-?[\d,]+\.\d{2})", re.I)
_PAID_RE = re.compile(
    r"\b(paid\s+in\s+full|payment\s+received|amount\s+paid|receipt\s+for|"
    r"thanks?\s+for\s+your\s+payment|this\s+is\s+not\s+an\s+invoice)\b", re.I)

_CURRENCY_RE = re.compile(r"\b(AUD|USD|EUR|GBP|NZD|SGD|CAD|JPY)\b")

_DATE_PATTERNS = [
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})[/](\d{1,2})[/](\d{4})\b"), "dmy"),
    (re.compile(r"\b(\d{1,2})\s+"
                r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
                r"\s+(\d{4})\b", re.I), "dMy"),
    (re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
                r"\s+(\d{1,2}),?\s+(\d{4})\b", re.I), "Mdy"),
]
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}

_ISSUE_HINT = re.compile(r"\b(invoice\s+date|date\s+issued|issued|date)\b", re.I)
_DUE_HINT = re.compile(r"\b(due\s+date|payment\s+due|due\s+by|due)\b", re.I)


def _norm_date(match, kind) -> str | None:
    try:
        g = match.groups()
        if kind == "ymd":
            y, mo, d = int(g[0]), int(g[1]), int(g[2])
        elif kind == "dmy":
            d, mo, y = int(g[0]), int(g[1]), int(g[2])
        elif kind == "dMy":
            d, mo, y = int(g[0]), _MONTHS[g[1][:3].lower()], int(g[2])
        else:
            mo, d, y = _MONTHS[g[0][:3].lower()], int(g[1]), int(g[2])
        if not (1 <= mo <= 12 and 1 <= d <= 31 and 1900 < y < 2200):
            return None
        return f"{y:04d}-{mo:02d}-{d:02d}"
    except Exception:
        return None


def _dates_near(text: str, hint: re.Pattern) -> str | None:
    """Find a date on a line that also carries the hint word."""
    for line in text.splitlines():
        if not hint.search(line):
            continue
        for pattern, kind in _DATE_PATTERNS:
            m = pattern.search(line)
            if m:
                got = _norm_date(m, kind)
                if got:
                    return got
    return None


def _line_with_date(text: str, iso: str, hint: re.Pattern) -> int | None:
    for i, line in enumerate(text.splitlines()):
        if not hint.search(line):
            continue
        for pattern, kind in _DATE_PATTERNS:
            m = pattern.search(line)
            if m and _norm_date(m, kind) == iso:
                return i
    return None


def _money(match_groups) -> int | None:
    try:
        return to_cents(match_groups[-1])
    except Exception:
        return None


def extract_fields(text: str) -> tuple[dict, dict, list]:
    """Pull structured fields out of invoice text.

    Returns (fields, confidence, gaps). Confidence is deliberately blunt:
    1.0 for something proven by arithmetic or a checksum, 0.8 for a clearly
    labelled field, 0.5 for a guess, and absent for anything not found.
    """
    fields: dict = {}
    conf: dict = {}
    gaps: list = []

    # --- ABN. The checksum makes this the most trustworthy field on the page.
    m = _ABN_RE.search(text)
    if not m:
        for cand in _ANY_11_RE.finditer(text):
            if au.abn_is_valid(cand.group(1)):
                m = cand
                break
    if m:
        abn = re.sub(r"\D", "", m.group(1))
        fields["supplier_abn"] = abn
        conf["supplier_abn"] = 1.0 if au.abn_is_valid(abn) else 0.6
        if not au.abn_is_valid(abn):
            gaps.append("An ABN was found on the document but it fails its "
                        "checksum, so it is not a real ABN.")

    # --- invoice number
    m = _INV_NO_RE.search(text) or _INV_NO_ALT_RE.search(text)
    if m:
        fields["invoice_number"] = m.group(1).strip().rstrip(".,;:")
        conf["invoice_number"] = 0.8

    # --- dates
    issue = _dates_near(text, _ISSUE_HINT)
    due = _dates_near(text, _DUE_HINT)
    # "Due" appears inside "Due Date" AND the loose _ISSUE_HINT "date" matches
    # the same line, so a document with only one of the two used to have the
    # other silently filled from it. A wrong due date invents an overdue
    # warning out of nothing.
    if issue and due and issue == due:
        issue_line = _line_with_date(text, issue, _ISSUE_HINT)
        due_line = _line_with_date(text, due, _DUE_HINT)
        if issue_line is not None and issue_line == due_line:
            issue = None
    if issue:
        fields["issue_date"], conf["issue_date"] = issue, 0.8
    if due:
        fields["due_date"], conf["due_date"] = due, 0.8

    # --- currency
    m = _CURRENCY_RE.search(text)
    if m:
        fields["currency"], conf["currency"] = m.group(1).upper(), 0.8
    elif "$" in text:
        fields["currency"], conf["currency"] = "AUD", 0.4
        gaps.append("The document shows a dollar sign but never names a "
                    "currency, so Australian dollars were assumed.")

    # --- amounts
    totals = [_money(mm.groups()) for mm in _TOTAL_RE.finditer(text)]
    totals = [t for t in totals if t is not None]
    if totals:
        fields["total_cents"], conf["total_cents"] = max(totals), 0.7

    m = _SUBTOTAL_RE.search(text)
    if m:
        v = _money(m.groups())
        if v is not None:
            fields["subtotal_cents"], conf["subtotal_cents"] = v, 0.8

    # Collect tax candidates line by line so a total line that merely
    # mentions GST cannot be mistaken for the GST itself.
    gsts = []
    for line in text.splitlines():
        if _TOTALISH_RE.search(line):
            continue
        mm = _GST_RE.search(line)
        if mm:
            v = _money(mm.groups())
            if v is not None:
                gsts.append(v)
    if gsts:
        fields["tax_cents"], conf["tax_cents"] = max(gsts), 0.7

    m = _BALANCE_RE.search(text)
    if m:
        v = _money(m.groups())
        if v is not None:
            fields["balance_due_cents"], conf["balance_due_cents"] = v, 0.85

    # A receipt says so in words. Reading the balance-due field rather than
    # the total is what stops a receipt being paid a second time.
    if _PAID_RE.search(text) and "balance_due_cents" not in fields:
        fields["balance_due_cents"], conf["balance_due_cents"] = 0, 0.7

    # --- arithmetic promotes confidence. If the numbers reconcile, they were
    # almost certainly read correctly, and that is worth more than any
    # model's self-reported score.
    st, tx, tot = (fields.get("subtotal_cents"), fields.get("tax_cents"),
                   fields.get("total_cents"))

    # If subtotal and total are known, the GST is derivable. Arithmetic beats
    # any regex: use it to CHOOSE between candidates, not merely to score
    # them. This is what stops a misread tax line producing a wrong verdict.
    if st is not None and tot is not None:
        implied = tot - st
        if implied >= 0 and (tx is None or
                             not au.totals_are_consistent(st, tx, tot)):
            fields["tax_cents"] = implied
            conf["tax_cents"] = 0.9
            if tx is not None:
                gaps.append(
                    f"The tax line read as {tx / 100:.2f} but the subtotal "
                    f"and total imply {implied / 100:.2f}. The arithmetic was "
                    f"used, because it is the more reliable of the two.")
            tx = implied

    if None not in (st, tx, tot) and au.totals_are_consistent(st, tx, tot):
        for k in ("subtotal_cents", "tax_cents", "total_cents"):
            conf[k] = 1.0

    # --- document type words
    words = []
    if au.looks_like_rcti(text):
        words.append("RECIPIENT CREATED TAX INVOICE")
    elif au.looks_like_tax_invoice(text):
        words.append("TAX INVOICE")
    if au.looks_like_credit_note(text):
        words.append("CREDIT NOTE")
    if words:
        fields["doc_type_words"] = " ".join(words)

    return fields, conf, gaps


def extract_payment(text: str) -> tuple[PaymentInstruction, list]:
    """Read the payment block, and be honest about how sure we are.

    Kept separate and deliberately conservative. This is the block fraud
    exists to change, so a partial read is reported as low confidence rather
    than presented as fact.
    """
    gaps = []
    p = PaymentInstruction(source="text")
    scores = []

    m = _BSB_RE.search(text)
    if m:
        p.bsb = au.bsb_normalise(m.group(1)) or m.group(1)
        scores.append(0.95)
    m = _ACCT_RE.search(text)
    if m:
        p.account_number = re.sub(r"\D", "", m.group(1))
        scores.append(0.9)
    m = _BPAY_RE.search(text)
    if m:
        p.bpay_biller = m.group(1)
        scores.append(0.95)
        mr = _BPAY_REF_RE.search(text)
        if mr:
            p.bpay_reference = mr.group(1)
    m = _IBAN_RE.search(text)
    if m:
        from .qr import iban_checksum_ok
        if iban_checksum_ok(m.group(1)):
            p.iban = m.group(1)
            scores.append(1.0)

    if p.bsb and not p.account_number:
        gaps.append("A BSB was found but no account number, so the payment "
                    "details are incomplete and were not used.")
        p.bsb = None
        scores = [s for s in scores if s != 0.95]

    p.confidence = min(scores) if scores else 0.0
    return p, gaps


# ---------------------------------------------------------------------------
# the one call
# ---------------------------------------------------------------------------

def from_text(text: str, *, channel: Channel = Channel.PASTE,
              supplier_name: str | None = None,
              supplier_domain: str | None = None,
              received_at: str | None = None,
              text_source: str = "document") -> Extraction:
    fields, conf, gaps = extract_fields(text)
    payment, pay_gaps = extract_payment(text)
    gaps.extend(pay_gaps)
    if payment.bsb:
        conf["payment.bsb"] = payment.confidence
    if payment.account_number:
        conf["payment.account_number"] = payment.confidence
    if payment.bpay_biller:
        conf["payment.bpay_biller"] = payment.confidence
    if payment.bpay_reference:
        conf["payment.bpay_reference"] = payment.confidence
    if payment.iban:
        conf["payment.iban"] = payment.confidence

    if supplier_name is None:
        supplier_name = _guess_supplier_name(text)
        if supplier_name:
            conf["supplier_name"] = 0.5

    doc = Document(
        channel=channel,
        received_at=received_at,
        supplier_name=supplier_name,
        supplier_domain=supplier_domain,
        raw_text=text[:20000],
        text_source=text_source,
        **{k: v for k, v in fields.items()},
    )
    doc.payment = payment
    doc.doc_id = doc.content_hash()
    return Extraction(doc, conf, gaps, None, len(text))


def _guess_supplier_name(text: str) -> str | None:
    """The letterhead is usually the first substantial line.

    A guess, and marked as one. Never used for a register lookup: resolving
    a supplier by name returned a confident wrong company in testing.
    """
    for line in text.splitlines():
        s = line.strip()
        if len(s) < 3 or len(s) > 70:
            continue
        if re.search(r"\d{2,}", s):
            continue
        # A labelled line is the label plus its value, not the letterhead.
        # "Account Name: Northwind Tiling" used to be read whole.
        if re.match(r"^(account|bank|payment|attention|attn|bill|ship|to|"
                    r"from|customer|client|abn|acn|phone|email|address)\b"
                    r"[^:]{0,20}:", s, re.I):
            continue
        if re.search(r"^(tax\s+invoice|invoice|receipt|statement|bill)$", s, re.I):
            continue
        if "@" in s or s.lower().startswith(("http", "www.")):
            continue
        return s
    return None


def from_file(path: str, *, received_at: str | None = None) -> Extraction:
    """Read any supported invoice file and return a Document.

    The one call a business owner's tooling needs.
    """
    text, gaps, meta = read_file(path)
    channel = meta.get("channel", Channel.UPLOAD)
    text_source = "document" if os.path.splitext(path)[1].lower() != ".eml" \
        else "email_body"

    ex = from_text(text, channel=channel,
                   supplier_domain=meta.get("supplier_domain"),
                   received_at=received_at, text_source=text_source)
    ex.gaps = gaps + ex.gaps
    ex.source_path = path

    # Any barcode on the page is decoded regardless of whether the text
    # could be read: a photo with no readable words can still carry a
    # payment code, and that is exactly the case worth catching.
    codes = _decode_codes(path)
    if codes:
        ex.document.artifacts["qr_codes"] = codes
    return ex


def _decode_codes(path: str) -> list:
    from . import qr
    ext = os.path.splitext(path)[1].lower()
    images = []
    tmpdir = None
    if ext == ".pdf":
        images, tmpdir = _render_pdf_pages(path)
    elif ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"):
        images = [(path, 0)]
    out = []
    try:
        for image_path, page in images:
            for code in qr.decode_image(image_path, page):
                parsed = qr.parse_payload(code.payload)
                out.append({
                    "payload": code.payload, "page": page,
                    "decoder": code.decoder, "scheme": parsed.scheme,
                    "destination": parsed.destination,
                    "payee_name": parsed.payee_name,
                    "amount": parsed.amount, "currency": parsed.currency,
                    "url": parsed.url, "checksum_ok": parsed.checksum_ok,
                    "findings": parsed.findings,
                })
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
    return out


def _render_pdf_pages(path: str) -> tuple[list, str | None]:
    """Render EVERY page. Real campaigns hide the code on the last one."""
    exe = shutil.which("pdftoppm")
    if not exe:
        return [], None
    import tempfile
    tmpdir = tempfile.mkdtemp(prefix="billguard-")
    try:
        rendered = subprocess.run(
            [exe, "-r", "300", "-png", path,
             os.path.join(tmpdir, "page")],
            capture_output=True, timeout=120)
    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return [], None
    if rendered.returncode != 0:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return [], None

    def page_number(filename: str) -> int:
        match = re.search(r"-(\d+)\.png$", filename)
        return int(match.group(1)) if match else 0

    pages = sorted(
        (os.path.join(tmpdir, f) for f in os.listdir(tmpdir)
         if f.lower().endswith(".png")),
        key=lambda filename: page_number(os.path.basename(filename)))
    return [(p, i) for i, p in enumerate(pages)], tmpdir
