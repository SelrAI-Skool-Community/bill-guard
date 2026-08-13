"""The check register.

Every check is a plain function taking (doc, ledger, ctx) and returning a
CheckResult. Each one declares its own false-positive risk, because the way
these systems die is not missing fraud, it is crying wolf until people
approve without looking.

Checks never raise. A check that cannot run returns UNKNOWN, and UNKNOWN
never becomes a pass.
"""

from __future__ import annotations

import datetime as _dt
import json
import re

from . import au
from .model import (
    CheckResult, Channel, Document, FPRisk, Severity, Status,
    fail, not_applicable, ok, unknown, from_cents,
)

REGISTRY: list = []


def check(check_id: str, title: str, family: str):
    """Register a check function."""
    def deco(fn):
        fn.check_id = check_id
        fn.title = title
        fn.family = family
        REGISTRY.append(fn)
        return fn
    return deco


def run_all(doc: Document, ledger=None, ctx: dict | None = None) -> list[CheckResult]:
    ctx = ctx or {}
    results = []
    for fn in REGISTRY:
        try:
            r = fn(doc, ledger, ctx)
        except Exception as exc:                      # never let one check
            r = unknown(fn.check_id, fn.title,        # take down the run
                        f"check raised {type(exc).__name__}: {exc}")
        if r is None:
            continue
        if isinstance(r, CheckResult):
            results.append(r)
        else:
            results.extend(r)
    return results


def _lookup(ctx: dict, name: str, value: str):
    """Run an injected lookup client; registry absence is never a pass."""
    clients = ctx.get("lookup_clients") or {}
    client = clients.get(name)
    if client is None:
        return None
    return client.lookup(value)


# ===========================================================================
# FAMILY D -- payment safety.  The highest-yield family that exists.
# ===========================================================================

@check("D01", "Payee bank details changed since we last paid this supplier",
       "payment")
def bank_details_changed(doc: Document, ledger, ctx) -> CheckResult:
    """THE control. Outranks every other technique in this system combined.

    Business email compromise is a changed destination account, not a forged
    pixel. Comparing this invoice's payee details against the destination we
    last actually PAID catches email compromise, supplier account takeover
    and altered documents in one test.

    Compared against destinations we PAID, never merely ones we have seen:
    an attacker's account is 'seen' the instant their invoice lands.
    """
    fp = doc.payment.fingerprint()
    key = doc.supplier_key()
    if key is None:
        return unknown("D01", bank_details_changed.title,
                       "no supplier key: cannot look up history")
    if fp is None:
        return unknown("D01", bank_details_changed.title,
                       "no payment instruction could be extracted")
    if ledger is None:
        return unknown("D01", bank_details_changed.title, "no ledger available")

    paid = ledger.paid_destinations(key)
    if not paid:
        seen = ledger.seen_destinations(key)
        if not seen:
            return not_applicable(
                "D01", bank_details_changed.title,
                "first document from this supplier: no baseline yet, so this "
                "is an onboarding decision rather than a change")
        return unknown(
            "D01", bank_details_changed.title,
            "this supplier has been seen but never recorded as paid, so "
            "there is no trustworthy baseline to compare against")

    known = {row["fingerprint"] for row in paid}
    if fp in known:
        return ok("D01", bank_details_changed.title,
                  "payee details match a destination we have paid before")

    last = paid[0]
    return fail(
        "D01", bank_details_changed.title,
        Severity.HOLD, FPRisk.LOW,
        evidence=(
            "The payee details on this invoice do not match any account we "
            "have ever paid this supplier at. Last paid destination was "
            f"{last['fingerprint']} on {last['last_paid_at']}."),
        new_fingerprint=fp,
        previous_fingerprint=last["fingerprint"],
        previously_paid_at=last["last_paid_at"],
        action=(
            "Do not pay. Verify by a channel the invoice did not supply: "
            "phone a number from your own records, never one printed on this "
            "document. At payment time your bank's own account-name check "
            "will confirm the name against the account."),
    )


@check("D02", "Payment destination is well formed", "payment")
def payment_format(doc: Document, ledger, ctx) -> list[CheckResult]:
    out = []
    p = doc.payment
    if p.bsb:
        if au.bsb_is_well_formed(p.bsb):
            out.append(ok("D02a", "BSB is well formed", p.bsb))
        else:
            out.append(fail("D02a", "BSB is not well formed",
                            Severity.QUERY, FPRisk.NONE,
                            f"BSB {p.bsb!r} is not six digits. Likely a "
                            f"reading error rather than fraud, but the "
                            f"payment would bounce."))
    if p.account_number and not au.account_number_is_plausible(p.account_number):
        out.append(fail("D02b", "Account number length is implausible",
                        Severity.QUERY, FPRisk.LOW,
                        f"{p.account_number!r} is outside the 5 to 10 digit "
                        f"range Australian accounts use."))
    if p.iban:
        from .qr import iban_checksum_ok
        if iban_checksum_ok(p.iban):
            out.append(ok("D02c", "IBAN checksum passes", p.iban))
        else:
            out.append(fail("D02c", "IBAN fails its checksum",
                            Severity.HOLD, FPRisk.NONE,
                            f"{p.iban!r} does not satisfy the mod-97 check."))
    if not out:
        return [not_applicable("D02", payment_format.title,
                               "no structured payment details extracted")]
    return out


@check("D03", "Payment block was read confidently", "payment")
def payment_confidence(doc: Document, ledger, ctx) -> CheckResult:
    """A one digit error in the payment block is a wrong verdict.

    Low confidence here escalates to a human. It never guesses.
    """
    p = doc.payment
    if p.is_empty():
        return not_applicable("D03", payment_confidence.title,
                              "no payment instruction present")
    threshold = ctx.get("payment_confidence_threshold", 0.90)
    if p.confidence >= threshold:
        return ok("D03", payment_confidence.title,
                  f"confidence {p.confidence:.2f} at or above {threshold:.2f}")
    return fail("D03", payment_confidence.title,
                Severity.QUERY, FPRisk.LOW,
                f"The payment details were read with confidence "
                f"{p.confidence:.2f}, below the {threshold:.2f} required. "
                f"A single wrong digit here sends money to the wrong place, "
                f"so a person should read the block before paying.",
                source=p.source)


