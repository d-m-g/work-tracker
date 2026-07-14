"""Tests for the web API's payload builders.

These need no socket: the builders are pure functions of a Storage and a clock.
"""

from __future__ import annotations

import unittest

from support import TrackerTestCase

from tracker.utils import CorruptJSONError
from web.api import build_sessions_payload, build_status_payload


class TestStatusPayload(TrackerTestCase):
    def test_idle_when_no_session(self) -> None:
        payload = build_status_payload(self.storage, clock=self.clock)

        self.assertEqual("idle", payload["state"])
        self.assertIsNone(payload["id"])
        self.assertEqual(0, payload["workedSeconds"])
        self.assertFalse(payload["pauseInProgress"])

    def test_running_session(self) -> None:
        self.tracker.start()
        self.clock.advance(1800)

        payload = build_status_payload(self.storage, clock=self.clock)
        self.assertEqual("running", payload["state"])
        self.assertEqual("2026-07-14_19-42-18", payload["id"])
        self.assertEqual("2026-07-14T19:42:18+03:00", payload["start"])
        self.assertEqual(1800, payload["workedSeconds"])
        self.assertEqual(0, payload["pausedSeconds"])
        self.assertFalse(payload["pauseInProgress"])

    def test_paused_session_freezes_worked_and_flags_the_open_pause(self) -> None:
        self.tracker.start()
        self.clock.advance(600)
        self.tracker.pause()
        self.clock.advance(300)

        payload = build_status_payload(self.storage, clock=self.clock)
        self.assertEqual("paused", payload["state"])
        self.assertEqual(600, payload["workedSeconds"])  # frozen
        self.assertEqual(300, payload["pausedSeconds"])  # growing
        self.assertEqual(0, payload["pauseCount"])  # none finished yet
        self.assertTrue(payload["pauseInProgress"])

    def test_gross_equals_worked_plus_paused(self) -> None:
        # The UI derives the session's current end as start + gross, so this
        # identity is what keeps the timeline anchored to the server's clock.
        self.tracker.start()
        self.clock.advance(600)
        self.tracker.pause()
        self.clock.advance(300)
        self.tracker.resume()
        self.clock.advance(120)

        payload = build_status_payload(self.storage, clock=self.clock)
        self.assertEqual(1020, payload["grossSeconds"])
        self.assertEqual(
            payload["grossSeconds"],
            payload["workedSeconds"] + payload["pausedSeconds"],
        )

    def test_finished_pauses_are_sent_for_the_timeline(self) -> None:
        self.tracker.start()
        self.clock.advance(600)
        self.tracker.pause()
        self.clock.advance(325)
        self.tracker.resume()

        payload = build_status_payload(self.storage, clock=self.clock)
        self.assertEqual(1, len(payload["pauses"]))
        self.assertEqual(325, payload["pauses"][0]["seconds"])
        self.assertIsNone(payload["pauseStart"])

    def test_an_open_pause_is_sent_separately_from_the_finished_ones(self) -> None:
        self.tracker.start()
        self.clock.advance(600)
        self.tracker.pause()
        self.clock.advance(60)

        payload = build_status_payload(self.storage, clock=self.clock)
        # The open pause has no end, so it is not in `pauses` -- the UI draws it
        # as a gap running to the live edge.
        self.assertEqual([], payload["pauses"])
        self.assertEqual("2026-07-14T19:52:18+03:00", payload["pauseStart"])

    def test_idle_payload_carries_the_timeline_keys(self) -> None:
        payload = build_status_payload(self.storage, clock=self.clock)
        self.assertEqual([], payload["pauses"])
        self.assertIsNone(payload["pauseStart"])
        self.assertEqual(0, payload["grossSeconds"])

    def test_state_is_a_plain_string_not_an_enum_repr(self) -> None:
        # Guards the 3.11 enum-formatting change: the JSON must say "running",
        # never "SessionState.RUNNING", on every supported interpreter.
        self.tracker.start()
        payload = build_status_payload(self.storage, clock=self.clock)

        self.assertEqual("running", payload["state"])
        self.assertIsInstance(payload["state"], str)

    def test_a_corrupt_current_file_propagates(self) -> None:
        self.storage.current_path.write_text("{ broken", encoding="utf-8")
        with self.assertRaises(CorruptJSONError):
            build_status_payload(self.storage, clock=self.clock)


class TestSessionsPayload(TrackerTestCase):
    def _archive_session(self, worked: int, pause: int = 0) -> None:
        """Record one complete session: `worked` seconds, with an optional pause."""
        self.tracker.start()
        self.clock.advance(worked)
        if pause:
            self.tracker.pause()
            self.clock.advance(pause)
            self.tracker.resume()
        self.tracker.stop()
        self.clock.advance(60)  # so the next session gets a distinct id

    def test_empty_archive(self) -> None:
        payload = build_sessions_payload(self.storage)

        self.assertEqual([], payload["sessions"])
        self.assertEqual([], payload["unreadable"])
        self.assertEqual(0, payload["totals"]["count"])
        self.assertEqual(0, payload["totals"]["workedSeconds"])

    def test_sessions_are_newest_first(self) -> None:
        self._archive_session(worked=100)
        self._archive_session(worked=200)
        self._archive_session(worked=300)

        payload = build_sessions_payload(self.storage)
        worked = [item["workedSeconds"] for item in payload["sessions"]]
        self.assertEqual([300, 200, 100], worked)

    def test_totals_sum_every_session(self) -> None:
        self._archive_session(worked=3600, pause=300)
        self._archive_session(worked=1800, pause=120)

        totals = build_sessions_payload(self.storage)["totals"]
        self.assertEqual(2, totals["count"])
        self.assertEqual(5400, totals["workedSeconds"])
        self.assertEqual(420, totals["pausedSeconds"])

    def test_a_session_carries_its_individual_pauses(self) -> None:
        self._archive_session(worked=600, pause=325)

        session = build_sessions_payload(self.storage)["sessions"][0]
        self.assertEqual(1, len(session["pauses"]))
        self.assertEqual(325, session["pauses"][0]["seconds"])

    def test_one_corrupt_file_does_not_hide_the_good_ones(self) -> None:
        self._archive_session(worked=3600)
        (self.storage.sessions_dir / "2020-01-01_00-00-00.json").write_text(
            "{ truncated", encoding="utf-8"
        )

        payload = build_sessions_payload(self.storage)
        self.assertEqual(1, len(payload["sessions"]))  # the good one survives
        self.assertEqual(1, len(payload["unreadable"]))
        self.assertEqual("2020-01-01_00-00-00.json", payload["unreadable"][0]["file"])
        self.assertEqual(1, payload["totals"]["count"])


if __name__ == "__main__":
    unittest.main()
