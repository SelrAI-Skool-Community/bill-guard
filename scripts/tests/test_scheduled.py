"""Scheduled adapter: deterministic digest and a tightly bounded write set."""

import json
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

from harness import eq, raises, test, true
from billguard import cli
from billguard.ledger import Ledger
from billguard.scheduled import run_folder


FIXTURE = Path(__file__).parents[2] / "examples" / "invoice-clean.json"


@test
def scheduled_folder_writes_only_its_ledger_and_digest_and_never_acts():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        inbox = root / "inbox"
        inbox.mkdir()
        shutil.copyfile(FIXTURE, inbox / "invoice.json")
        sentinel = root / "do-not-touch.txt"
        sentinel.write_text("original", encoding="utf-8")
        before_input = (inbox / "invoice.json").read_bytes()
        ledger = root / "state" / "ledger.db"
        output = root / "reports" / "digest.json"

        digest = run_folder(inbox, ledger_path=ledger, output_path=output,
                            ctx={"as_of": "2026-08-14"})

        eq(json.loads(output.read_text(encoding="utf-8")), digest)
        eq(digest["summary"], {"processed": 1, "assessed": 1, "errors": 0})
        eq(digest["actions_taken"], [])
        eq([item["file"] for item in digest["documents"]], ["invoice.json"])
        eq((inbox / "invoice.json").read_bytes(), before_input)
        eq(sentinel.read_text(encoding="utf-8"), "original")
        eq(sorted(path.relative_to(root).as_posix() for path in root.rglob("*")
                  if path.is_file()), [
            "do-not-touch.txt", "inbox/invoice.json",
            "reports/digest.json", "state/ledger.db",
        ])
        with Ledger(ledger) as saved:
            eq(saved.stats()["documents"], 1)


@test
def scheduled_folder_isolates_bad_inputs_and_cli_reports_errors():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        inbox = root / "inbox"
        inbox.mkdir()
        shutil.copyfile(FIXTURE, inbox / "b-good.json")
        (inbox / "a-bad.json").write_text("not json", encoding="utf-8")
        ledger, output = root / "ledger.db", root / "digest.json"

        args = SimpleNamespace(input_dir=str(inbox), ledger=str(ledger),
                               output=str(output))
        eq(cli.cmd_scheduled_run(args), cli.EXIT_ERROR)
        digest = json.loads(output.read_text(encoding="utf-8"))
        eq([item["file"] for item in digest["documents"]],
           ["a-bad.json", "b-good.json"])
        eq(digest["summary"], {"processed": 2, "assessed": 1, "errors": 1})
        true(not digest["documents"][0]["ok"])
        true(digest["documents"][1]["ok"])


@test
def scheduled_folder_refuses_to_overwrite_its_ledger_with_the_digest():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        inbox = root / "inbox"
        inbox.mkdir()
        same_file = root / "result"
        raises(ValueError, run_folder, inbox, ledger_path=same_file,
               output_path=same_file)


if __name__ == "__main__":
    from harness import main
    main(__name__)
