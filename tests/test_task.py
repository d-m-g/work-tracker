"""Tests for the one free-text field: what a session is being spent on.

The task cuts through every layer -- model, storage, service, CLI, web -- so it is
tested through every one of them here rather than being scattered across five
files by accident of which module happens to own each rule.
"""

from __future__ import annotations

import io
import json
import unittest
from datetime import timedelta

from support import EPOCH, TrackerTestCase

from tracker.cli import EXIT_ERROR, EXIT_OK, main
from tracker.models import MAX_TASK_LENGTH, ActiveSession, CompletedSession
from tracker.storage import NoSuchSessionError
from tracker.tracker import InvalidTaskError, NoActiveSessionError
from tracker.utils import CorruptJSONError, read_json
from web.api import build_sessions_payload, build_status_payload


class TestTaskOnTheModel(unittest.TestCase):
    def test_a_session_may_have_no_task(self) -> None:
        self.assertIsNone(ActiveSession.begin(EPOCH).task)

    def test_whitespace_is_collapsed_to_one_line(self) -> None:
        session = ActiveSession.begin(EPOCH, task="  rewriting\n  the   parser \t")
        self.assertEqual("rewriting the parser", session.task)

    def test_a_blank_task_is_the_same_fact_as_no_task(self) -> None:
        # One way to say "nothing written down", not two: the file never holds "".
        self.assertIsNone(ActiveSession.begin(EPOCH, task="   ").task)
        self.assertIsNone(ActiveSession.begin(EPOCH, task="").to_dict()["task"])

    def test_it_round_trips_through_json(self) -> None:
        session = ActiveSession.begin(EPOCH, task="rewriting the parser")
        self.assertEqual(session, ActiveSession.from_dict(session.to_dict()))

    def test_a_file_written_before_the_field_existed_still_loads(self) -> None:
        legacy = {
            "state": "running",
            "id": "2026-07-14_19-42-18",
            "start": "2026-07-14T19:42:18+03:00",
            "pauseStart": None,
            "pauses": [],
        }
        self.assertIsNone(ActiveSession.from_dict(legacy).task)

    def test_a_task_that_is_not_text_is_corrupt(self) -> None:
        with self.assertRaises(CorruptJSONError):
            ActiveSession.begin(EPOCH, task=42)  # type: ignore[arg-type]

    def test_an_over_long_task_already_on_disk_still_loads(self) -> None:
        # Lenient on the way out: refusing to read it would not make it shorter,
        # it would only cost you the day it belongs to.
        session = ActiveSession.begin(EPOCH, task="x" * (MAX_TASK_LENGTH + 50))
        self.assertEqual(MAX_TASK_LENGTH + 50, len(ActiveSession.from_dict(session.to_dict()).task))

    def test_stopping_carries_the_task_into_the_archive(self) -> None:
        session = ActiveSession.begin(EPOCH, task="rewriting the parser")
        completed = CompletedSession.from_active(session, EPOCH + timedelta(seconds=3600))
        self.assertEqual("rewriting the parser", completed.task)

    def test_with_task_leaves_every_number_alone(self) -> None:
        original = CompletedSession.from_active(
            ActiveSession.begin(EPOCH), EPOCH + timedelta(seconds=3600)
        )
        amended = original.with_task("written down late")

        self.assertEqual("written down late", amended.task)
        self.assertIsNone(original.task)  # frozen: the original is untouched
        for field in ("id", "start", "end", "grossSeconds", "pausedSeconds", "workedSeconds"):
            self.assertEqual(original.to_dict()[field], amended.to_dict()[field])