@check("D04", "No second payment code on the page", "payment")
def duplicate_payment_codes(doc: Document, ledger, ctx) -> CheckResult:
    """The overlay attack leaves two codes on one page.

    A legitimate invoice almost never carries two payment codes, so finding
    both is the tell -- which is why the decoder has to find every code
    rather than stopping at the first.
    """
    codes = doc.artifacts.get("qr_codes") or []
    if not codes:
        return not_applicable("D04", duplicate_payment_codes.title,
                              "no codes found on the document")
    payment_like = [c for c in codes
                    if (c.get("scheme") in ("emvco", "epc", "upi"))]
    if len(payment_like) <= 1:
        return ok("D04", duplicate_payment_codes.title,
                  f"{len(payment_like)} payment code(s) found")
    return fail("D04", duplicate_payment_codes.title,
                Severity.HOLD, FPRisk.LOW,
                f"{len(payment_like)} separate payment codes were decoded on "
                f"this document. A genuine invoice carries one. This is the "
                f"signature of a payment code pasted over the original.",
                codes=payment_like)


@check("D05", "Code payload destination agrees with the supplier", "payment")
def code_destination_agrees(doc: Document, ledger, ctx) -> list[CheckResult]:
    out = []
    for c in doc.artifacts.get("qr_codes") or []:
        for finding in c.get("findings") or []:
            out.append(fail(
                "D05", code_destination_agrees.title,
                Severity.QUERY, FPRisk.LOW, finding, code=c))
        if c.get("checksum_ok") is False:
            out.append(fail(
                "D05b", "Payment code checksum does not match",
                Severity.HOLD, FPRisk.NONE,
                "The code's own checksum fails, which means the payload was "
                "altered after it was generated.", code=c))
    if not out:
        return [not_applicable("D05", code_destination_agrees.title,
                               "no code findings")]
    return out


def _code_destination_fingerprint(code: dict) -> str | None:
    """Put a decoded destination in the same namespace as document payment.

    Intake stores the parser's destination rather than a PaymentInstruction,
    so the scheme supplies the otherwise implicit destination type. Already
    canonical fingerprints are accepted for agent/library callers.
    """
    destination = code.get("destination")
    if not isinstance(destination, str) or not destination.strip():
        return None
    destination = destination.strip()
    if re.match(r"^au:", destination, re.I):
        return destination.lower()
    if re.match(r"^iban:", destination, re.I):
        return "iban:" + destination.split(":", 1)[1].replace(" ", "").upper()
    if re.match(r"^payid:", destination, re.I):
        return destination.lower()
    if re.match(r"^(bpay|crypto):", destination, re.I):
        return destination
    scheme = str(code.get("scheme") or "").lower()
    if scheme == "epc":
        return "iban:" + destination.replace(" ", "").upper()
    if scheme == "upi":
        return "payid:" + destination.lower()
    return destination


@check("D06", "Payment destination agrees with the decoded code payload",
       "payment")
def decoded_destination_match(doc: Document, ledger, ctx) -> CheckResult:
    codes = doc.artifacts.get("qr_codes") or []
    if not codes:
        return not_applicable("D06", decoded_destination_match.title,
                              "no decoded code payload appears on the document")
    code_destinations = [_code_destination_fingerprint(code) for code in codes]
    if any(value is None for value in code_destinations):
        return unknown("D06", decoded_destination_match.title,
                       "a code was decoded but its payload states no payment "
                       "destination")
    text_destination = doc.payment.fingerprint()
    if text_destination is None:
        return unknown("D06", decoded_destination_match.title,
                       "the code states a payment destination but no text "
                       "payment destination was extracted for comparison")
    if all(value == text_destination for value in code_destinations):
        return ok("D06", decoded_destination_match.title,
                  "the text payment destination and decoded code payload "
                  f"agree on {text_destination}",
                  text_destination=text_destination,
                  code_destinations=code_destinations)
    return fail(
        "D06", decoded_destination_match.title, Severity.HOLD, FPRisk.LOW,
        "The payment destination printed as text does not agree with the "
        "destination inside the decoded code payload.",
        text_destination=text_destination,
        code_destinations=code_destinations,
        action="Do not pay until the destination is verified out of band.")


@check("D07", "BSB appears in the supplied directory", "payment")
def bsb_directory_match(doc: Document, ledger, ctx) -> CheckResult:
    if not doc.payment.bsb:
        return not_applicable("D07", bsb_directory_match.title,
                              "no BSB appears in the payment instruction")
    result = _lookup(ctx, "bsb", doc.payment.bsb)
    if result is None:
        return unknown("D07", bsb_directory_match.title,
                       "no BSB-directory client was supplied")
    evidence = (f"{result.source}, observed {result.observed_at}; "
                f"cache={result.cache}")
    if result.status == "unknown":
        return unknown("D07", bsb_directory_match.title,
                       evidence + f"; lookup unavailable: {result.error}")
    if result.status == "not_found":
        return fail("D07", bsb_directory_match.title,
                    Severity.QUERY, FPRisk.LOW,
                    evidence + "; the directory did not return this BSB",
                    lookup=result.to_dict())
    institution = result.data.get("institution") or "unnamed institution"
    return ok("D07", bsb_directory_match.title,
              evidence + f"; listed institution={institution}",
              lookup=result.to_dict())


# ===========================================================================
# FAMILY B -- business identity
# ===========================================================================

@check("B01", "Supplier ABN is present, valid, and confirmed by the ABR",
       "identity")
