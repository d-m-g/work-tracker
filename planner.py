#!/usr/bin/env python3
"""Executable entry point: ``python3 planner.py <command>``. See planner/."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from planner.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
