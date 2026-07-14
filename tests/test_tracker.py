"""Tests for the service layer: the state machine and its arithmetic."""

from __future__ import annotations

import unittest

from support import TrackerTestCase

from tracker.models import SessionState
from tracker.tracker import (
    NoActiveSessionError,
    SessionAlreadyRunningError,
    ToggleAction,
    WrongStateError,
)
from tracker.utils import CorruptJSONError


class TestStart(TrackerTestCase):
    def test_start_creates_the_current_file(self) -> None:
        session = self.tracker.start()
        self.assertEqual("2026-07-14_19-42-18", session.id)
        self.assertTrue(self.storage.has_current())

    def test_start_fails_when_a_session_is_already_running(self) -> None:
        self.tracker.start()
        self.clock.advance(60)
        with self.assertRaises(SessionAlreadyRunningError):
            self.tracker.start()

    def test_start_fails_even_when_the_existing_session_is_paused(self) -> None:
        self.tracker.start()
        self.clock.advance(60)
        self.tracker.pause()
        with self.assertRaises(SessionAlreadyRunningError):
            self.tracker.start()


class TestPauseResume(TrackerTestCase):
    def test_pause_requires_an_active_session(self) -> None:
        with self.assertRaises(NoActiveSessionError):
            self.tracker.pause()

    def test_pause_fails_when_already_paused(self) -> None:
        self.tracker.start()
        self.tracker.pause()
        with self.assertRaises(WrongStateError):
            self.tracker.pause()

    def test_resume_requires_an_active_session(self) -> None:
        with self.assertRaises(NoActiveSessionError):
            self.tracker.resume()

    def test_resume_fails_when_running(self) -> None:
        self.tracker.start()
        with self.assertRaises(WrongStateError):
            self.tracker.resume()

    def test_resume_appends_the_completed_pause_and_clears_pause_start(self) -> None:
        self.tracker.start()
        self.clock.advance(600)
        self.tracker.pause()
        self.clock.advance(325)
        session, pause = self.tracker.resume()

        self.assertEqual(325, pause.seconds)
        self.assertIs(SessionState.RUNNING, session.state)
        self.assertIsNone(session.pause_start)
        self.assertEqual([pause], session.pauses)

        # And the transition was persisted, not just held in memory.
        reloaded = self.storage.load_current()
        assert reloaded is not None
        self.assertEqual(session, reloaded)

    def test_pauses_accumulate_across_several_cycles(self) -> None:
        self.tracker.start()
        for pause_length in (60, 120, 180):
            self.clock.advance(300)
            self.tracker.pause()
            self.clock.advance(pause_length)
            self.tracker.resume()

        status = self.tracker.status()
        self.assertEqual(3, status.pause_count)
        self.assertEqual(360, status.paused_seconds)


class TestToggle(TrackerTestCase):
    def test_toggle_when_idle_starts_a_session(self) -> None:
        result = self.tracker.toggle()

        self.assertIs(ToggleAction.STARTED, result.action)
        self.assertIsNone(result.pause)
        self.assertIs(SessionState.RUNNING, result.session.state)
        self.assertTrue(self.storage.has_current())

    def test_toggle_when_running_pauses(self) -> None:
        self.tracker.start()
        self.clock.advance(600)
        result = self.tracker.toggle()

        self.assertIs(ToggleAction.PAUSED, result.action)
        self.assertIsNone(result.pause)
        self.assertIs(SessionState.PAUSED, result.session.state)
        self.assertEqual(self.clock.current, result.session.pause_start)

    def test_toggle_when_paused_resumes_and_reports_the_closed_pause(self) -> None:
        self.tracker.start()
        self.clock.advance(600)
        self.tracker.pause()
        self.clock.advance(325)
        result = self.tracker.toggle()

        self.assertIs(ToggleAction.RESUMED, result.action)
        assert result.pause is not None
        self.assertEqual(325, result.pause.seconds)
        self.assertIs(SessionState.RUNNING, result.session.state)
        self.assertIsNone(result.session.pause_start)

    def test_toggle_never_stops_a_session(self) -> None:
        """Whatever the state, toggling leaves a session in progress."""
        self.tracker.toggle()
        for _ in range(6):
            self.clock.advance(300)
            self.tracker.toggle()

        self.assertTrue(self.storage.has_current())
        self.assertEqual([], self.storage.list_sessions())

    def test_toggling_is_persisted_between_calls(self) -> None:
        """Each toggle reads the state back off disk, so a run of them alternates."""
        self.tracker.toggle()  # started
        actions = []
        for _ in range(4):
            self.clock.advance(60)
            actions.append(self.tracker.toggle().action)

        self.assertEqual(
            [
                ToggleAction.PAUSED,
                ToggleAction.RESUMED,
                ToggleAction.PAUSED,
                ToggleAction.RESUMED,
            ],
            actions,
        )

    def test_toggling_through_a_day_keeps_the_arithmetic_straight(self) -> None:
        self.tracker.toggle()  # start
        self.clock.advance(3600)
        self.tracker.toggle()  # pause after an hour of work
        self.clock.advance(900)
        self.tracker.toggle()  # resume after fifteen minutes away
        self.clock.advance(1800)

        status = self.tracker.status()
        self.assertEqual(3600 + 1800, status.worked_seconds)
        self.assertEqual(900, status.paused_seconds)
        self.assertEqual(1, status.pause_count)

    def test_toggle_reports_a_corrupt_current_file(self) -> None:
        self.storage.current_path.write_text("{ oops", encoding="utf-8")
        with self.assertRaises(CorruptJSONError):
            self.tracker.toggle()


