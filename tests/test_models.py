"""Tests for the domain model: serialisation, validation and duration maths."""

from __future__ import annotations

import unittest
from datetime import timedelta

from support import EPOCH

from tracker.models import ActiveSession, CompletedSession, Pause, SessionState, SessionStatus
from tracker.utils import CorruptJSONError


class TestPause(unittest.TestCase):
    def test_seconds_is_derived_from_the_timestamps(self) -> None:
        pause = Pause(start=EPOCH, end=EPOCH + timedelta(seconds=325))
        self.assertEqual(325, pause.seconds)

    def test_serialises_to_the_documented_shape(self) -> None:
        pause = Pause(start=EPOCH, end=EPOCH + timedelta(seconds=325))
        self.assertEqual(
            {
                "start": "2026-07-14T19:42:18+03:00",
                "end": "2026-07-14T19:47:43+03:00",
                "seconds": 325,
            },
            pause.to_dict(),
        )

    def test_round_trips_through_json(self) -> None:
        pause = Pause(start=EPOCH, end=EPOCH + timedelta(seconds=90))
        self.assertEqual(pause, Pause.from_dict(pause.to_dict()))

    def test_rejects_an_end_before_its_start(self) -> None:
        with self.assertRaises(ValueError):
            Pause(start=EPOCH, end=EPOCH - timedelta(seconds=1))

    def test_recomputes_seconds_rather_than_trusting_the_file(self) -> None:
        # The timestamps remain the source of truth even if 'seconds' disagrees.
        pause = Pause.from_dict(
            {
                "start": "2026-07-14T19:42:18+03:00",
                "end": "2026-07-14T19:47:43+03:00",
                "seconds": 999999,
            }
        )
        self.assertEqual(325, pause.seconds)

    def test_rejects_a_malformed_object(self) -> None:
        for bad in ([], {"start": "2026-07-14T19:42:18+03:00"}, {"end": "x"}, "nope"):
            with self.subTest(value=bad):
                with self.assertRaises(CorruptJSONError):
                    Pause.from_dict(bad)


class TestActiveSession(unittest.TestCase):
    def test_begin_derives_the_id_from_the_start_time(self) -> None:
        session = ActiveSession.begin(EPOCH)
        self.assertEqual("2026-07-14_19-42-18", session.id)
        self.assertIs(SessionState.RUNNING, session.state)
        self.assertIsNone(session.pause_start)
        self.assertEqual([], session.pauses)

    def test_serialises_to_the_documented_shape(self) -> None:
        self.assertEqual(
            {
                "state": "running",
                "id": "2026-07-14_19-42-18",
                "start": "2026-07-14T19:42:18+03:00",
                "pauseStart": None,
                "pauses": [],
            },
            ActiveSession.begin(EPOCH).to_dict(),
        )

    def test_pause_then_resume_records_one_pause(self) -> None:
        session = ActiveSession.begin(EPOCH)
        session.pause(EPOCH + timedelta(seconds=600))
        self.assertIs(SessionState.PAUSED, session.state)

        pause = session.resume(EPOCH + timedelta(seconds=925))
        self.assertIs(SessionState.RUNNING, session.state)
        self.assertIsNone(session.pause_start)
        self.assertEqual([pause], session.pauses)
        self.assertEqual(325, pause.seconds)

    def test_pause_rejects_an_already_paused_session(self) -> None:
        session = ActiveSession.begin(EPOCH)
        session.pause(EPOCH + timedelta(seconds=10))
        with self.assertRaises(ValueError):
            session.pause(EPOCH + timedelta(seconds=20))

    def test_resume_rejects_a_running_session(self) -> None:
        with self.assertRaises(ValueError):
            ActiveSession.begin(EPOCH).resume(EPOCH + timedelta(seconds=10))

    def test_worked_time_excludes_completed_pauses(self) -> None:
        session = ActiveSession.begin(EPOCH)
        session.pause(EPOCH + timedelta(seconds=600))
        session.resume(EPOCH + timedelta(seconds=900))  # 300s paused

        moment = EPOCH + timedelta(seconds=1200)
        self.assertEqual(1200, session.gross_seconds(moment))
        self.assertEqual(300, session.paused_seconds(moment))
        self.assertEqual(900, session.worked_seconds(moment))

    def test_worked_time_freezes_while_a_pause_is_open(self) -> None:
        session = ActiveSession.begin(EPOCH)
        session.pause(EPOCH + timedelta(seconds=600))

        # Ten minutes in, then a further ten minutes of pause: worked time must
        # stay at 600s while gross and paused both keep growing.
        for extra in (0, 60, 600):
            moment = EPOCH + timedelta(seconds=600 + extra)
            with self.subTest(extra=extra):
                self.assertEqual(extra, session.paused_seconds(moment))
                self.assertEqual(600, session.worked_seconds(moment))

    def test_round_trips_through_json_while_paused(self) -> None:
        session = ActiveSession.begin(EPOCH)
        session.pause(EPOCH + timedelta(seconds=600))
        session.resume(EPOCH + timedelta(seconds=900))
        session.pause(EPOCH + timedelta(seconds=1000))

        restored = ActiveSession.from_dict(session.to_dict())
        self.assertEqual(session, restored)

    def test_rejects_paused_state_without_a_pause_start(self) -> None:
        with self.assertRaises(CorruptJSONError):
            ActiveSession.from_dict(
                {
                    "state": "paused",
                    "id": "2026-07-14_19-42-18",
                    "start": "2026-07-14T19:42:18+03:00",
                    "pauseStart": None,
                    "pauses": [],
                }
            )

    def test_rejects_running_state_with_a_pause_start(self) -> None:
        with self.assertRaises(CorruptJSONError):
            ActiveSession.from_dict(
                {
                    "state": "running",
                    "id": "2026-07-14_19-42-18",
                    "start": "2026-07-14T19:42:18+03:00",
                    "pauseStart": "2026-07-14T19:52:18+03:00",
                    "pauses": [],
                }
            )

    def test_rejects_an_unknown_state(self) -> None:
        with self.assertRaises(CorruptJSONError):
            ActiveSession.from_dict(
                {
                    "state": "sleeping",
                    "id": "2026-07-14_19-42-18",
                    "start": "2026-07-14T19:42:18+03:00",
                }
            )

    def test_rejects_an_id_that_could_escape_the_sessions_directory(self) -> None:
        with self.assertRaises(CorruptJSONError):
            ActiveSession.from_dict(
                {
                    "state": "running",
                    "id": "../../etc/passwd",
                    "start": "2026-07-14T19:42:18+03:00",
                }
            )

    def test_rejects_a_missing_required_key(self) -> None:
        with self.assertRaises(CorruptJSONError):
            ActiveSession.from_dict({"state": "running", "id": "2026-07-14_19-42-18"})


