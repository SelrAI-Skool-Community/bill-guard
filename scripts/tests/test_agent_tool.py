"""Equivalence contract for the library, CLI, and agent-tool surfaces."""

import contextlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

from harness import eq, test, true
from billguard import assess, check_json
from billguard import cli


FIXTURE = Path(__file__).parents[2] / "examples" / "invoice-clean.json"


@test
def library_cli_and_agent_tool_return_the_same_verdict_and_exit_semantics():
    source = FIXTURE.read_text(encoding="utf-8")
    document = cli.document_from_dict(json.loads(source))
    library_verdict = assess(document).to_dict()

    agent_result = check_json(source)

    output = io.StringIO()
    args = SimpleNamespace(document=str(FIXTURE), ledger=None,
                           remember=False, json=True)
    with contextlib.redirect_stdout(output):
        cli_exit = cli.cmd_check(args)
    cli_result = json.loads(output.getvalue())
    cli_verdict = {key: cli_result[key] for key in library_verdict}

    true(agent_result["ok"])
    eq(agent_result["verdict"], library_verdict)
    eq(cli_verdict, library_verdict)
    eq(agent_result["exit_code"], cli_exit)
    eq(cli_exit, cli._EXIT[library_verdict["outcome"]])


@test
def invalid_agent_input_has_the_same_error_exit_semantics_as_the_cli():
    result = check_json("not JSON")
    eq(result["ok"], False)
    eq(result["exit_code"], cli.EXIT_ERROR)
    true("could not be read" in result["error"])


@test
def agent_tool_accepts_an_object_without_mutating_it():
    source = json.loads(FIXTURE.read_text(encoding="utf-8"))
    before = json.loads(json.dumps(source))
    result = check_json(source)
    true(result["ok"])
    eq(source, before)


if __name__ == "__main__":
    from harness import main
    main(__name__)
