"""Read-only scheduled folder adapter.

The runner assesses files already present in an inbox.  Its only writes are
the configured Bill Guard ledger and digest; it never pays, approves, moves,
renames, or deletes an input document.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import assess
from .cli import _EXIT, _remember, document_from_dict
from .intake import from_file
from .ledger import Ledger


def run_folder(input_dir: str | Path, *, ledger_path: str | Path,
               output_path: str | Path,
               ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    """Assess every regular file in *input_dir* and write a JSON digest.

    Unreadable documents become error entries and do not prevent the remaining
    files from being assessed.  Inputs are never modified.
    """
    inbox = Path(input_dir)
    ledger_file = Path(ledger_path)
    output_file = Path(output_path)
    if not inbox.is_dir():
        raise ValueError(f"scheduled input is not a directory: {inbox}")
    if ledger_file.resolve() == output_file.resolve():
        raise ValueError("scheduled ledger and digest must be different files")
    for target, label in ((ledger_file, "ledger"), (output_file, "digest")):
        if target.exists() and not target.is_file():
            raise ValueError(f"scheduled {label} is not a file: {target}")

    excluded = {ledger_file.resolve(), output_file.resolve()}
    inputs = sorted(
        (path for path in inbox.iterdir()
         if path.is_file() and path.resolve() not in excluded),
        key=lambda path: path.name,
    )
    entries: list[dict[str, Any]] = []
    with Ledger(ledger_file) as ledger:
        for path in inputs:
            try:
                document, gaps = _read_document(path)
                verdict = assess(document, ledger, ctx or {})
                _remember(ledger, document, verdict)
                entries.append({
                    "file": path.name,
                    "ok": True,
                    "exit_code": _EXIT[verdict.outcome],
                    "doc_id": document.doc_id,
                    "read_problems": gaps,
                    "verdict": verdict.to_dict(),
                })
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                entries.append({
                    "file": path.name,
                    "ok": False,
                    "exit_code": 3,
                    "error": str(exc),
                })

    digest = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_directory": str(inbox),
        "documents": entries,
        "summary": {
            "processed": len(entries),
            "assessed": sum(1 for item in entries if item["ok"]),
            "errors": sum(1 for item in entries if not item["ok"]),
        },
        "actions_taken": [],
    }
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(digest, indent=2) + "\n", encoding="utf-8")
    return digest


def _read_document(path: Path):
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        return document_from_dict(data), []
    extraction = from_file(path)
    if not extraction.ok and not extraction.document.artifacts.get("qr_codes"):
        detail = "; ".join(extraction.gaps) or "nothing could be read"
        raise ValueError(detail)
    if extraction.ok and not extraction.looks_like_an_invoice:
        raise ValueError("file does not look like an invoice or bill")
    return extraction.document, extraction.gaps
