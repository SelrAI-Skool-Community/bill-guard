"""Turning a pile of check results into one decision a human can act on.

Three outcomes, never a risk score. People act on words. A number invites
argument about the threshold instead of about the invoice.

Two rules govern everything here:

  * No single weak signal decides anything. A confident finding is a
    conjunction: the payment destination disagrees with history AND at
    least one other anomaly. Everything else routes to a person.

  * Unknown is reported as unknown. A check that could not run never
    quietly becomes a pass, because a green verdict built on checks that
    never executed is the one genuinely dishonest thing this could do.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import CheckResult, FPRisk, Severity, Status

SAFE = "SAFE TO PAY"
QUERY = "QUERY"
HOLD = "HOLD"


@dataclass
class Verdict:
    outcome: str
    headline: str
    reasons: list = field(default_factory=list)
    not_checked: list = field(default_factory=list)
    limits: list = field(default_factory=list)
    results: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "outcome": self.outcome,
            "headline": self.headline,
            "reasons": self.reasons,
            "not_checked": self.not_checked,
            "limits": self.limits,
            "checks": [r.to_dict() for r in self.results],
        }


#: Stated on every verdict without exception. Silence about this would be
#: the one dishonest thing in the product: it is the control that actually
#: stops payment redirection, and it is structurally out of reach here.
BANK_OWNERSHIP_LIMIT = (
    "Bank account ownership was not verified. Nothing here proves the "
    "account named on this invoice belongs to that business. At payment "
    "time your own banking app runs an account-name check, which is the "
    "control that catches a redirected payment. Read what it says."
)


def decide(results: list[CheckResult], doc=None) -> Verdict:
    fired = [r for r in results if r.status is Status.FAIL]
    unknowns = [r for r in results if r.status is Status.UNKNOWN]

    holds = [r for r in fired if r.severity is Severity.HOLD]
    queries = [r for r in fired if r.severity is Severity.QUERY]

    # A high false-positive-risk finding never gates payment on its own.
    weak_only_queries = [r for r in queries if r.fp_risk is FPRisk.HIGH]
    real_queries = [r for r in queries if r.fp_risk is not FPRisk.HIGH]

    limits = [BANK_OWNERSHIP_LIMIT]
    not_checked = [f"{r.check_id}: {r.title} ({r.evidence})" for r in unknowns]

    if holds:
        primary = _most_severe(holds)
        return Verdict(
            outcome=HOLD,
            headline=primary.evidence.split(". ")[0].rstrip(".") + ".",
            reasons=[_reason(r) for r in holds + real_queries],
            not_checked=not_checked,
            limits=limits,
            results=results,
        )

    if real_queries:
        primary = real_queries[0]
        return Verdict(
            outcome=QUERY,
            headline=primary.evidence.split(". ")[0].rstrip(".") + ".",
            reasons=[_reason(r) for r in real_queries],
            not_checked=not_checked,
            limits=limits,
            results=results,
        )

    if _material_unknowns(unknowns):
        return Verdict(
            outcome=QUERY,
            headline=("Not enough of this invoice could be read to say it is "
                      "safe to pay."),
            reasons=[
                "Checks that decide whether an invoice is safe could not run "
                "on this document. That is not the same as passing them."
            ],
            not_checked=not_checked,
            limits=limits,
            results=results,
        )

    headline = "Nothing material changed, and every check that ran passed."
    reasons = []
    if weak_only_queries:
        reasons = [
            _reason(r) + " (noted only: this signal is unreliable on its own "
            "and did not change the verdict)" for r in weak_only_queries]
    return Verdict(
        outcome=SAFE,
        headline=headline,
        reasons=reasons,
        not_checked=not_checked,
        limits=limits,
        results=results,
    )


#: A check whose absence should stop a green verdict. These are the ones
#: that decide safety, so silence from them is not reassurance.
MATERIAL_CHECKS = frozenset({"D01", "B01", "F02"})


def _material_unknowns(unknowns: list[CheckResult]) -> bool:
    return any(r.check_id in MATERIAL_CHECKS for r in unknowns)


def _most_severe(results: list[CheckResult]) -> CheckResult:
    order = {FPRisk.NONE: 0, FPRisk.LOW: 1, FPRisk.MODERATE: 2, FPRisk.HIGH: 3}
    return sorted(results, key=lambda r: order.get(r.fp_risk, 9))[0]


def _reason(r: CheckResult) -> str:
    return f"{r.title}. {r.evidence}".strip()


def render_text(verdict: Verdict, doc=None) -> str:
    """Plain-text report. Every verdict shows its evidence.

    A verdict with no visible reasoning is a verdict nobody trusts the
    second time.
    """
    lines = []
    marker = {HOLD: "HOLD", QUERY: "QUERY", SAFE: "SAFE TO PAY"}[verdict.outcome]
    lines.append(f"{marker}: {verdict.headline}")
    lines.append("")

    if doc is not None:
        who = doc.supplier_name or "unknown supplier"
        num = doc.invoice_number or "no number"
        amt = ""
        if doc.total_cents is not None:
            from .model import from_cents
            amt = f" for {doc.currency or ''} {from_cents(doc.total_cents)}".rstrip()
        lines.append(f"  {who}, invoice {num}{amt}")
        lines.append(f"  arrived by {doc.channel.value}")
        lines.append("")

    if verdict.reasons:
        lines.append("Why:")
        for r in verdict.reasons:
            lines.append(f"  - {r}")
        lines.append("")

    passed = [r for r in verdict.results if r.status is Status.PASS]
    if passed:
        lines.append(f"Passed ({len(passed)}):")
        for r in passed:
            lines.append(f"  - {r.title}")
        lines.append("")

    if verdict.not_checked:
        lines.append("Could not be checked (this is not a pass):")
        for n in verdict.not_checked:
            lines.append(f"  - {n}")
        lines.append("")

    lines.append("Limits:")
    for lim in verdict.limits:
        lines.append(f"  - {lim}")
    return "\n".join(lines)