def abn_present_and_valid(doc: Document, ledger, ctx) -> CheckResult:
    """The ABN must be printed on the document.

    A required detail that can only be found by going to an external
    register is not, in law, on the invoice. This also happens to be the
    rule that stops a name-based lookup returning a confident wrong answer
    about a completely different company.
    """
    if not doc.supplier_abn:
        if doc.is_foreign_supplier():
            return not_applicable(
                "B01", abn_present_and_valid.title,
                "this is an overseas supplier, which is not required to hold "
                "or quote an ABN. Applying the Australian rule here would "
                "raise a finding on every single overseas invoice.")
        return fail("B01", abn_present_and_valid.title,
                    Severity.QUERY, FPRisk.LOW,
                    "No ABN appears on this document. It is not a valid tax "
                    "invoice without one, the GST credit cannot be claimed, "
                    "and withholding may apply. Do not look the supplier up "
                    "by name to fill the gap: that produces confident wrong "
                    "answers about different companies.")
    if not au.abn_is_valid(doc.supplier_abn):
        return fail("B01", abn_present_and_valid.title,
                    Severity.HOLD, FPRisk.NONE,
                    f"The ABN {doc.supplier_abn!r} fails its checksum, so it is "
                    f"not a real ABN. An invented number passes this test only "
                    f"about one time in 89.")

    formatted = au.abn_format(doc.supplier_abn)
    result = _lookup(ctx, "abr", doc.supplier_abn)
    if result is None:
        # A valid checksum is a real, deterministic result on its own. The
        # register lookup is an enhancement, never a precondition. Requiring
        # it would mean a business with nothing configured could never see a
        # green verdict, which defeats the point of the tool working the
        # moment it is plugged in. Register confirmation lives in B03.
        return ok("B01", abn_present_and_valid.title,
                  f"ABN {formatted} passes the checksum")
    evidence = (f"ABN {formatted} passes the checksum; {result.source} was "
                f"asked on {result.observed_at}; cache={result.cache}")
    if result.status == "unknown":
        # The checksum still passed. A register that could not be reached
        # does not undo arithmetic.
        return ok("B01", abn_present_and_valid.title,
                  evidence + f"; register unreachable: {result.error}",
                  lookup=result.to_dict())
    if result.status == "not_found":
        return fail("B01", abn_present_and_valid.title,
                    Severity.HOLD, FPRisk.LOW,
                    evidence + "; the register did not return this ABN",
                    lookup=result.to_dict())

    status = str(result.data.get("status") or "").strip()
    if status.casefold() in {"cancelled", "canceled"}:
        return fail("B01", abn_present_and_valid.title,
                    Severity.HOLD, FPRisk.LOW,
                    evidence + f"; register status={status}",
                    lookup=result.to_dict())
    if status.casefold() != "active":
        return unknown("B01", abn_present_and_valid.title,
                       evidence + "; register did not return a recognised "
                       f"current status (status={status or 'missing'})",
                       lookup=result.to_dict())
    return ok("B01", abn_present_and_valid.title,
              evidence + "; register status=Active",
              lookup=result.to_dict())


@check("B02", "Buyer identity present when the law requires it", "identity")
def buyer_identity_present(doc: Document, ledger, ctx) -> CheckResult:
    if doc.total_cents is None:
        return unknown("B02", buyer_identity_present.title, "no total extracted")
    if doc.total_cents < au.BUYER_IDENTITY_REQUIRED_AT_OR_ABOVE_CENTS:
        return not_applicable(
            "B02", buyer_identity_present.title,
            f"total ${from_cents(doc.total_cents)} is below the threshold")
    if doc.buyer_name or doc.buyer_abn:
        return ok("B02", buyer_identity_present.title, "buyer identified")
    # A note about the supplier's paperwork, not a reason to withhold
    # payment. The recipient of an invoice cannot fix its format, and
    # gating on it would put a warning on a large share of ordinary bills.
    return fail("B02", buyer_identity_present.title,
                Severity.INFO, FPRisk.LOW,
                f"At ${from_cents(doc.total_cents)} an invoice must also name "
                f"the buyer or their ABN, and this one does not. It is safe "
                f"to pay, but ask the supplier to reissue it if you want the "
                f"GST credit to stand up.")


@check("B03", "Supplier registration was active on the invoice date",
       "identity")
def abn_registry_match(doc: Document, ledger, ctx) -> CheckResult:
    if not doc.supplier_abn:
        return unknown("B03", abn_registry_match.title,
                       "no ABN was extracted, so an identifier-safe lookup "
                       "cannot run")
    result = _lookup(ctx, "abr", doc.supplier_abn)
    if result is None:
        return unknown("B03", abn_registry_match.title,
                       "no ABR lookup client was supplied")
    evidence = (f"{result.source}, observed {result.observed_at}; "
                f"cache={result.cache}")
    if result.status == "unknown":
        return unknown("B03", abn_registry_match.title,
                       evidence + f"; lookup unavailable: {result.error}")
    if result.status == "not_found":
        return fail("B03", abn_registry_match.title,
                    Severity.QUERY, FPRisk.LOW,
                    evidence + "; the ABR lookup did not return this ABN",
                    lookup=result.to_dict())
    name = result.data.get("name") or "name not returned"
    status = str(result.data.get("status") or "").strip()
    if not doc.issue_date:
        return unknown("B03", abn_registry_match.title,
                       evidence + "; no invoice date was extracted, so the "
                       "supplier's status on that date cannot be determined",
                       lookup=result.to_dict())
    try:
        invoice_date = _dt.date.fromisoformat(doc.issue_date[:10])
    except (TypeError, ValueError):
        return unknown("B03", abn_registry_match.title,
                       evidence + f"; invoice date {doc.issue_date!r} is not "
                       "a valid ISO date",
                       lookup=result.to_dict())

    if status.casefold() in {"cancelled", "canceled"}:
        effective_value = result.data.get("status_effective_from")
        if not effective_value:
            return unknown(
                "B03", abn_registry_match.title,
                evidence + "; ABN is cancelled but the register did not "
                "return its cancellation effective date",
                lookup=result.to_dict())
        try:
            cancelled_from = _dt.date.fromisoformat(str(effective_value)[:10])
        except ValueError:
            return unknown(
                "B03", abn_registry_match.title,
                evidence + f"; cancellation effective date "
                f"{effective_value!r} is not a valid ISO date",
                lookup=result.to_dict())
        dated_evidence = (
            evidence + f"; entity={name}; ABN status=Cancelled effective "
            f"{cancelled_from.isoformat()}; invoice date="
            f"{invoice_date.isoformat()}")
        if cancelled_from <= invoice_date:
            return fail("B03", abn_registry_match.title,
                        Severity.HOLD, FPRisk.LOW,
                        dated_evidence + "; supplier was not active on the "
                        "invoice date",
                        lookup=result.to_dict())
        return ok("B03", abn_registry_match.title,
                  dated_evidence + "; cancellation occurred after the "
                  "invoice date",
                  lookup=result.to_dict())
    if status.casefold() != "active":
        return unknown("B03", abn_registry_match.title,
                       evidence + "; the register did not return a recognised "
                       f"status (status={status or 'missing'})",
                       lookup=result.to_dict())
    return ok("B03", abn_registry_match.title,
              evidence + f"; entity={name}; ABN status=Active; invoice date="
              f"{invoice_date.isoformat()}",
              lookup=result.to_dict())


