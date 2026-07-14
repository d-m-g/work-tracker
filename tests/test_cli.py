"""Tests for the CLI: exit codes, rendering and the ``--root`` plumbing.

These run ``main()`` in-process with injected streams -- no subprocesses -- so
they stay fast and give real tracebacks when something breaks.
"""

from __future__ import annotations

import io
import json
import unittest

from support import TrackerTestCase

from tracker.cli import EXIT_ERROR, EXIT_OK, main


class CLITestCase(TrackerTestCase):
    """Runs the CLI against the temporary root created by the base case."""

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        """Invoke ``main`` and return ``(exit_code, stdout, stderr)``."""
        out, err = io.StringIO(), io.StringIO()
        code = main(["--root", str(self.root), *argv], out=out, err=err)
        return code, out.getvalue(), err.getvalue()


class TestCommands(CLITestCase):
    def test_start_reports_success(self) -> None:
        code, out, err = self.run_cli("start")
        self.assertEqual(EXIT_OK, code)
        self.assertIn("Started session", out)
        self.assertEqual("", err)
        self.assertTrue(self.storage.has_current())

    def test_starting_twice_fails_cleanly(self) -> None:
        self.run_cli("start")
        code, out, err = self.run_cli("start")

        self.assertEqual(EXIT_ERROR, code)
        self.assertEqual("", out)
        self.assertIn("already in progress", err)
        self.assertNotIn("Traceback", err)

    def test_pause_without_a_session_fails_cleanly(self) -> None:
        code, _, err = self.run_cli("pause")
        self.assertEqual(EXIT_ERROR, code)
        self.assertIn("no session is in progress", err)

    def test_resume_without_a_pause_fails_cleanly(self) -> None:
        self.run_cli("start")
        code, _, err = self.run_cli("resume")
        self.assertEqual(EXIT_ERROR, code)
        self.assertIn("not paused", err)

    def test_the_full_lifecycle_exits_zero_at_every_step(self) -> None:
        for command in ("start", "pause", "resume", "stop"):
            with self.subTest(command=command):
                code, _, err = self.run_cli(command)
                self.assertEqual(EXIT_OK, code, err)

        self.assertFalse(self.storage.has_current())
        self.assertEqual(1, len(self.storage.list_sessions()))

    def test_stop_prints_the_totals_and_the_archive_path(self) -> None:
        self.run_cli("start")
        code, out, _ = self.run_cli("stop")

        self.assertEqual(EXIT_OK, code)
        self.assertIn("Worked:", out)
        self.assertIn("Paused:", out)
        self.assertIn("Gross:", out)
        self.assertIn("sessions/", out)

    def test_status_when_idle(self) -> None:
        code, out, _ = self.run_cli("status")
        self.assertEqual(EXIT_OK, code)
        self.assertIn("idle", out)

    def test_status_while_running(self) -> None:
        self.run_cli("start")
        code, out, _ = self.run_cli("status")

        self.assertEqual(EXIT_OK, code)
        self.assertIn("running", out)
        self.assertIn("Worked:", out)
        self.assertIn("Pauses:  0", out)

    def test_status_while_paused_flags_the_open_pause(self) -> None:
        self.run_cli("start")
        self.run_cli("pause")
        _, out, _ = self.run_cli("status")

        self.assertIn("paused", out)
        self.assertIn("one in progress", out)


class TestToggle(CLITestCase):
    def test_toggle_starts_pauses_and_resumes_in_turn(self) -> None:
        expected = ("Started session", "Paused at", "Resumed at", "Paused at")
        for sentence in expected:
            with self.subTest(expecting=sentence):
                code, out, err = self.run_cli("toggle")
                self.assertEqual(EXIT_OK, code, err)
                self.assertIn(sentence, out)

    def test_toggle_leaves_the_session_running_for_stop_to_finish(self) -> None:
        self.run_cli("toggle")
        code, _, err = self.run_cli("stop")

        self.assertEqual(EXIT_OK, code, err)
        self.assertFalse(self.storage.has_current())
        self.assertEqual(1, len(self.storage.list_sessions()))

    def test_toggle_json_names_the_action_it_took(self) -> None:
        _, out, _ = self.run_cli("--json", "toggle")
        started = json.loads(out)
        self.assertEqual("started", started["action"])
        self.assertEqual("running", started["state"])

        _, out, _ = self.run_cli("--json", "toggle")
        paused = json.loads(out)
        self.assertEqual("paused", paused["action"])
        self.assertEqual("paused", paused["state"])
        self.assertIsNotNone(paused["pauseStart"])

        _, out, _ = self.run_cli("--json", "toggle")
        resumed = json.loads(out)
        self.assertEqual("resumed", resumed["action"])
        self.assertEqual("running", resumed["state"])
        self.assertIsNone(resumed["pauseStart"])
        self.assertEqual(1, len(resumed["pauses"]))

    def test_toggle_reports_a_corrupt_current_file_cleanly(self) -> None:
        self.storage.current_path.write_text("{ oops", encoding="utf-8")
        code, _, err = self.run_cli("toggle")

        self.assertEqual(EXIT_ERROR, code)
        self.assertIn("not valid JSON", err)
        self.assertNotIn("Traceback", err)


class TestJSONOutput(CLITestCase):
    def test_status_json_is_machine_readable(self) -> None:
        self.run_cli("start")
        code, out, _ = self.run_cli("--json", "status")

        self.assertEqual(EXIT_OK, code)
        payload = json.loads(out)
        self.assertEqual("running", payload["state"])
        self.assertEqual(0, payload["pauses"])

    def test_idle_status_json(self) -> None:
        _, out, _ = self.run_cli("--json", "status")
        payload = json.loads(out)
        self.assertEqual("idle", payload["state"])
        self.assertIsNone(payload["id"])

    def test_stop_json_matches_the_archived_document(self) -> None:
        self.run_cli("start")
        _, out, _ = self.run_cli("--json", "stop")

        payload = json.loads(out)
        self.assertEqual("completed", payload["status"])
        archived = json.loads(self.storage.list_sessions()[0].read_text())
        self.assertEqual(archived, payload)


class TestUsageErrors(CLITestCase):
    def test_an_unknown_command_is_a_usage_error(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            main(["--root", str(self.root), "frobnicate"], out=io.StringIO(), err=io.StringIO())
        self.assertEqual(2, caught.exception.code)

    def test_no_command_is_a_usage_error(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            main(["--root", str(self.root)], out=io.StringIO(), err=io.StringIO())
        self.assertEqual(2, caught.exception.code)

    def test_a_corrupt_current_file_fails_cleanly(self) -> None:
        self.storage.current_path.write_text("{ oops", encoding="utf-8")
        code, _, err = self.run_cli("status")

        self.assertEqual(EXIT_ERROR, code)
        self.assertIn("not valid JSON", err)
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
