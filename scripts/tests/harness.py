"""Tiny test harness. Standard library only, no pytest required.

Run everything:   python3 scripts/tests/run.py
Run one file:     python3 scripts/tests/test_au.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

# Make `billguard` importable from anywhere.
_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

_CASES: list = []


def test(fn):
    """Decorator registering a test function."""
    _CASES.append(fn)
    return fn


class Failure(AssertionError):
    pass


def eq(actual, expected, msg=""):
    if actual != expected:
        raise Failure(f"{msg}\n  expected: {expected!r}\n  actual:   {actual!r}")


def ne(actual, unexpected, msg=""):
    if actual == unexpected:
        raise Failure(f"{msg}\n  expected anything but: {unexpected!r}")


def true(cond, msg=""):
    if not cond:
        raise Failure(msg or "expected true")


def false(cond, msg=""):
    if cond:
        raise Failure(msg or "expected false")


def close(actual, expected, tolerance, msg=""):
    if abs(actual - expected) > tolerance:
        raise Failure(f"{msg}\n  expected {expected} +/- {tolerance}, "
                      f"got {actual}")


def raises(exc_type, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc_type:
        return
    except Exception as e:
        raise Failure(f"expected {exc_type.__name__}, got "
                      f"{type(e).__name__}: {e}")
    raise Failure(f"expected {exc_type.__name__}, nothing raised")


def run(module_name: str = "") -> int:
    """Run every registered test. Returns the count of failures."""
    passed = failed = 0
    failures = []
    for fn in _CASES:
        name = f"{fn.__module__}.{fn.__name__}"
        try:
            fn()
            passed += 1
        except Exception as exc:
            failed += 1
            failures.append((name, exc, traceback.format_exc()))

    label = module_name or "tests"
    for name, exc, tb in failures:
        print(f"FAIL {name}")
        print(f"     {exc}")
        if not isinstance(exc, Failure):
            print("     " + tb.replace("\n", "\n     "))
    print(f"{label}: {passed} passed, {failed} failed")
    return failed


def main(module_name: str = ""):
    sys.exit(1 if run(module_name) else 0)