class TestCompletedSession(unittest.TestCase):
    def _session_with_one_pause(self) -> ActiveSession:
        session = ActiveSession.begin(EPOCH)
        session.pause(EPOCH + timedelta(seconds=600))
        session.resume(EPOCH + timedelta(seconds=925))  # 325s paused
        return session

    def test_totals_are_gross_minus_paused(self) -> None:
        completed = CompletedSession.from_active(
            self._session_with_one_pause(), EPOCH + timedelta(seconds=3600)
        )
        self.assertEqual(3600, completed.gross_seconds)
        self.assertEqual(325, completed.paused_seconds)
        self.assertEqual(3275, completed.worked_seconds)
        self.assertIs(SessionStatus.COMPLETED, completed.status)

    def test_serialises_to_the_documented_shape(self) -> None:
        completed = CompletedSession.from_active(
            self._session_with_one_pause(), EPOCH + timedelta(seconds=3600)
        )
        payload = completed.to_dict()
        self.assertEqual(
            [
                "id",
                "start",
                "end",
                "status",
                "grossSeconds",
                "pausedSeconds",
                "workedSeconds",
                "pauses",
            ],
            list(payload),
        )
        self.assertEqual("completed", payload["status"])
        self.assertEqual(1, len(payload["pauses"]))

    def test_round_trips_through_json(self) -> None:
        completed = CompletedSession.from_active(
            self._session_with_one_pause(), EPOCH + timedelta(seconds=3600)
        )
        self.assertEqual(completed, CompletedSession.from_dict(completed.to_dict()))

    def test_a_session_with_no_pauses_works_its_whole_span(self) -> None:
        completed = CompletedSession.from_active(
            ActiveSession.begin(EPOCH), EPOCH + timedelta(seconds=1800)
        )
        self.assertEqual(1800, completed.gross_seconds)
        self.assertEqual(0, completed.paused_seconds)
        self.assertEqual(1800, completed.worked_seconds)

    def test_rejects_a_negative_duration(self) -> None:
        with self.assertRaises(CorruptJSONError):
            CompletedSession.from_dict(
                {
                    "id": "2026-07-14_19-42-18",
                    "start": "2026-07-14T19:42:18+03:00",
                    "end": "2026-07-14T20:42:18+03:00",
                    "status": "completed",
                    "grossSeconds": -1,
                    "pausedSeconds": 0,
                    "workedSeconds": 0,
                    "pauses": [],
                }
            )


if __name__ == "__main__":
    unittest.main()
