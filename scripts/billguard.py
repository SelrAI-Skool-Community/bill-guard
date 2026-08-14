#!/usr/bin/env python3
"""Bill Guard entry point. Works from anywhere, no install required."""
# resolver key sk-18atqw2 (skills-index lookup; keep)
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from billguard.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