# ===========================================================================
# FAMILY E -- document integrity and external watchlists
# ===========================================================================

def _producer_tool(doc: Document) -> str | None:
    """Return normalized producer metadata across supported artifact shapes."""
    direct = doc.artifacts.get("producer_tool")
    metadata = doc.artifacts.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    value = direct or metadata.get("producer_tool") or metadata.get("producer")
    if not isinstance(value, str) or not value.strip():
        return None
    return " ".join(value.split()).casefold()


@check("E02", "Document producer matches this supplier's own history",
       "document_integrity")
def producer_tool_drift(doc: Document, ledger, ctx) -> CheckResult:
    """Producer drift is weak alone, but corroborates payment redirection."""
    current = _producer_tool(doc)
    key = doc.supplier_key()
    if current is None:
        return unknown("E02", producer_tool_drift.title,
                       "no document producer tool was extracted")
    if key is None:
        return unknown("E02", producer_tool_drift.title,
                       "no supplier key: cannot look up producer history")
    if ledger is None:
        return unknown("E02", producer_tool_drift.title,
                       "no ledger available for producer history")

    previous = set()
    for row in ledger.supplier_documents(key):
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except (TypeError, ValueError):
            continue
        value = payload.get("producer_tool")
        if isinstance(value, str) and value.strip():
            previous.add(" ".join(value.split()).casefold())

    if not previous:
        return not_applicable(
            "E02", producer_tool_drift.title,
            "first document with producer metadata for this supplier: no "
            "producer baseline exists yet")
    if current in previous:
        return ok("E02", producer_tool_drift.title,
                  f"producer tool {current!r} appears in supplier history")

    fp = doc.payment.fingerprint()
    paid_fingerprints = {
        row["fingerprint"] for row in ledger.paid_destinations(key)
    }
    payment_changed = bool(
        fp and paid_fingerprints and fp not in paid_fingerprints)
    history = ", ".join(sorted(previous))
    evidence = (f"producer tool changed to {current!r}; this supplier's "
                f"recorded producer history is {history}")
    if payment_changed:
        return fail(
            "E02", producer_tool_drift.title, Severity.HOLD, FPRisk.LOW,
            evidence + "; the payment destination also differs from every "
            "destination previously paid for this supplier",
            current_producer=current, previous_producers=sorted(previous),
            payment_fingerprint=fp)
    return fail(
        "E02", producer_tool_drift.title, Severity.INFO, FPRisk.HIGH,
        evidence + "; producer drift alone is informational because suppliers "
        "legitimately change invoicing software",
        current_producer=current, previous_producers=sorted(previous))


@check("E01", "Supplier name has no exact supplied sanctions-list match",
       "sanctions")
def sanctions_exact_name(doc: Document, ledger, ctx) -> CheckResult:
    if not doc.supplier_name:
        return unknown("E01", sanctions_exact_name.title,
                       "no supplier name was extracted")
    result = _lookup(ctx, "sanctions", doc.supplier_name)
    if result is None:
        return unknown("E01", sanctions_exact_name.title,
                       "no sanctions lookup client was supplied")
    evidence = (f"{result.source}, observed {result.observed_at}; "
                f"cache={result.cache}")
    if result.status == "unknown":
        return unknown("E01", sanctions_exact_name.title,
                       evidence + f"; lookup unavailable: {result.error}")
    if result.status == "found":
        return fail(
            "E01", sanctions_exact_name.title, Severity.HOLD, FPRisk.LOW,
            evidence + "; an exact normalized-name match was returned; "
            "a person must resolve identity before any payment decision",
            lookup=result.to_dict())
    return ok("E01", sanctions_exact_name.title,
              evidence + "; no exact normalized-name match was returned; "
              "this does not exclude aliases or spelling differences",
              lookup=result.to_dict())


# ===========================================================================
# FAMILY F -- legal validity and arithmetic
# ===========================================================================

@check("F01", "Document declares itself a tax invoice", "legal")
def declares_tax_invoice(doc: Document, ledger, ctx) -> CheckResult:
    text = (doc.doc_type_words or "") + " " + (doc.raw_text or "")
    if au.looks_like_rcti(text):
        return ok("F01", declares_tax_invoice.title,
                  "declares itself a recipient created tax invoice")
    if au.looks_like_tax_invoice(text):
        return ok("F01", declares_tax_invoice.title,
                  "carries the words 'tax invoice'")
    if not text.strip():
        return unknown("F01", declares_tax_invoice.title,
                       "no document text available to search")
    if doc.text_source != "document":
        return unknown(
            "F01", declares_tax_invoice.title,
            "only the covering email was read, and the words that make a "
            "document a tax invoice normally sit in the attached file. "
            "Searching an email body cannot answer this either way.")
    return fail("F01", declares_tax_invoice.title,
                Severity.QUERY, FPRisk.MODERATE,
                "The document does not say it is a tax invoice. A structured "
                "electronic invoice satisfies this without the words, so "
                "check the format before treating it as a defect.")


