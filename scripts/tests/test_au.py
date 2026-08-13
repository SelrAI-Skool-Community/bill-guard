"""Australian deterministic validators. These must never false-positive."""

from harness import test, eq, true, false, raises, main
from billguard import au
from billguard.model import to_cents, from_cents


# --- ABN checksum ----------------------------------------------------------

@test
def real_abns_pass():
    # Selr AI's own trust, and its tested-live pair from the architecture work.
    true(au.abn_is_valid("40156575753"), "Heka Family Trust ABN")
    true(au.abn_is_valid("98273029681"), "Tiles by Morrissey Trust ABN")
    true(au.abn_is_valid("12706916336"), "the cancelled partnership ABN")
    true(au.abn_is_valid("40 156 575 753"), "spaces must be tolerated")
    true(au.abn_is_valid("40-156-575-753"), "hyphens must be tolerated")


@test
def wrong_length_fails():
    false(au.abn_is_valid("4015657575"), "10 digits")
    false(au.abn_is_valid("401565757530"), "12 digits")
    false(au.abn_is_valid(""), "empty")
    false(au.abn_is_valid(None), "none")


@test
def single_digit_change_fails():
    # A checksum that does not catch a transposition is not doing its job.
    true(au.abn_is_valid("40156575753"))
    false(au.abn_is_valid("40156575754"), "last digit changed")
    false(au.abn_is_valid("41156575753"), "second digit changed")
    false(au.abn_is_valid("40156575735"), "last two transposed")


@test
def invented_abns_almost_always_fail():
    # The claim in the design is roughly 1 in 89. Assert the order of
    # magnitude so a broken implementation that passes everything is caught.
    passes = sum(1 for n in range(10_000_000_000, 10_000_002_000)
                 if au.abn_is_valid(str(n)))
    true(passes < 60, f"{passes} of 2000 sequential numbers passed; "
                      f"expected roughly 2000/89 = 22")
    true(passes > 5, f"only {passes} passed; checksum may be over-strict")


@test
def abn_format_groups_correctly():
    eq(au.abn_format("40156575753"), "40 156 575 753")
    eq(au.abn_format("bad"), None)


# --- ACN -------------------------------------------------------------------

@test
def acn_checksum_works():
    true(au.acn_is_valid("678725301"), "Heka Corporation ACN")
    false(au.acn_is_valid("678725302"), "check digit changed")
    false(au.acn_is_valid("12345678"), "8 digits")


@test
def abn_recovers_from_acn():
    got = au.abn_from_acn("678725301")
    true(got is not None, "should find a valid prefix")
    true(au.abn_is_valid(got), "recovered ABN must itself validate")
    true(got.endswith("678725301"), "must keep the ACN as the tail")


# --- BSB -------------------------------------------------------------------

@test
def bsb_format_rules():
    true(au.bsb_is_well_formed("062-000"))
    true(au.bsb_is_well_formed("062000"))
    false(au.bsb_is_well_formed("62-000"), "5 digits")
    false(au.bsb_is_well_formed("0620000"), "7 digits")
    false(au.bsb_is_well_formed("abc-def"))
    false(au.bsb_is_well_formed(None))


@test
def bsb_normalises():
    eq(au.bsb_normalise("062000"), "062-000")
    eq(au.bsb_normalise("062-000"), "062-000")
    eq(au.bsb_normalise("bad"), None)


@test
def account_number_length():
    true(au.account_number_is_plausible("12345678"))
    true(au.account_number_is_plausible("12345"))
    false(au.account_number_is_plausible("1234"), "too short")
    false(au.account_number_is_plausible("12345678901"), "too long")


# --- GST -------------------------------------------------------------------

@test
def gst_on_inclusive_total_is_one_eleventh():
    eq(au.gst_on_inclusive_total(11000), 1000, "$110.00 inc GST -> $10.00")
    eq(au.gst_on_inclusive_total(0), 0)
    eq(au.gst_on_inclusive_total(100), 9, "$1.00 -> 9c, half-up")
    eq(au.gst_on_inclusive_total(106810), 9710, "the live Tiles invoice total")


