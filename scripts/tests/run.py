#!/usr/bin/env python3
"""Run every test module. Exit non-zero on any failure."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import harness  # noqa: E402

failures = 0
modules = sorted(p.stem for p in HERE.glob("test_*.py"))
for name in modules:
    harness._CASES.clear()
    __import__(name)
    failures += harness.run(name)

print()
print(f"TOTAL: {len(modules)} modules, {failures} failures")
sys.exit(1 if failures else 0)
