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


if __name__ == "__main__":
    main()
