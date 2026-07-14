"""Tests for the web API's write commands.

These need no socket either: :func:`web.api.run_command` is a function of a
Storage and a clock, exactly like the payload builders it answers with.

The through-line of the whole file: the browser is not a second writer. Every
command is one call into the same tracker the CLI drives, so it obeys the same
rules, refuses the same things, and reports the same durations. A test that could
pass here and fail at the CLI would mean the two had drifted apart -- which is the
thing the design exists to prevent.
"""

from __future__ import annotations

import unittest

from support import TrackerTestCase

from tracker.storage import NoSuchSessionError
from tracker.tracker import (
    InvalidTaskError,
    NoActiveSessionError,
    SessionAlreadyRunningError,
    WrongStateError,
)
from web.api import UnknownCommandError, run_command


class CommandTestCase(TrackerTestCase):
    def send(self, command: str, **body: object) -> dict:
        """Run one command, as the server does when a POST arrives."""
        return run_command(self.storage, command, body, clock=self.clock)


class TestCommands(CommandTestCase):
    def test_start(self) -> None:
        result = self.send("start")
        self.assertEqual("start", result["action"])
        self.assertEqual("running", result["status"]["state"])
        self.assertTrue(self.storage.has_current())

    def test_start_with_a_task(self) -> None:
        result = self.send("start", task="rewriting the parser")
        self.assertEqual("rewriting the parser", result["status"]["task"])

    def test_pause_then_resume(self) -> None:
        self.send("start")
        self.clock.advance(600)

        paused = self.send("pause")
        self.assertEqual("paused", paused["status"]["state"])
        self.assertTrue(paused["status"]["pauseInProgress"])

        self.clock.advance(300)
        resumed = self.send("resume")
        self.assertEqual("running", resumed["status"]["state"])
        self.assertEqual(600, resumed["status"]["workedSeconds"])  # frozen while away
        self.assertEqual(300, resumed["status"]["pausedSeconds"])

    def test_stop_archives_the_day_and_returns_it(self) -> None:
        self.send("start", task="rewriting the parser")
        self.clock.advance(3600)

        result = self.send("stop")
        self.assertEqual("idle", result["status"]["state"])
        self.assertEqual(3600, result["session"]["workedSeconds"])
        self.assertEqual("rewriting the parser", result["session"]["task"])
        self.assertFalse(self.storage.has_current())

    def test_toggle_reports_which_of_the_three_it_did(self) -> None:
        # The action a toggle *chose* is the interesting half of its answer.
        self.assertEqual("started", self.send("toggle")["action"])
        self.assertEqual("paused", self.send("toggle")["action"])
        self.assertEqual("resumed", self.send("toggle")["action"])

    def test_toggle_never_stops_a_session(self) -> None:
        self.send("start")
        for _ in range(6):
            self.send("toggle")
        self.assertTrue(self.storage.has_current())  # still there, whatever it did

    def test_every_command_answers_with_the_state_it_left_behind(self) -> None:
        # The UI renders what the server read back afterwards, never what the click
        # assumed -- so a button that raced a Shortcut still shows the truth.
        self.send("start")
        self.clock.advance(90)
        self.assertEqual(90, self.send("pause")["status"]["workedSeconds"])


class TestCommandsRefuse(CommandTestCase):
    def test_starting_twice(self) -> None:
        self.send("start")
        with self.assertRaises(SessionAlreadyRunningError):
            self.send("start")

    def test_pausing_nothing(self) -> None:
        with self.assertRaises(NoActiveSessionError):
            self.send("pause")

    def test_pausing_twice(self) -> None:
        self.send("start")
        self.send("pause")
        with self.assertRaises(WrongStateError):
            self.send("pause")

    def test_resuming_a_running_session(self) -> None:
        self.send("start")
        with self.assertRaises(WrongStateError):
            self.send("resume")

    def test_stopping_nothing(self) -> None:
        with self.assertRaises(NoActiveSessionError):
            self.send("stop")

    def test_a_command_that_does_not_exist(self) -> None:
        # The UI cannot reach an operation by guessing at a URL.
        with self.assertRaises(UnknownCommandError):
            self.send("delete")

    def test_a_refused_command_changes_nothing(self) -> None:
        self.send("start")
        self.clock.advance(600)
        with self.assertRaises(SessionAlreadyRunningError):
            self.send("start")

        status = self.send("pause")["status"]
        self.assertEqual(600, status["workedSeconds"])  # the first session, intact


class TestTaskCommand(CommandTestCase):
    def test_it_sets_the_live_task(self) -> None:
        self.send("start")
        self.assertEqual("code review", self.send("task", task="code review")["status"]["task"])

    def test_an_empty_task_clears_it(self) -> None:
        # Emptying the box is not a different operation from clearing it.
        self.send("start", task="rewriting the parser")
        self.assertIsNone(self.send("task", task="")["status"]["task"])
        self.assertIsNone(self.send("task")["status"]["task"])

    def test_it_amends_an_archived_session(self) -> None:
        self.send("start")
        self.clock.advance(3600)
        session_id = self.send("stop")["session"]["id"]

        result = self.send("task", id=session_id, task="written down late")
        self.assertEqual("written down late", result["session"]["task"])
        self.assertEqual(3600, result["session"]["workedSeconds"])  # untouched
        self.assertEqual("idle", result["status"]["state"])  # nothing was restarted

    def test_an_unknown_session_is_refused(self) -> None:
        with self.assertRaises(NoSuchSessionError):
            self.send("task", id="2020-01-01_00-00-00", task="nothing")

    def test_an_id_that_would_escape_the_sessions_directory_is_refused(self) -> None:
        with self.assertRaises(NoSuchSessionError):
            self.send("task", id="../../../../etc/passwd", task="pwned")

    def test_an_id_that_is_not_text_is_refused(self) -> None:
        with self.assertRaises(InvalidTaskError):
            self.send("task", id=42, task="nothing")

    def test_a_task_that_is_not_text_is_refused(self) -> None:
        self.send("start")
        with self.assertRaises(InvalidTaskError):
            self.send("task", task={"not": "text"})

    def test_an_over_long_task_is_refused(self) -> None:
        self.send("start")
        with self.assertRaises(InvalidTaskError):
            self.send("task", task="x" * 500)


if __name__ == "__main__":
    unittest.main()
