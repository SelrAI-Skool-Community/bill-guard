"""Prove the zero-install package and CLI work outside the checkout."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from harness import eq, test, true


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
CLI = SCRIPTS / "billguard.py"


def _clean_environment() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    return env


@test
def import_and_cli_work_from_a_clean_directory_without_installing():
    with tempfile.TemporaryDirectory() as clean_dir:
        env = _clean_environment()
        env["PYTHONPATH"] = str(SCRIPTS)
        imported = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import json, billguard; "
                    "print(json.dumps({'version': billguard.__version__, "
                    "'module': billguard.__file__}))"
                ),
            ],
            cwd=clean_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        eq(imported.returncode, 0, imported.stderr)
        details = json.loads(imported.stdout)
        eq(details["version"], "0.1.0")
        true(Path(details["module"]).resolve().is_relative_to(SCRIPTS.resolve()))

        cli = subprocess.run(
            [sys.executable, str(CLI), "--help"],
            cwd=clean_dir,
            env=_clean_environment(),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        eq(cli.returncode, 0, cli.stderr)
        true("check" in cli.stdout and "scheduled-run" in cli.stdout,
             "CLI help did not expose the expected commands")


if __name__ == "__main__":
    from harness import main
    main(__name__)
