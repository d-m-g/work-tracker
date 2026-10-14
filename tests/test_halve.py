"""Halving a day: the server keeps the list, the browser applies it."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tracker.storage import Storage
from tracker.tracker import InvalidTaskError
from web.api import build_sessions_payload, run_command


class TestHalve(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.storage = Storage(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_mark_and_unmark(self) -> None:
        self.assertEqual(build_sessions_payload(self.storage)["halved"], [])
        run_command(self.storage, "halve", {"day": "2026-10-14", "halved": True})
        run_command(self.storage, "halve", {"day": "2026-10-01"})
        self.assertEqual(build_sessions_payload(self.storage)["halved"], ["2026-10-01", "2026-10-14"])
        result = run_command(self.storage, "halve", {"day": "2026-10-14", "halved": False})
        self.assertEqual(result["halved"], ["2026-10-01"])

    def test_refuses_anything_but_one_spelling_of_a_day(self) -> None:
        for day in (None, 7, "", "20261014", "2026-W42-3", "2026-13-01", "../x"):
            with self.subTest(day=day), self.assertRaises(InvalidTaskError):
                run_command(self.storage, "halve", {"day": day})


if __name__ == "__main__":
    unittest.main()