@check("F02", "Totals add up", "legal")
def totals_add_up(doc: Document, ledger, ctx) -> CheckResult:
    """Deterministic, and the best value in the whole pipeline.

    Making the numbers reconcile catches extraction errors that no
    confidence score ever surfaced.
    """
    if None in (doc.subtotal_cents, doc.tax_cents, doc.total_cents):
        return unknown("F02", totals_add_up.title,
                       "subtotal, tax or total not extracted")
    if au.totals_are_consistent(doc.subtotal_cents, doc.tax_cents,
                                doc.total_cents):
        return ok("F02", totals_add_up.title,
                  f"{from_cents(doc.subtotal_cents)} + "
                  f"{from_cents(doc.tax_cents)} = "
                  f"{from_cents(doc.total_cents)}")
    diff = (doc.subtotal_cents + doc.tax_cents) - doc.total_cents
    return fail("F02", totals_add_up.title,
                Severity.QUERY, FPRisk.NONE,
                f"Subtotal {from_cents(doc.subtotal_cents)} plus tax "
                f"{from_cents(doc.tax_cents)} does not equal the total "
                f"{from_cents(doc.total_cents)}. Out by "
                f"{from_cents(diff)}.",
                difference_cents=diff)


@check("F03", "Line items sum to the subtotal", "legal")
def lines_sum_to_subtotal(doc: Document, ledger, ctx) -> CheckResult:
    lines = [li for li in doc.line_items if li.amount_cents is not None]
    if not lines:
        return unknown("F03", lines_sum_to_subtotal.title,
                       "no line item amounts extracted")
    if doc.subtotal_cents is None:
        return unknown("F03", lines_sum_to_subtotal.title, "no subtotal")
    total = sum(li.amount_cents for li in lines)
    tolerance = au.LINE_TOLERANCE_CENTS * max(1, len(lines))
    if abs(total - doc.subtotal_cents) <= tolerance:
        return ok("F03", lines_sum_to_subtotal.title,
                  f"{len(lines)} lines sum to {from_cents(total)}")
    return fail("F03", lines_sum_to_subtotal.title,
                Severity.QUERY, FPRisk.LOW,
                f"The {len(lines)} line items sum to {from_cents(total)} but "
                f"the subtotal says {from_cents(doc.subtotal_cents)}. Line "
                f"item reading is the weakest part of any extractor, so "
                f"check the document before treating this as a dispute.",
                line_total_cents=total)


@check("F04", "GST is arithmetically correct", "legal")
def gst_arithmetic(doc: Document, ledger, ctx) -> CheckResult:
    if doc.tax_cents is None or doc.total_cents is None:
        return unknown("F04", gst_arithmetic.title, "tax or total not extracted")
    if doc.currency and doc.currency.upper() != "AUD":
        return not_applicable("F04", gst_arithmetic.title,
                              f"currency is {doc.currency}, not AUD")
    if doc.tax_cents == 0:
        return not_applicable("F04", gst_arithmetic.title,
                              "no GST stated: nothing to reconcile here")

    mixed = _has_mixed_tax_codes(doc)
    if mixed:
        taxable = sum(li.amount_cents or 0 for li in doc.line_items
                      if (li.tax_code or "").upper() in ("GST", "TAXABLE"))
        if taxable:
            expected = au.gst_on_exclusive_amount(taxable)
            if abs(expected - doc.tax_cents) <= au.TOTAL_TOLERANCE_CENTS:
                return ok("F04", gst_arithmetic.title,
                          "GST matches the taxable lines only, as it should "
                          "on a mixed invoice")
            return fail("F04", gst_arithmetic.title,
                        Severity.QUERY, FPRisk.MODERATE,
                        f"This invoice mixes taxable and GST-free lines. GST "
                        f"should be {from_cents(expected)} on the taxable "
                        f"portion but {from_cents(doc.tax_cents)} is stated. "
                        f"Depends on the line classification being right, "
                        f"which extractors often get wrong.")
        return unknown("F04", gst_arithmetic.title,
                       "mixed tax codes present but taxable lines not summable")

    if au.inclusive_total_is_consistent(doc.total_cents, doc.tax_cents):
        return ok("F04", gst_arithmetic.title,
                  f"GST {from_cents(doc.tax_cents)} is one eleventh of "
                  f"{from_cents(doc.total_cents)}")
    expected = au.gst_on_inclusive_total(doc.total_cents)
    return fail("F04", gst_arithmetic.title,
                Severity.QUERY, FPRisk.MODERATE,
                f"GST on a wholly taxable total of "
                f"{from_cents(doc.total_cents)} should be "
                f"{from_cents(expected)}, but {from_cents(doc.tax_cents)} is "
                f"stated. If the invoice mixes GST-free items this is "
                f"expected and the line classification needs checking.",
                expected_cents=expected)


def _has_mixed_tax_codes(doc: Document) -> bool:
    codes = {(li.tax_code or "").upper() for li in doc.line_items if li.tax_code}
    return len(codes) > 1


@check("F05", "No ABN withholding obligation", "legal")
def no_abn_withholding(doc: Document, ledger, ctx) -> CheckResult:
    """An obligation rather than a fraud signal, and nobody else computes it.

    Failing to withhold carries a penalty equal to the amount that should
    have been withheld, so it is worth surfacing before payment.
    """
    if doc.supplier_abn and au.abn_is_valid(doc.supplier_abn):
        return ok("F05", no_abn_withholding.title, "a valid ABN is quoted")
    if doc.is_foreign_supplier():
        return not_applicable(
            "F05", no_abn_withholding.title,
            "no-ABN withholding applies to payments to Australian "
            "enterprises, not to an overseas supplier")
    base = doc.subtotal_cents
    if base is None:
        base = doc.total_cents
    if base is None:
        return unknown("F05", no_abn_withholding.title, "no amount extracted")
    withhold = au.no_abn_withholding_cents(base)
    if withhold == 0:
        return ok("F05", no_abn_withholding.title,
                  f"no ABN quoted, but ${from_cents(base)} is at or below "
                  f"the ${from_cents(au.NO_ABN_WITHHOLDING_THRESHOLD_CENTS)} "
                  f"threshold")
    return fail("F05", no_abn_withholding.title,
                Severity.INFO, FPRisk.LOW,
                f"No valid ABN is quoted and the payment is "
                f"${from_cents(base)}. Withholding of "
                f"${from_cents(withhold)} may be required, and the penalty "
                f"for not withholding equals that amount. An exemption may "
                f"apply, so check before paying in full.",
                withhold_cents=withhold)