class TestTaskThroughTheTracker(TrackerTestCase):
    def test_start_records_the_task(self) -> None:
        self.tracker.start("rewriting the parser")
        self.assertEqual("rewriting the parser", self.tracker.status().task)

    def test_it_can_be_set_while_running(self) -> None:
        self.tracker.start()
        self.tracker.set_task("code review")
        self.assertEqual("code review", self.tracker.task())

    def test_it_can_be_set_while_paused(self) -> None:
        # What you are working on is not a fact about the clock.
        self.tracker.start()
        self.tracker.pause()
        self.assertEqual("code review", self.tracker.set_task("code review").task)

    def test_setting_it_again_replaces_it(self) -> None:
        self.tracker.start("rewriting the parser")
        self.tracker.set_task("code review")
        self.assertEqual("code review", self.tracker.task())

    def test_it_can_be_cleared(self) -> None:
        self.tracker.start("rewriting the parser")
        self.assertIsNone(self.tracker.set_task(None).task)

    def test_it_survives_a_pause_and_resume(self) -> None:
        self.tracker.start("rewriting the parser")
        self.clock.advance(600)
        self.tracker.pause()
        self.clock.advance(300)
        self.tracker.resume()
        self.assertEqual("rewriting the parser", self.tracker.task())

    def test_a_toggle_that_starts_takes_the_task(self) -> None:
        self.assertEqual("rewriting the parser", self.tracker.toggle("rewriting the parser").session.task)

    def test_a_toggle_that_pauses_leaves_the_task_alone(self) -> None:
        # Going to lunch does not change what you are working on -- and the key a
        # toggle is bound to has nothing to ask you anyway.
        self.tracker.start("rewriting the parser")
        self.assertEqual("rewriting the parser", self.tracker.toggle("something else").session.task)

    def test_setting_it_without_a_session_is_refused(self) -> None:
        with self.assertRaises(NoActiveSessionError):
            self.tracker.set_task("rewriting the parser")

    def test_an_over_long_task_is_refused_on_the_way_in(self) -> None:
        with self.assertRaises(InvalidTaskError):
            self.tracker.start("x" * (MAX_TASK_LENGTH + 1))

    def test_a_task_that_is_not_text_is_refused_on_the_way_in(self) -> None:
        self.tracker.start()
        with self.assertRaises(InvalidTaskError):
            self.tracker.set_task(42)  # type: ignore[arg-type]

    def test_a_refused_task_is_not_written(self) -> None:
        self.tracker.start("rewriting the parser")
        with self.assertRaises(InvalidTaskError):
            self.tracker.set_task("x" * (MAX_TASK_LENGTH + 1))
        self.assertEqual("rewriting the parser", self.tracker.task())


class TestTaskOnAnArchivedSession(TrackerTestCase):
    def _archive(self, task: str | None = None) -> str:
        self.tracker.start(task)
        self.clock.advance(3600)
        completed, _ = self.tracker.stop()
        return completed.id

    def test_a_day_can_be_labelled_after_the_fact(self) -> None:
        session_id = self._archive()
        self.tracker.set_archived_task(session_id, "written down late")
        self.assertEqual("written down late", self.tracker.archived(session_id).task)

    def test_the_numbers_are_rewritten_unchanged(self) -> None:
        session_id = self._archive("rewriting the parser")
        before = read_json(self.storage.session_path(session_id))

        self.tracker.set_archived_task(session_id, "code review")
        after = read_json(self.storage.session_path(session_id))

        self.assertEqual("code review", after["task"])
        del before["task"], after["task"]
        self.assertEqual(before, after)  # every other byte of it is identical

    def test_it_can_be_cleared(self) -> None:
        session_id = self._archive("rewriting the parser")
        self.assertIsNone(self.tracker.set_archived_task(session_id, None).task)

    def test_a_session_that_does_not_exist_is_refused(self) -> None:
        with self.assertRaises(NoSuchSessionError):
            self.tracker.set_archived_task("2020-01-01_00-00-00", "nothing")

    def test_an_id_that_would_escape_the_sessions_directory_is_refused(self) -> None:
        # The id becomes a filename, so this is the check that keeps it a filename.
        for hostile in ("../../etc/passwd", "..", "a/b", "2026-07-14_19-42-18/../../x"):
            with self.assertRaises(NoSuchSessionError):
                self.tracker.set_archived_task(hostile, "pwned")

    def test_no_file_is_created_by_labelling_a_session_that_is_not_there(self) -> None:
        with self.assertRaises(NoSuchSessionError):
            self.tracker.set_archived_task("2020-01-01_00-00-00", "nothing")
        self.assertEqual([], self.storage.list_sessions())


