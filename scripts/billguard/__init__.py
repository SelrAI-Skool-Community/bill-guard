"""Bill Guard: check an invoice before a cent moves.

Designed to be dropped into anything. The core is a pure function --
document in, verdict out -- with no runtime assumptions, so the same code
serves a command line, a scheduled routine, an agent tool call, or an
import inside someone else's system.

Standard library only in the core. Every optional dependency degrades to a
reported UNKNOWN rather than a silent pass.
"""

from .model import (           # noqa: F401
    Channel, CheckResult, Document, FPRisk, LineItem, PaymentInstruction,
    Severity, Status, from_cents, to_cents,
)
from .verdict import HOLD, QUERY, SAFE, Verdict, decide, render_text  # noqa: F401

__version__ = "0.1.0"

__all__ = [
    "Channel", "CheckResult", "Document", "FPRisk", "LineItem",
    "PaymentInstruction", "Severity", "Status", "Verdict",
    "decide", "render_text", "from_cents", "to_cents",
    "HOLD", "QUERY", "SAFE", "assess",
]


def assess(doc, ledger=None, ctx=None):
    """Run the full check register and return a Verdict.

    The one call anything else in the world needs.
    """
    from . import checks
    results = checks.run_all(doc, ledger, ctx or {})
    return decide(results, doc)
