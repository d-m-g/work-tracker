#!/usr/bin/env python3
"""Executable entry point: ``python3 tracker.py <command>``.

This script is only a launcher. It resolves the ``tracker`` package next to it
and hands over to :func:`tracker.cli.main`.

A note on the name collision: this file and the package are both called
``tracker``. That is safe, because a script run directly is imported under the
name ``__main__`` rather than ``tracker``, and Python's import machinery
resolves a package directory before a same-named module file. So the
``from tracker.cli import ...`` below always finds the package, never this file.

The package directory is pushed onto ``sys.path`` explicitly so the tracker also
works when invoked through a symlink or from a macOS Shortcut, where the working
directory is not the repository.

The package targets Python 3.9 -- the version macOS ships at
``/usr/bin/python3`` -- so the Shortcuts can call the system interpreter and keep
working across Homebrew upgrades and clean machines. It runs unchanged on newer
interpreters too.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: Matches the oldest interpreter the code is written against.
MINIMUM_PYTHON = (3, 9)

if sys.version_info < MINIMUM_PYTHON:  # pragma: no cover - depends on interpreter
    required = ".".join(str(part) for part in MINIMUM_PYTHON)
    running = ".".join(str(part) for part in sys.version_info[:3])
    sys.exit(f"error: work-tracker needs Python {required} or newer, but is running {running}")

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tracker.cli import main  # noqa: E402  (import must follow the sys.path fix)

if __name__ == "__main__":
    sys.exit(main())