# ===========================================================================
# FAMILY G -- duplicates and anomalies
# ===========================================================================

@check("G01", "Not a duplicate of an invoice already recorded", "duplicate")
def duplicate_invoice(doc: Document, ledger, ctx) -> CheckResult:
    if ledger is None:
        return unknown("G01", duplicate_invoice.title, "no ledger available")
    key = doc.supplier_key()
    if key is None or not doc.invoice_number:
        return unknown("G01", duplicate_invoice.title,
                       "supplier or invoice number missing")
    matches = [m for m in ledger.find_by_invoice_number(key, doc.invoice_number)
               if m["doc_id"] != doc.doc_id]
    if not matches:
        return ok("G01", duplicate_invoice.title,
                  "invoice number not seen before for this supplier")

    changed_payee = _payee_changed_against(doc, matches, ledger, key)
    if changed_payee:
        return fail("G01", "Same invoice number, different payee details",
                    Severity.HOLD, FPRisk.LOW,
                    f"Invoice {doc.invoice_number} has been seen before from "
                    f"this supplier, and the payment details are not the "
                    f"same. That combination is re-presentation of a real "
                    f"invoice with an altered destination, not a filing "
                    f"mistake.",
                    matches=[m["doc_id"] for m in matches])

    already_paid = [m for m in matches if m.get("paid_at")]
    if already_paid:
        return fail("G01", duplicate_invoice.title,
                    Severity.HOLD, FPRisk.LOW,
                    f"Invoice {doc.invoice_number} was already paid on "
                    f"{already_paid[0]['paid_at']}.",
                    matches=[m["doc_id"] for m in already_paid])
    return fail("G01", duplicate_invoice.title,
                Severity.QUERY, FPRisk.MODERATE,
                f"Invoice number {doc.invoice_number} has been seen before "
                f"from this supplier. Progress and instalment billing "
                f"legitimately reuses a number, so check before rejecting.",
                matches=[m["doc_id"] for m in matches])


def _payee_changed_against(doc, matches, ledger, key) -> bool:
    fp = doc.payment.fingerprint()
    if fp is None:
        return False
    import json as _json
    for m in matches:
        try:
            payload = _json.loads(m.get("payload_json") or "{}")
        except Exception:
            continue
        prior = payload.get("payment_fingerprint")
        if prior and prior != fp:
            return True
    return False


@check("G02", "Balance due is not already zero", "duplicate")
def already_settled(doc: Document, ledger, ctx) -> CheckResult:
    """The commonest cause of accidental double payment.

    Software sends a 'tax invoice' AFTER charging the card, and the document
    says paid, or amount due zero, in small type. Read the balance due
    field, never the invoice total.
    """
    if doc.balance_due_cents is None:
        return unknown("G02", already_settled.title,
                       "no balance-due field extracted; read the document "
                       "rather than assuming the total is payable")
    if doc.balance_due_cents > 0:
        return ok("G02", already_settled.title,
                  f"balance due {from_cents(doc.balance_due_cents)}")
    # Nothing is owed, so nothing can be paid twice by acting on this. That
    # is not a fraud hold, it is a receipt. Calling it HOLD would put a red
    # verdict on every payment confirmation a business receives, which is
    # how a checker teaches its user to ignore it.
    return fail("G02", already_settled.title,
                Severity.SETTLED, FPRisk.NONE,
                f"This document shows a balance due of "
                f"{from_cents(doc.balance_due_cents)}. It is a receipt for "
                f"something already paid, not a bill. File it; there is "
                f"nothing to action.")


@check("G03", "Invoice is not dated after it was paid", "duplicate")
def dated_after_payment(doc: Document, ledger, ctx) -> CheckResult:
    """The highest-signal date test there is."""
    paid_at = ctx.get("paid_at")
    if not paid_at or not doc.issue_date:
        return not_applicable("G03", dated_after_payment.title,
                              "no payment date to compare against")
    try:
        issue = _dt.date.fromisoformat(doc.issue_date[:10])
        paid = _dt.date.fromisoformat(str(paid_at)[:10])
    except ValueError:
        return unknown("G03", dated_after_payment.title, "unparseable date")
    if issue <= paid:
        return ok("G03", dated_after_payment.title, "issued before payment")
    return fail("G03", dated_after_payment.title,
                Severity.QUERY, FPRisk.NONE,
                f"The invoice is dated {doc.issue_date} but payment is "
                f"recorded on {paid_at}. An invoice dated after it was paid "
                f"is almost always a control failure or backdating.")


# ===========================================================================
# FAMILY C -- relationship
# ===========================================================================

@check("C01", "We have dealt with this supplier before", "relationship")
def known_supplier(doc: Document, ledger, ctx) -> CheckResult:
    if ledger is None:
        return unknown("C01", known_supplier.title, "no ledger available")
    key = doc.supplier_key()
    if key is None:
        return unknown("C01", known_supplier.title, "no supplier key")
    if ledger.is_known_supplier(key):
        s = ledger.get_supplier(key)
        return ok("C01", known_supplier.title,
                  f"{s['invoice_count']} prior document(s), first seen "
                  f"{s['first_seen']}")
    return fail("C01", known_supplier.title,
                Severity.QUERY, FPRisk.MODERATE,
                "No record of ever dealing with this supplier. Genuine first "
                "invoices look exactly like this, so it routes to supplier "
                "onboarding rather than to fraud.",
                supplier_key=key)


