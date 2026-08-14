"""Keep every shell command advertised in README executable."""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

from harness import eq, test, true


ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"


def _documented_commands() -> list[str]:
    text = README.read_text(encoding="utf-8")
    blocks = re.findall(r"```bash\n(.*?)```", text, flags=re.DOTALL)
    return [line.strip() for block in blocks for line in block.splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


@test
def every_documented_shell_command_runs():
    commands = _documented_commands()
    true(commands, "README must contain exercised repository commands")
    expected = {
        "--help": {0},
        "capabilities": {0},
        "check": {1},  # the fixture deliberately queries missing history
        "read": {0},
        "scan-codes": {0, 3},  # optional decoder or explicit capability gap
    }
    seen = set()
    for command in commands:
        argv = shlex.split(command)
        eq(argv[:2], ["python3", "scripts/billguard.py"],
           f"README command must be repository-relative: {command}")
        key = argv[2]
        true(key in expected, f"add an expected result for README command: {key}")
        result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                                timeout=30, check=False)
        true(result.returncode in expected[key],
             f"README command failed: {command}\n{result.stderr}")
        true(result.stdout.strip() or result.stderr.strip(),
             f"README command produced no useful output: {command}")
        seen.add(key)
    eq(seen, set(expected), "README command coverage drifted")


if __name__ == "__main__":
    from harness import main
    main(__name__)