@test
def gst_on_exclusive_amount_is_ten_percent():
    eq(au.gst_on_exclusive_amount(10000), 1000)
    eq(au.gst_on_exclusive_amount(0), 0)
    eq(au.gst_on_exclusive_amount(105), 11, "half-up rounding")


@test
def gst_roundtrip_is_stable():
    for ex in (100, 999, 10000, 143300, 999999):
        inc = ex + au.gst_on_exclusive_amount(ex)
        back = au.gst_on_inclusive_total(inc)
        # Within a cent: the two legal rounding rules can disagree by one.
        true(abs(back - au.gst_on_exclusive_amount(ex)) <= 1,
             f"roundtrip drifted on {ex}")


@test
def tolerance_is_generous_enough_for_legal_rounding():
    # Too tight a tolerance floods the queue with exceptions until humans
    # start approving without looking. Assert the slack actually exists.
    true(au.TOTAL_TOLERANCE_CENTS >= 5)
    true(au.LINE_TOLERANCE_CENTS >= 2)
    true(au.inclusive_total_is_consistent(11000, 1000))
    true(au.inclusive_total_is_consistent(11000, 1004), "4c drift allowed")
    false(au.inclusive_total_is_consistent(11000, 1020), "20c is a real error")


@test
def totals_consistency():
    true(au.totals_are_consistent(10000, 1000, 11000))
    true(au.totals_are_consistent(10000, 1000, 11003), "3c rounding")
    false(au.totals_are_consistent(10000, 1000, 12000))


@test
def negative_amounts_rejected():
    raises(ValueError, au.gst_on_inclusive_total, -100)
    raises(ValueError, au.gst_on_exclusive_amount, -100)


# --- withholding -----------------------------------------------------------

@test
def no_abn_withholding_threshold():
    eq(au.no_abn_withholding_cents(7500), 0, "at the threshold: nothing")
    eq(au.no_abn_withholding_cents(7000), 0, "below: nothing")
    eq(au.no_abn_withholding_cents(10000), 4700, "$100 -> $47 at 47%")
    eq(au.no_abn_withholding_cents(100000), 47000)


# --- document markers ------------------------------------------------------

@test
def detects_tax_invoice_words():
    true(au.looks_like_tax_invoice("TAX INVOICE"))
    true(au.looks_like_tax_invoice("Please find your Tax  Invoice attached"))
    false(au.looks_like_tax_invoice("Invoice"))
    false(au.looks_like_tax_invoice(""))


@test
def detects_payer_written_invoice():
    true(au.looks_like_rcti("RECIPIENT CREATED TAX INVOICE"))
    true(au.looks_like_rcti("this RCTI covers"))
    false(au.looks_like_rcti("TAX INVOICE"))


@test
def detects_credit_note():
    true(au.looks_like_credit_note("ADJUSTMENT NOTE"))
    true(au.looks_like_credit_note("Credit Note 123"))
    false(au.looks_like_credit_note("TAX INVOICE"))


# --- money -----------------------------------------------------------------

@test
def money_is_integer_cents():
    eq(to_cents("1,068.10"), 106810)
    eq(to_cents("$1,068.10"), 106810)
    eq(to_cents("1068.1"), 106810)
    eq(to_cents("1068"), 106800)
    eq(to_cents(632.80), 63280, "the float that demonstrably drifts")
    eq(to_cents("-50.00"), -5000)
    eq(to_cents("(50.00)"), -5000, "accounting negative")


@test
def money_rejects_rubbish():
    raises(ValueError, to_cents, "")
    raises(ValueError, to_cents, None)
    raises(ValueError, to_cents, "abc")
    raises(ValueError, to_cents, "1.2.3")
    raises(ValueError, to_cents, True)


@test
def money_renders_back():
    eq(from_cents(106810), "1068.10")
    eq(from_cents(0), "0.00")
    eq(from_cents(5), "0.05")
    eq(from_cents(-5000), "-50.00")
    raises(TypeError, from_cents, 1.5)


@test
def no_float_drift_across_many_values():
    # 632.80 -> 63279.99999999999 was the real observed failure.
    for text in ("632.80", "1068.10", "0.07", "99999.99", "143.00"):
        cents = to_cents(text)
        eq(from_cents(cents), text if "." in text else text + ".00")


if __name__ == "__main__":
    main("test_au")
