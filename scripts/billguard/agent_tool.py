"""Agent-facing JSON adapter for Bill Guard.

The adapter is deliberately small: it accepts the same versioned document
JSON as the CLI, calls the public library assessment function, and returns a
JSON-serialisable result.  It performs no I/O and takes no action on a bill.
"""

from __future__ import annotations

import json
from typing import Any

from . import assess
from .cli import EXIT_ERROR, _EXIT, document_from_dict


def check_json(document_json: str | bytes | bytearray | dict[str, Any],
               *, ledger=None, ctx: dict | None = None) -> dict[str, Any]:
    """Assess one prepared document and return an agent-friendly JSON value.

    ``exit_code`` has exactly the meaning of the CLI process status.  Invalid
    JSON or an invalid document contract is reported as an error value instead
    of escaping as an exception across an agent-tool boundary.
    """
    try:
        if isinstance(document_json, (str, bytes, bytearray)):
            data = json.loads(document_json)
        elif isinstance(document_json, dict):
            data = document_json
        else:
            raise ValueError("document JSON must be an object or JSON text")
        document = document_from_dict(data)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError) as exc:
        return {
            "ok": False,
            "exit_code": EXIT_ERROR,
            "error": f"That JSON could not be read as an invoice: {exc}",
        }

    verdict = assess(document, ledger, ctx or {})
    return {
        "ok": True,
        "exit_code": _EXIT[verdict.outcome],
        "verdict": verdict.to_dict(),
    }
