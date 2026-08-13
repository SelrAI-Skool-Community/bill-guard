"""Core value objects for Bill Guard.

Stdlib only. Money is integer cents everywhere; floats drift and a drifted
total is a wrong verdict.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


# --------------------------------------------------------------------------
# money
# --------------------------------------------------------------------------

_MONEY_CLEAN = re.compile(r"[^\d.\-]")


def to_cents(value: Any, field_name: str = "amount") -> int:
    """Parse a money value to integer cents. Never returns a float.

    Accepts int cents pass-through only via `int` when already cents is wrong,
    so callers must pass a decimal string, a float, or a Decimal-ish string.
    """
    if value is None:
        raise ValueError(f"{field_name}: no value")
    if isinstance(value, bool):
        raise ValueError(f"{field_name}: bool is not money")
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field_name}: empty")
    neg = text.startswith("(") and text.endswith(")")
    if neg:
        text = text[1:-1]
    text = text.replace(",", "")
    text = _MONEY_CLEAN.sub("", text)
    if text in ("", "-", ".", "-."):
        raise ValueError(f"{field_name}: not a number: {value!r}")
    if text.count(".") > 1:
        raise ValueError(f"{field_name}: malformed: {value!r}")
    if "." in text:
        whole, frac = text.split(".")
        frac = (frac + "00")[:2]
    else:
        whole, frac = text, "00"
    sign = -1 if whole.startswith("-") or neg else 1
    whole = whole.lstrip("-")
    if not whole:
        whole = "0"
    if not whole.isdigit() or not frac.isdigit():
        raise ValueError(f"{field_name}: not a number: {value!r}")
    return sign * (int(whole) * 100 + int(frac))


def from_cents(cents: int) -> str:
    """Render integer cents as a plain decimal string."""
    if not isinstance(cents, int) or isinstance(cents, bool):
        raise TypeError("from_cents expects int cents")
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}.{cents % 100:02d}"


# --------------------------------------------------------------------------
# check results
# --------------------------------------------------------------------------

class Status(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"      # could not run. NEVER silently becomes pass.
    NOT_APPLICABLE = "n/a"


class Severity(str, Enum):
    HOLD = "hold"            # do not pay
    QUERY = "query"          # a human must answer something first
    INFO = "info"            # worth knowing, does not gate payment


class FPRisk(str, Enum):
    NONE = "none"            # arithmetic or checksum. Cannot false-positive.
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"            # never gate on this alone


@dataclass
class CheckResult:
    check_id: str
    title: str
    status: Status
    severity: Severity
    fp_risk: FPRisk
    evidence: str = ""
    detail: dict = field(default_factory=dict)

    @property
    def fired(self) -> bool:
        """A check 'fires' only when it actually failed."""
        return self.status is Status.FAIL

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        d["severity"] = self.severity.value
        d["fp_risk"] = self.fp_risk.value
        return d


def ok(check_id, title, evidence="", **detail) -> CheckResult:
    return CheckResult(check_id, title, Status.PASS, Severity.INFO,
                       FPRisk.NONE, evidence, detail)


def fail(check_id, title, severity, fp_risk, evidence="", **detail) -> CheckResult:
    return CheckResult(check_id, title, Status.FAIL, severity, fp_risk,
                       evidence, detail)


def unknown(check_id, title, why, **detail) -> CheckResult:
    """A check that could not run. Reported as not-run, never as a pass."""
    return CheckResult(check_id, title, Status.UNKNOWN, Severity.INFO,
                       FPRisk.NONE, why, detail)


def not_applicable(check_id, title, why="") -> CheckResult:
    return CheckResult(check_id, title, Status.NOT_APPLICABLE, Severity.INFO,
                       FPRisk.NONE, why, {})


# --------------------------------------------------------------------------
# channel trust
# --------------------------------------------------------------------------

class Channel(str, Enum):
    """How the document reached us. Governs what a verdict may claim."""
    MAILBOX = "mailbox"          # T1: direct API read, headers intact
    FORWARD_ATTACHED = "fwd_eml"  # T2a: forwarded as .eml, inner msg intact
    FORWARD_INLINE = "fwd_inline"  # T2b: inline forward, headers are body text
    UPLOAD = "upload"            # T3: file drop, web upload, chat app
    PASTE = "paste"              # T3: pasted text
    PHOTO = "photo"              # T3: photo or scan of paper
    UNKNOWN = "unknown"


#: Channels where email authentication forensics are even possible.
FORENSIC_CHANNELS = frozenset({Channel.MAILBOX, Channel.FORWARD_ATTACHED})


def forensic_grade(channel: Channel) -> str:
    """Plain-English confidence in email forensics for this channel."""
    if channel is Channel.MAILBOX:
        return "full"
    if channel is Channel.FORWARD_ATTACHED:
        return "inner-message"
    if channel is Channel.FORWARD_INLINE:
        return "none-sender-checks-broken-by-forward"
    return "none-not-email"


# --------------------------------------------------------------------------
# the document
# --------------------------------------------------------------------------

@dataclass
class PaymentInstruction:
    """Extracted separately and at the highest fidelity available.

    This is the block fraud exists to change. A one-digit error here is a
    wrong verdict, so every field carries its own confidence.
    """
    bsb: str | None = None
    account_number: str | None = None
    account_name: str | None = None
    iban: str | None = None
    swift: str | None = None
    payid: str | None = None
    bpay_biller: str | None = None
    bpay_reference: str | None = None
    crypto_address: str | None = None
    pay_url: str | None = None
    confidence: float = 0.0
    source: str = ""          # "text" | "qr" | "embedded-xml" | "manual"

    def fingerprint(self) -> str | None:
        """Stable identity of *where the money goes*, ignoring formatting.

        Two instructions with the same fingerprint send money to the same
        place. Used to detect a changed destination against the ledger.
        """
        parts = []
        if self.bsb and self.account_number:
            parts.append("au:" + _digits(self.bsb) + ":" + _digits(self.account_number))
        if self.iban:
            parts.append("iban:" + self.iban.replace(" ", "").upper())
        if self.payid:
            parts.append("payid:" + self.payid.strip().lower())
        if self.bpay_biller:
            parts.append("bpay:" + _digits(self.bpay_biller))
        if self.crypto_address:
            parts.append("crypto:" + self.crypto_address.strip())
        if not parts:
            return None
        return "|".join(sorted(parts))

    def is_empty(self) -> bool:
        return self.fingerprint() is None


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


@dataclass
class LineItem:
    description: str = ""
    quantity: str | None = None
    unit_price_cents: int | None = None
    amount_cents: int | None = None
    tax_code: str | None = None       # e.g. GST / GST-FREE / EXPORT
    confidence: float = 0.0


@dataclass
class Document:
    """A normalised invoice or bill, whatever channel it arrived by."""
    doc_id: str = ""
    channel: Channel = Channel.UNKNOWN
    received_at: str | None = None        # ISO 8601

    supplier_name: str | None = None
    supplier_abn: str | None = None
    supplier_domain: str | None = None

    buyer_name: str | None = None
    buyer_abn: str | None = None

    invoice_number: str | None = None
    issue_date: str | None = None          # ISO 8601 date
    due_date: str | None = None
    po_reference: str | None = None

    currency: str | None = None
    subtotal_cents: int | None = None
    tax_cents: int | None = None
    total_cents: int | None = None
    balance_due_cents: int | None = None   # "Amount due: 0.00" means already paid

    line_items: list[LineItem] = field(default_factory=list)
    payment: PaymentInstruction = field(default_factory=PaymentInstruction)

    doc_type_words: str = ""    # raw text of any "tax invoice" / "RCTI" marker
    raw_text: str = ""
    artifacts: dict = field(default_factory=dict)   # qr payloads, urls, etc
    jurisdiction: str = "AU"

    def content_hash(self) -> str:
        """Content-addressed id so re-runs are idempotent."""
        basis = json.dumps({
            "s": self.supplier_name, "a": self.supplier_abn,
            "n": self.invoice_number, "d": self.issue_date,
            "t": self.total_cents,
        }, sort_keys=True)
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()

    def supplier_key(self) -> str | None:
        """Ledger key for a supplier. ABN wins; name is the weak fallback.

        Never resolve a supplier by name alone for a *registry* check --
        that produced a confident wrong verdict in testing. Here the key is
        only used to group our own history, which is safe.
        """
        if self.supplier_abn:
            d = _digits(self.supplier_abn)
            if len(d) == 11:
                return "abn:" + d
        if self.supplier_name:
            return "name:" + re.sub(r"[^a-z0-9]", "", self.supplier_name.lower())
        return None