# ===========================================================================
# FAMILY A -- channel.  Necessary, but never decisive.
# ===========================================================================

@check("A01", "What the arrival channel allows us to claim", "channel")
def channel_forensics(doc: Document, ledger, ctx) -> CheckResult:
    """Records honestly what could and could not be checked.

    Email authentication is sixth of eight for real detection yield, because
    the dominant attacks pass it by construction: a compromised supplier
    mailbox, a hijacked accounting account, or an attacker on a legitimate
    invoicing platform all authenticate perfectly.
    """
    from .model import forensic_grade, FORENSIC_CHANNELS
    grade = forensic_grade(doc.channel)
    if doc.channel in FORENSIC_CHANNELS:
        return ok("A01", channel_forensics.title,
                  f"arrived by {doc.channel.value}; sender checks are "
                  f"available ({grade})")
    if doc.channel is Channel.FORWARD_INLINE:
        return unknown(
            "A01", channel_forensics.title,
            "This was forwarded inline, which rebuilds the message and turns "
            "the original headers into body text. Sender verification is "
            "unavailable, not failed. To preserve it, forward as an "
            "attachment instead, or have the mail server copy it "
            "automatically.")
    return unknown(
        "A01", channel_forensics.title,
        f"Arrived by {doc.channel.value}, so no email forensics exist for "
        f"it at all. All weight moves to the payment, register and history "
        f"checks.")


@check("A02", "Sender domain against this supplier's history", "channel")
def sender_domain_known(doc: Document, ledger, ctx) -> CheckResult:
    """A different sending domain is normal, not suspicious.

    Real invoices arrive from accounting-platform relays rather than the
    supplier's own domain. Flagging that would flag every legitimate
    relayed invoice, so the question is whether this supplier changed relay
    AND changed bank details, which D01 answers.
    """
    if ledger is None or not doc.supplier_domain:
        return unknown("A02", sender_domain_known.title,
                       "no sender domain or no ledger")
    key = doc.supplier_key()
    if key is None:
        return unknown("A02", sender_domain_known.title, "no supplier key")
    known = ledger.known_senders(key)
    dom = doc.supplier_domain.lower()
    if not known:
        return not_applicable("A02", sender_domain_known.title,
                              "no sending history for this supplier yet")
    if dom in known:
        return ok("A02", sender_domain_known.title,
                  f"{dom} has sent for this supplier before")
    return fail("A02", sender_domain_known.title,
                Severity.QUERY, FPRisk.HIGH,
                f"This arrived from {dom}, which has not sent for this "
                f"supplier before. Suppliers switch accounting platforms "
                f"routinely, so on its own this means very little. It "
                f"matters only alongside a change of payment details.",
                known_senders=known)


# ===========================================================================
# FAMILY H -- known scam shapes.  Pure pattern, near-zero cost.
# ===========================================================================

_SOLICITATION_RE = re.compile(
    r"this\s+is\s+not\s+a\s+bill|not\s+a\s+bill[.,]?\s*this\s+is\s+a\s+"
    r"solicitation|this\s+is\s+a\s+solicitation\s+for\s+the\s+order", re.I)


@check("H01", "Not a solicitation dressed as an invoice", "scam")
def solicitation_disclaimer(doc: Document, ledger, ctx) -> CheckResult:
    """Fake directory and domain-renewal letters must legally carry a
    disclaimer saying they are a solicitation. Finding it is the sender's
    own admission that nothing is owed."""
    text = doc.raw_text or ""
    if not text.strip():
        return unknown("H01", solicitation_disclaimer.title, "no text")
    m = _SOLICITATION_RE.search(text)
    if not m:
        return ok("H01", solicitation_disclaimer.title,
                  "no solicitation disclaimer found")
    return fail("H01", solicitation_disclaimer.title,
                Severity.HOLD, FPRisk.NONE,
                f"This document carries the wording {m.group(0)!r}. That "
                f"disclaimer is required on a solicitation posing as a "
                f"bill. Nothing is owed and no obligation exists.")


_PHONE_ONLY_HINT_RE = re.compile(
    r"(call|phone|contact)\s+(us|our|support|customer\s+service|the\s+"
    r"support\s+team)|(support|helpline|toll[\s-]?free)\s*(number|line)", re.I)


@check("H02", "It wants payment, not a phone call", "scam")
def wants_a_phone_call(doc: Document, ledger, ctx) -> CheckResult:
    """The cleanest single discriminator found in the research.

    A genuine invoice wants you to pay it. The refund scam has no payment
    details at all, only a support number, because the payload is the call.
    """
    text = doc.raw_text or ""
    if not text.strip():
        return unknown("H02", wants_a_phone_call.title, "no text")
    if not doc.payment.is_empty():
        return ok("H02", wants_a_phone_call.title,
                  "the document supplies payment details")
    if doc.balance_due_cents is not None and doc.balance_due_cents <= 0:
        return not_applicable(
            "H02", wants_a_phone_call.title,
            "nothing is owed on this document, so the absence of payment "
            "details is exactly what a receipt looks like")
    if doc.balance_due_cents is None and doc.total_cents is None:
        # The scam shape is "an amount is stated, but the only way to act on
        # it is a phone call". With no amount demanded at all this is a
        # notification -- a renewal reminder, a statement, a confirmation --
        # and the absence of payment details is simply correct.
        return not_applicable(
            "H02", wants_a_phone_call.title,
            "no amount is being demanded, so this is a notice rather than a "
            "bill and there is nothing to pay")
    if not _PHONE_ONLY_HINT_RE.search(text):
        return unknown("H02", wants_a_phone_call.title,
                       "no payment details found and no support number "
                       "either; the document may simply not be a bill")
    return fail("H02", wants_a_phone_call.title,
                Severity.HOLD, FPRisk.LOW,
                "This document states an amount but gives no way to pay it, "
                "only a number to call. A real invoice wants to be paid. "
                "This shape exists to get you on the phone, where the caller "
                "asks for remote access to your computer.")


