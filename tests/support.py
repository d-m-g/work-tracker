"""Shared test fixtures: a fake clock and a temporary-directory test case."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make the repository root importable when the tests are run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker.storage import Storage  # noqa: E402
from tracker.tracker import WorkTracker  # noqa: E402

#: A fixed, timezone-aware starting point (+03:00, matching the spec's example).
TZ = timezone(timedelta(hours=3))
EPOCH = datetime(2026, 7, 14, 19, 42, 18, tzinfo=TZ)


class FakeClock:
    """A controllable stand-in for :func:`tracker.utils.now`.

    Time only moves when a test moves it, which is what lets the suite assert on
    exact durations instead of sleeping and hoping.
    """

    def __init__(self, start: datetime = EPOCH) -> None:
        self.current = start

    def __call__(self) -> datetime:
        """Return the current fake time (the clock's callable interface)."""
        return self.current

    def advance(self, seconds: int) -> datetime:
        """Move the clock forward and return the new time."""
        self.current += timedelta(seconds=seconds)
        return self.current


class TrackerTestCase(unittest.TestCase):
    """Base case providing a throwaway data directory, storage and tracker."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

        self.root = Path(self._tmp.name)
        self.clock = FakeClock()
        self.storage = Storage(self.root)
        self.tracker = WorkTracker(self.storage, clock=self.clock)
