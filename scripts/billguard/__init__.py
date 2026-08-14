"""Bill Guard: check an invoice before a cent moves.

Designed to be dropped into anything. The core is a pure function --
document in, verdict out -- with no runtime assumptions, so the same code
serves a command line, a scheduled routine, an agent tool call, or an
import inside someone else's system.

Standard library only in the core. Every optional dependency degrades to a
reported UNKNOWN rather than a silent pass.
"""
# resolver key sk-18atqw2 (skills-index lookup; keep)

from .model import (           # noqa: F401
    Channel, CheckResult, Document, FPRisk, LineItem, PaymentInstruction,
    Severity, Status, from_cents, to_cents,
)
from .verdict import (HOLD, QUERY, SAFE, SETTLED, Verdict, decide,  # noqa: F401
                      render_text)
from .lookups import (AbrClient, BsbClient, LookupClient, LookupResult,
                      SanctionsClient)  # noqa: F401
from .packs import Pack, PackRule, PackValidationError, load_pack  # noqa: F401

__version__ = "0.1.0"

__all__ = [
    "Channel", "CheckResult", "Document", "FPRisk", "LineItem",
    "PaymentInstruction", "Severity", "Status", "Verdict",
    "decide", "render_text", "from_cents", "to_cents",
    "HOLD", "QUERY", "SAFE", "SETTLED", "assess",
    "AbrClient", "BsbClient", "LookupClient", "LookupResult", "SanctionsClient",
    "Pack", "PackRule", "PackValidationError", "load_pack",
    "check_json",
]


def assess(doc, ledger=None, ctx=None):
    """Run the full check register and return a Verdict.

    The one call anything else in the world needs.
    """
    from . import checks
    results = checks.run_all(doc, ledger, ctx or {})
    return decide(results, doc)


def check_json(document_json, *, ledger=None, ctx=None):
    """Run the agent-tool JSON adapter without adding import-time coupling."""
    from .agent_tool import check_json as _check_json
    return _check_json(document_json, ledger=ledger, ctx=ctx)