class TestTaskThroughTheCLI(TrackerTestCase):
    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = main(["--root", str(self.root), *argv], out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def test_start_takes_a_task_and_reports_it(self) -> None:
        code, out, _ = self.run_cli("start", "--task", "rewriting the parser")
        self.assertEqual(EXIT_OK, code)
        self.assertIn("rewriting the parser", out)

    def test_status_shows_the_task(self) -> None:
        self.run_cli("start", "--task", "rewriting the parser")
        _, out, _ = self.run_cli("status")
        self.assertIn("Task:    rewriting the parser", out)

    def test_status_omits_the_line_entirely_when_there_is_no_task(self) -> None:
        self.run_cli("start")
        _, out, _ = self.run_cli("status")
        self.assertNotIn("Task:", out)

    def test_task_prints_what_is_recorded(self) -> None:
        self.run_cli("start", "--task", "rewriting the parser")
        code, out, _ = self.run_cli("task")
        self.assertEqual(EXIT_OK, code)
        self.assertEqual("rewriting the parser\n", out)

    def test_task_says_so_out_loud_when_nothing_is_recorded(self) -> None:
        self.run_cli("start")
        _, out, _ = self.run_cli("task")
        self.assertEqual("(no task recorded)\n", out)

    def test_task_sets_it(self) -> None:
        self.run_cli("start")
        self.run_cli("task", "code review")
        _, out, _ = self.run_cli("task")
        self.assertEqual("code review\n", out)

    def test_task_clear_removes_it(self) -> None:
        self.run_cli("start", "--task", "rewriting the parser")
        self.run_cli("task", "--clear")
        _, out, _ = self.run_cli("task")
        self.assertEqual("(no task recorded)\n", out)

    def test_task_without_a_session_fails_cleanly(self) -> None:
        code, _, err = self.run_cli("task")
        self.assertEqual(EXIT_ERROR, code)
        self.assertIn("no session is in progress", err)
        self.assertNotIn("Traceback", err)

    def test_task_can_amend_an_archived_session(self) -> None:
        self.run_cli("start")
        self.run_cli("stop")
        session_id = self.storage.list_sessions()[0].stem

        code, out, _ = self.run_cli("task", "--session", session_id, "written down late")
        self.assertEqual(EXIT_OK, code)
        self.assertEqual("written down late\n", out)

    def test_task_on_an_unknown_session_fails_cleanly(self) -> None:
        code, _, err = self.run_cli("task", "--session", "2020-01-01_00-00-00", "x")
        self.assertEqual(EXIT_ERROR, code)
        self.assertIn("no such session", err)
        self.assertNotIn("Traceback", err)

    def test_an_over_long_task_fails_cleanly(self) -> None:
        code, _, err = self.run_cli("start", "--task", "x" * (MAX_TASK_LENGTH + 1))
        self.assertEqual(EXIT_ERROR, code)
        self.assertIn("at most", err)

    def test_json_status_carries_the_task(self) -> None:
        self.run_cli("start", "--task", "rewriting the parser")
        _, out, _ = self.run_cli("--json", "status")
        self.assertEqual("rewriting the parser", json.loads(out)["task"])

    def test_a_task_and_clear_together_are_bad_usage(self) -> None:
        # They ask for opposite things. Refusing beats quietly picking a winner.
        self.run_cli("start")
        with self.assertRaises(SystemExit) as raised:
            self.run_cli("task", "code review", "--clear")
        self.assertEqual(2, raised.exception.code)


class TestTaskThroughTheWebAPI(TrackerTestCase):
    def test_the_status_payload_carries_the_task(self) -> None:
        self.tracker.start("rewriting the parser")
        payload = build_status_payload(self.storage, clock=self.clock)
        self.assertEqual("rewriting the parser", payload["task"])

    def test_the_idle_payload_carries_the_key(self) -> None:
        self.assertIsNone(build_status_payload(self.storage, clock=self.clock)["task"])

    def test_an_archived_session_carries_its_task(self) -> None:
        self.tracker.start("rewriting the parser")
        self.clock.advance(3600)
        self.tracker.stop()

        sessions = build_sessions_payload(self.storage)["sessions"]
        self.assertEqual("rewriting the parser", sessions[0]["task"])


if __name__ == "__main__":
    unittest.main()