# ===========================================================================
# FAMILY I -- actionable timing
# ===========================================================================

_PAYMENT_SCHEDULE_RULES = {
    "NSW": {
        "business_days": 10,
        "year_end_exclusions": ((12, 27, 31),),
        "source": (
            "NSW Building and Construction Industry Security of Payment "
            "Act 1999, s 14(4)"
        ),
        "source_url": (
            "https://legislation.nsw.gov.au/view/whole/html/inforce/current/"
            "act-1999-046#sec.14"
        ),
    },
    "QLD": {
        "business_days": 15,
        "year_end_exclusions": ((12, 22, 24), (12, 27, 31),
                                (1, 2, 10)),
        "source": (
            "Queensland Building Industry Fairness (Security of Payment) "
            "Act 2017, s 76(1)"
        ),
        "source_url": (
            "https://www.legislation.qld.gov.au/view/html/inforce/current/"
            "act-2017-043#sec.76"
        ),
    },
}


def _add_business_days(start: _dt.date, days: int,
                       holidays: set[_dt.date], exclusions: tuple) -> _dt.date:
    """Count weekdays after ``start``; supplied public holidays do not count."""
    current = start
    remaining = days
    while remaining:
        current += _dt.timedelta(days=1)
        excluded = any(current.month == month and first <= current.day <= last
                       for month, first, last in exclusions)
        if (current.weekday() < 5 and current not in holidays and
                not excluded):
            remaining -= 1
    return current


@check("I01", "Payment due date is present and actionable", "deadline")
def payment_due_date(doc: Document, ledger, ctx) -> CheckResult:
    if doc.balance_due_cents is not None and doc.balance_due_cents <= 0:
        return not_applicable("I01", payment_due_date.title,
                              "the document has no outstanding balance")
    if not doc.due_date:
        return unknown("I01", payment_due_date.title,
                       "no due date was extracted")
    try:
        due = _dt.date.fromisoformat(doc.due_date[:10])
        raw_as_of = ctx.get("as_of")
        as_of = (_dt.date.fromisoformat(str(raw_as_of)[:10]) if raw_as_of
                 else _dt.datetime.now(_dt.timezone.utc).date())
    except (TypeError, ValueError):
        return unknown("I01", payment_due_date.title,
                       "due date or as-of date is not valid ISO 8601")
    days = (due - as_of).days
    if days < 0:
        return fail("I01", payment_due_date.title,
                    Severity.QUERY, FPRisk.NONE,
                    f"due {due.isoformat()}, {-days} day(s) overdue as of "
                    f"{as_of.isoformat()}; timing reminder only, not legal "
                    "advice",
                    timing_state="overdue", days_until_due=days,
                    due_date=due.isoformat(), as_at=as_of.isoformat())
    if days == 0:
        return ok("I01", payment_due_date.title,
                  f"due today, {due.isoformat()}, as of {as_of.isoformat()}; "
                  "timing reminder only, not legal advice",
                  timing_state="due_today", days_until_due=0,
                  due_date=due.isoformat(), as_at=as_of.isoformat())
    return ok("I01", payment_due_date.title,
              f"upcoming: due {due.isoformat()}, {days} day(s) remaining as "
              f"of {as_of.isoformat()}; timing reminder only, not legal "
              "advice",
              timing_state="upcoming", days_until_due=days,
              due_date=due.isoformat(), as_at=as_of.isoformat())


@check("I02", "Payment claim reply deadline is actionable", "deadline")
def payment_claim_reply_deadline(doc: Document, ledger, ctx) -> CheckResult:
    claim = doc.artifacts.get("payment_claim")
    if claim is None:
        return not_applicable(
            "I02", payment_claim_reply_deadline.title,
            "document is not identified as a construction payment claim")
    if not isinstance(claim, dict):
        return unknown("I02", payment_claim_reply_deadline.title,
                       "payment claim metadata is not an object")

    state = str(claim.get("state") or "").strip().upper()
    rule = _PAYMENT_SCHEDULE_RULES.get(state)
    if rule is None:
        return unknown("I02", payment_claim_reply_deadline.title,
                       f"no payment-schedule rule is configured for {state or 'missing state'}")
    try:
        served = _dt.date.fromisoformat(str(claim.get("served_date"))[:10])
        raw_as_of = ctx.get("as_of")
        as_of = (_dt.date.fromisoformat(str(raw_as_of)[:10]) if raw_as_of
                 else _dt.datetime.now(_dt.timezone.utc).date())
        raw_holidays = (ctx.get("public_holidays") or {}).get(state, [])
        holidays = {_dt.date.fromisoformat(str(value)[:10])
                    for value in raw_holidays}
    except (AttributeError, TypeError, ValueError):
        return unknown("I02", payment_claim_reply_deadline.title,
                       "served date, as-of date, or public holiday is not valid ISO 8601")

    deadline = _add_business_days(served, rule["business_days"], holidays,
                                  rule["year_end_exclusions"])
    days = (deadline - as_of).days
    timing_state = ("overdue" if days < 0 else
                    "due_today" if days == 0 else "upcoming")
    evidence = (
        f"{state} statutory outer reply date {deadline.isoformat()} "
        f"({rule['business_days']} business days after service on "
        f"{served.isoformat()}), under {rule['source']}; an earlier period "
        "in the construction contract may control; timing reminder only, "
        "not legal advice"
    )
    detail = {
        "timing_state": timing_state,
        "days_until_deadline": days,
        "served_date": served.isoformat(),
        "deadline_date": deadline.isoformat(),
        "as_at": as_of.isoformat(),
        "jurisdiction": state,
        "business_days": rule["business_days"],
        "source": rule["source"],
        "source_url": rule["source_url"],
    }
    if days < 0:
        return fail("I02", payment_claim_reply_deadline.title,
                    Severity.QUERY, FPRisk.NONE, evidence, **detail)
    return ok("I02", payment_claim_reply_deadline.title, evidence, **detail)
