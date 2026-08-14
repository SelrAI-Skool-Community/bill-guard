"""Australian deterministic validators.

Everything here is arithmetic or a published format rule. No network, no
model, no judgement. These are the checks that cannot false-positive, which
is why they get built first and weighted hardest.
"""
# resolver key sk-18atqw2 (skills-index lookup; keep)

from __future__ import annotations

import re

# ABN weights, per the ABR's published algorithm.
_ABN_WEIGHTS = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)

# ACN weights, per ASIC's published algorithm.
_ACN_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 1)


def digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


# --------------------------------------------------------------------------
# ABN
# --------------------------------------------------------------------------

def abn_is_valid(abn: str | None) -> bool:
    """True when the ABN passes the ABR modulus-89 checksum.

    Subtract 1 from the first digit, multiply the 11 digits by the published
    weights, sum, and the remainder mod 89 must be zero.

    A randomly fabricated 11-digit number passes about 1 time in 89, so this
    is a genuinely strong offline check on a hallucinated or invented ABN.
    """
    d = digits(abn)
    if len(d) != 11:
        return False
    nums = [int(c) for c in d]
    nums[0] -= 1
    if nums[0] < 0:
        return False
    total = sum(n * w for n, w in zip(nums, _ABN_WEIGHTS))
    return total % 89 == 0


def abn_format(abn: str | None) -> str | None:
    """Render an ABN in the conventional 2-3-3-3 grouping."""
    d = digits(abn)
    if len(d) != 11:
        return None
    return f"{d[0:2]} {d[2:5]} {d[5:8]} {d[8:11]}"


def acn_is_valid(acn: str | None) -> bool:
    """True when the ACN passes ASIC's modulus-10 complement checksum."""
    d = digits(acn)
    if len(d) != 9:
        return False
    body, check = d[:8], int(d[8])
    total = sum(int(c) * w for c, w in zip(body, _ACN_WEIGHTS))
    remainder = total % 10
    expected = (10 - remainder) % 10
    return expected == check


def abn_from_acn(acn: str | None) -> str | None:
    """An ABN for a company is its ACN with a 2-digit prefix. Recover the pair."""
    d = digits(acn)
    if len(d) != 9 or not acn_is_valid(d):
        return None
    for prefix in range(10, 100):
        candidate = f"{prefix}{d}"
        if abn_is_valid(candidate):
            return candidate
    return None


# --------------------------------------------------------------------------
# BSB
# --------------------------------------------------------------------------

_BSB_RE = re.compile(r"^\d{3}-?\d{3}$")


def bsb_is_well_formed(bsb: str | None) -> bool:
    """BSB has no check digit. Format is all that can be tested offline.

    Real validation is membership in the AusPayNet directory, and the
    genuinely useful extra test is whether the record is flagged for the
    electronic stream: a BSB valid for paper only will bounce a payment.
    """
    if not bsb:
        return False
    return bool(_BSB_RE.match(bsb.strip()))


def bsb_normalise(bsb: str | None) -> str | None:
    d = digits(bsb)
    if len(d) != 6:
        return None
    return f"{d[0:3]}-{d[3:6]}"


def account_number_is_plausible(acct: str | None) -> bool:
    """Australian account numbers carry no checksum. Length is the only test."""
    d = digits(acct)
    return 5 <= len(d) <= 10


# --------------------------------------------------------------------------
# GST
# --------------------------------------------------------------------------

#: Rounding is legal per-line and per-invoice, and the two give different
#: answers on a long invoice. Too tight a tolerance here is the single most
#: common implementation error in automated invoice checking: it floods the
#: queue with exceptions until humans start approving without looking.
LINE_TOLERANCE_CENTS = 2
TOTAL_TOLERANCE_CENTS = 5


def gst_on_inclusive_total(total_cents: int) -> int:
    """GST contained in a GST-inclusive total for a wholly taxable supply.

    Exactly one eleventh, rounded half-up to the nearest cent.
    """
    if total_cents < 0:
        raise ValueError("negative total")
    return (total_cents * 2 + 11) // 22


def gst_on_exclusive_amount(amount_cents: int) -> int:
    """GST added to a GST-exclusive amount: ten percent, half-up."""
    if amount_cents < 0:
        raise ValueError("negative amount")
    return (amount_cents * 2 + 10) // 20


def inclusive_total_is_consistent(total_cents: int, stated_gst_cents: int,
                                  tolerance: int = TOTAL_TOLERANCE_CENTS) -> bool:
    """Does the stated GST match one eleventh of the total, within tolerance?

    Only meaningful for a wholly taxable supply. An invoice mixing taxable
    and GST-free lines fails this legitimately, which is why the caller must
    know the mix before treating a mismatch as a finding.
    """
    return abs(gst_on_inclusive_total(total_cents) - stated_gst_cents) <= tolerance


def totals_are_consistent(subtotal_cents: int, tax_cents: int, total_cents: int,
                          tolerance: int = TOTAL_TOLERANCE_CENTS) -> bool:
    """subtotal + tax == total, within legal rounding tolerance."""
    return abs((subtotal_cents + tax_cents) - total_cents) <= tolerance


# --------------------------------------------------------------------------
# thresholds that appear in the rules
# --------------------------------------------------------------------------

#: A tax invoice must be provided for a taxable sale above this (GST inclusive).
TAX_INVOICE_REQUIRED_ABOVE_CENTS = 8250        # $82.50

#: Above this, the invoice must also carry the buyer's identity or ABN.
BUYER_IDENTITY_REQUIRED_AT_OR_ABOVE_CENTS = 100_000   # $1,000

#: No ABN quoted and payment above this (GST exclusive) triggers withholding.
NO_ABN_WITHHOLDING_THRESHOLD_CENTS = 7500      # $75

#: The rate withheld when no ABN is quoted.
NO_ABN_WITHHOLDING_RATE = 0.47


def no_abn_withholding_cents(payment_ex_gst_cents: int) -> int:
    """Amount to withhold when a supplier quotes no ABN.

    Applies above the threshold. The penalty for failing to withhold equals
    the amount that should have been withheld, so this is worth computing
    even though it is an obligation rather than a fraud signal.
    """
    if payment_ex_gst_cents <= NO_ABN_WITHHOLDING_THRESHOLD_CENTS:
        return 0
    return int(round(payment_ex_gst_cents * NO_ABN_WITHHOLDING_RATE))


# --------------------------------------------------------------------------
# document markers
# --------------------------------------------------------------------------

_TAX_INVOICE_RE = re.compile(r"\btax\s+invoice\b", re.I)
_RCTI_RE = re.compile(
    r"\brecipient[\s\-]*created\s+tax\s+invoice\b|\brcti\b", re.I)
_ADJUSTMENT_RE = re.compile(r"\badjustment\s+note\b|\bcredit\s+note\b", re.I)


def looks_like_tax_invoice(text: str) -> bool:
    return bool(_TAX_INVOICE_RE.search(text or ""))


def looks_like_rcti(text: str) -> bool:
    """A payer-written invoice. Different rules, and a different fraud shape."""
    return bool(_RCTI_RE.search(text or ""))


def looks_like_credit_note(text: str) -> bool:
    return bool(_ADJUSTMENT_RE.search(text or ""))
