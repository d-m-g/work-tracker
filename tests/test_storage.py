"""Tests for the persistence layer."""

from __future__ import annotations

import unittest
from datetime import timedelta

from support import EPOCH, TrackerTestCase

from tracker.models import ActiveSession, CompletedSession
from tracker.storage import NoSuchSessionError, SessionExistsError
from tracker.utils import CorruptJSONError, read_json


class TestCurrentFile(TrackerTestCase):
    def test_no_current_session_reads_as_none(self) -> None:
        self.assertFalse(self.storage.has_current())
        self.assertIsNone(self.storage.load_current())

    def test_create_then_load_round_trips(self) -> None:
        session = ActiveSession.begin(EPOCH)
        self.storage.create_current(session)

        self.assertTrue(self.storage.has_current())
        self.assertEqual(session, self.storage.load_current())

    def test_create_refuses_to_clobber_an_existing_session(self) -> None:
        self.storage.create_current(ActiveSession.begin(EPOCH))
        with self.assertRaises(SessionExistsError):
            self.storage.create_current(ActiveSession.begin(EPOCH + timedelta(seconds=5)))

        # The original session must be untouched.
        loaded = self.storage.load_current()
        assert loaded is not None
        self.assertEqual("2026-07-14_19-42-18", loaded.id)

    def test_save_overwrites_an_existing_session(self) -> None:
        session = ActiveSession.begin(EPOCH)
        self.storage.create_current(session)

        session.pause(EPOCH + timedelta(seconds=60))
        self.storage.save_current(session)

        self.assertEqual(session, self.storage.load_current())

    def test_delete_removes_the_file_and_is_idempotent(self) -> None:
        self.storage.create_current(ActiveSession.begin(EPOCH))
        self.storage.delete_current()
        self.assertFalse(self.storage.has_current())
        self.storage.delete_current()  # must not raise

    def test_a_corrupt_current_file_is_reported_not_swallowed(self) -> None:
        self.storage.current_path.write_text("{{{", encoding="utf-8")
        with self.assertRaises(CorruptJSONError):
            self.storage.load_current()

    def test_written_json_matches_the_documented_schema(self) -> None:
        self.storage.create_current(ActiveSession.begin(EPOCH))
        payload = read_json(self.storage.current_path)
        self.assertEqual(
            {
                "state": "running",
                "id": "2026-07-14_19-42-18",
                "start": "2026-07-14T19:42:18+03:00",
                "task": None,
                "pauseStart": None,
                "pauses": [],
            },
            payload,
        )


class TestSessionArchive(TrackerTestCase):
    def _completed(self, end_offset: int = 3600) -> CompletedSession:
        return CompletedSession.from_active(
            ActiveSession.begin(EPOCH), EPOCH + timedelta(seconds=end_offset)
        )

    def test_archive_writes_a_file_named_after_the_session(self) -> None:
        path = self.storage.archive(self._completed())
        self.assertEqual("2026-07-14_19-42-18.json", path.name)
        self.assertEqual(self.storage.sessions_dir, path.parent)
        self.assertTrue(path.is_file())

    def test_archive_creates_the_sessions_directory_on_demand(self) -> None:
        self.assertFalse(self.storage.sessions_dir.exists())
        self.storage.archive(self._completed())
        self.assertTrue(self.storage.sessions_dir.is_dir())

    def test_archive_refuses_to_overwrite_an_existing_record(self) -> None:
        self.storage.archive(self._completed())
        with self.assertRaises(SessionExistsError):
            self.storage.archive(self._completed(end_offset=7200))

    def test_archived_session_round_trips(self) -> None:
        completed = self._completed()
        path = self.storage.archive(completed)
        self.assertEqual(completed, self.storage.load_session(path))

    def test_update_session_rewrites_a_record_that_exists(self) -> None:
        completed = self._completed()
        path = self.storage.archive(completed)

        self.storage.update_session(completed.with_task("written down late"))
        self.assertEqual("written down late", self.storage.load_session(path).task)

    def test_update_session_refuses_to_create_a_record(self) -> None:
        # `archive` stays the only thing that can bring a session into existence,
        # so a mistyped id here can only ever fail -- never quietly mint a new day.
        with self.assertRaises(NoSuchSessionError):
            self.storage.update_session(self._completed())
        self.assertEqual([], self.storage.list_sessions())

    def test_list_sessions_is_empty_before_anything_is_archived(self) -> None:
        self.assertEqual([], self.storage.list_sessions())

    def test_list_sessions_returns_archives_oldest_first(self) -> None:
        for offset in (0, 7200, 3600):
            start = EPOCH + timedelta(seconds=offset)
            self.storage.archive(
                CompletedSession.from_active(
                    ActiveSession.begin(start), start + timedelta(seconds=60)
                )
            )

        self.assertEqual(
            [
                "2026-07-14_19-42-18.json",
                "2026-07-14_20-42-18.json",
                "2026-07-14_21-42-18.json",
            ],
            [path.name for path in self.storage.list_sessions()],
        )


if __name__ == "__main__":
    unittest.main()