class TestStop(TrackerTestCase):
    def test_stop_requires_an_active_session(self) -> None:
        with self.assertRaises(NoActiveSessionError):
            self.tracker.stop()

    def test_stop_computes_the_three_totals(self) -> None:
        self.tracker.start()
        self.clock.advance(600)  # 600s worked
        self.tracker.pause()
        self.clock.advance(325)  # 325s paused
        self.tracker.resume()
        self.clock.advance(1200)  # 1200s worked
        completed, _ = self.tracker.stop()

        self.assertEqual(2125, completed.gross_seconds)
        self.assertEqual(325, completed.paused_seconds)
        self.assertEqual(1800, completed.worked_seconds)
        self.assertEqual(1, len(completed.pauses))

    def test_stop_while_paused_closes_the_open_pause(self) -> None:
        self.tracker.start()
        self.clock.advance(600)
        self.tracker.pause()
        self.clock.advance(300)  # still paused when we stop
        completed, _ = self.tracker.stop()

        self.assertEqual(900, completed.gross_seconds)
        self.assertEqual(300, completed.paused_seconds)
        self.assertEqual(600, completed.worked_seconds)
        self.assertEqual(1, len(completed.pauses))
        self.assertEqual(300, completed.pauses[0].seconds)

    def test_stop_archives_the_session_and_removes_the_current_file(self) -> None:
        self.tracker.start()
        self.clock.advance(3600)
        completed, path = self.tracker.stop()

        self.assertFalse(self.storage.has_current())
        self.assertTrue(path.is_file())
        self.assertEqual(completed, self.storage.load_session(path))

    def test_a_session_can_be_started_again_after_stopping(self) -> None:
        self.tracker.start()
        self.clock.advance(60)
        self.tracker.stop()

        self.clock.advance(60)
        session = self.tracker.start()
        self.assertEqual("2026-07-14_19-44-18", session.id)
        self.assertEqual(1, len(self.storage.list_sessions()))

    def test_a_zero_length_session_is_valid(self) -> None:
        self.tracker.start()
        completed, _ = self.tracker.stop()
        self.assertEqual(0, completed.gross_seconds)
        self.assertEqual(0, completed.worked_seconds)


class TestStatus(TrackerTestCase):
    def test_status_is_idle_with_no_session(self) -> None:
        status = self.tracker.status()
        self.assertFalse(status.is_active)
        self.assertIsNone(status.state)
        self.assertEqual(0, status.worked_seconds)
        self.assertEqual(0, status.paused_seconds)
        self.assertEqual(0, status.pause_count)

    def test_status_reports_live_worked_time_while_running(self) -> None:
        self.tracker.start()
        self.clock.advance(1800)

        status = self.tracker.status()
        self.assertIs(SessionState.RUNNING, status.state)
        self.assertEqual("2026-07-14_19-42-18", status.session_id)
        self.assertEqual(1800, status.worked_seconds)
        self.assertEqual(0, status.paused_seconds)

    def test_worked_time_stands_still_while_paused(self) -> None:
        self.tracker.start()
        self.clock.advance(600)
        self.tracker.pause()

        self.clock.advance(900)
        status = self.tracker.status()
        self.assertIs(SessionState.PAUSED, status.state)
        self.assertEqual(600, status.worked_seconds)  # frozen
        self.assertEqual(900, status.paused_seconds)  # growing

    def test_status_reports_a_corrupt_file_rather_than_pretending_to_be_idle(self) -> None:
        self.storage.current_path.write_text("not json", encoding="utf-8")
        with self.assertRaises(CorruptJSONError):
            self.tracker.status()


class TestFullDayScenario(TrackerTestCase):
    def test_a_realistic_working_day_adds_up(self) -> None:
        """Four hours in, a lunch break, four hours out."""
        self.tracker.start()
        self.clock.advance(4 * 3600)
        self.tracker.pause()
        self.clock.advance(45 * 60)  # lunch
        self.tracker.resume()
        self.clock.advance(4 * 3600)
        completed, _ = self.tracker.stop()

        self.assertEqual(8 * 3600 + 45 * 60, completed.gross_seconds)
        self.assertEqual(45 * 60, completed.paused_seconds)
        self.assertEqual(8 * 3600, completed.worked_seconds)
        self.assertEqual(
            completed.gross_seconds,
            completed.worked_seconds + completed.paused_seconds,
            "gross must always be worked + paused",
        )


if __name__ == "__main__":
    unittest.main()
