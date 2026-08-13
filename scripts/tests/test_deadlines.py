"""Deterministic tests for family-I timing reminders."""

from harness import eq, test, true, main
from billguard.checks import run_all
from billguard.model import Channel, Document, Status


def _deadline(due_date, as_at="2026-08-14"):
    doc = Document(
        doc_id="deadline-test",
        channel=Channel.UPLOAD,
        due_date=due_date,
        balance_due_cents=100,
    )
    return next(
        result for result in run_all(doc, None, {"as_of": as_at})
        if result.check_id == "I01"
    )


def _claim_deadline(state, served_date="2026-08-14", as_at="2026-08-20",
                    holidays=None):
    doc = Document(doc_id="claim-deadline", channel=Channel.UPLOAD)
    doc.artifacts["payment_claim"] = {
        "state": state,
        "served_date": served_date,
    }
    ctx = {"as_of": as_at}
    if holidays is not None:
        ctx["public_holidays"] = {state: holidays}
    return next(result for result in run_all(doc, None, ctx)
                if result.check_id == "I02")


@test
def upcoming_due_today_and_overdue_are_classified_from_injected_date():
    upcoming = _deadline("2026-08-20")
    today = _deadline("2026-08-14")
    overdue = _deadline("2026-08-10")

    eq((upcoming.status, upcoming.detail["timing_state"],
        upcoming.detail["days_until_due"]),
       (Status.PASS, "upcoming", 6))
    eq((today.status, today.detail["timing_state"],
        today.detail["days_until_due"]),
       (Status.PASS, "due_today", 0))
    eq((overdue.status, overdue.detail["timing_state"],
        overdue.detail["days_until_due"]),
       (Status.FAIL, "overdue", -4))
    true("due today" in today.evidence)
    true("overdue" in overdue.evidence)


@test
def missing_or_invalid_dates_are_unknown_instead_of_guessed():
    missing = _deadline(None)
    invalid_due = _deadline("14/08/2026")
    invalid_as_at = _deadline("2026-08-20", "not-a-date")

    eq(missing.status, Status.UNKNOWN)
    eq(invalid_due.status, Status.UNKNOWN)
    eq(invalid_as_at.status, Status.UNKNOWN)
    true("no due date" in missing.evidence)
    true("not valid ISO 8601" in invalid_due.evidence)
    true("not valid ISO 8601" in invalid_as_at.evidence)


@test
def resolved_deadlines_are_explicitly_non_advisory_and_source_dated():
    for due_date in ("2026-08-20", "2026-08-14", "2026-08-10"):
        result = _deadline(due_date)
        true("not legal advice" in result.evidence)
        eq(result.detail["as_at"], "2026-08-14")
        eq(result.detail["due_date"], due_date)


@test
def payment_claim_reply_deadline_uses_each_states_business_day_rule():
    nsw = _claim_deadline("NSW")
    qld = _claim_deadline("QLD")

    eq((nsw.status, nsw.detail["deadline_date"], nsw.detail["business_days"]),
       (Status.PASS, "2026-08-28", 10))
    eq((qld.status, qld.detail["deadline_date"], qld.detail["business_days"]),
       (Status.PASS, "2026-09-04", 15))
    true("Security of Payment Act 1999, s 14(4)" in nsw.evidence)
    true("Building Industry Fairness" in qld.evidence)
    true("s 76(1)" in qld.evidence)
    true("earlier period" in nsw.evidence)
    true("not legal advice" in qld.evidence)


@test
def payment_claim_deadline_skips_supplied_holidays_and_can_be_overdue():
    result = _claim_deadline("NSW", as_at="2026-09-01",
                             holidays=["2026-08-17"])

    eq(result.status, Status.FAIL)
    eq(result.detail["deadline_date"], "2026-08-31")
    eq(result.detail["timing_state"], "overdue")


@test
def state_specific_year_end_blackouts_are_not_business_days():
    holidays = ["2026-12-25", "2027-01-01"]
    nsw = _claim_deadline("NSW", served_date="2026-12-21",
                          as_at="2026-12-22", holidays=holidays)
    qld = _claim_deadline("QLD", served_date="2026-12-21",
                          as_at="2026-12-22", holidays=holidays)

    eq(nsw.detail["deadline_date"], "2027-01-12")
    eq(qld.detail["deadline_date"], "2027-01-29")


@test
def incomplete_or_unsupported_payment_claim_rules_are_unknown():
    missing_date = _claim_deadline("NSW", served_date=None)
    unsupported = _claim_deadline("VIC")

    eq(missing_date.status, Status.UNKNOWN)
    eq(unsupported.status, Status.UNKNOWN)
    true("not valid ISO 8601" in missing_date.evidence)
    true("no payment-schedule rule" in unsupported.evidence)


if __name__ == "__main__":
    main()
