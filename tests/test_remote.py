"""Tests for the remote-driving and offline-reconciliation logic.

Like the rest of the suite these bind no socket and spawn no ssh: the decisions
that matter -- who wins a reconnection, what counts as recent activity, how the
environment switches remote mode on -- are pure functions, asserted directly. The
ssh/rsync plumbing is a thin shell over them and is exercised live against the
real VM instead.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tracker.cli import _strip_root
from tracker.remote import (
    clear_offline,
    clear_pending,
    is_pending,
    latest_activity,
    mark_pending,
    note_offline,
    offline_recent,
    reconcile_current,
    remote_from_env,
)


def _session(session_id: str, start: str, pauses=None, pause_start=None):
    """A minimal current.json-shaped dict for the reconciler."""
    return {
        "state": "running",
        "id": session_id,
        "start": start,
        "task": None,
        "pauseStart": pause_start,
        "pauses": pauses or [],
    }


class TestReconcileCurrent(unittest.TestCase):
    """Who wins the live session when local and remote meet again."""

    def test_both_idle_is_a_draw(self) -> None:
        self.assertEqual(("none", False), reconcile_current(None, None))

    def test_only_local_active_flows_up(self) -> None:
        session = _session("a", "2026-07-16T10:00:00+00:00")
        self.assertEqual(("local", False), reconcile_current(session, None))

    def test_only_remote_active_flows_down(self) -> None:
        session = _session("a", "2026-07-16T10:00:00+00:00")
        self.assertEqual(("remote", False), reconcile_current(None, session))

    def test_same_session_local_wins_without_conflict(self) -> None:
        # Only this machine could have changed it while the VM was unreachable.
        local = _session("a", "2026-07-16T10:00:00+00:00", pause_start="2026-07-16T11:00:00+00:00")
        remote = _session("a", "2026-07-16T10:00:00+00:00")
        self.assertEqual(("local", False), reconcile_current(local, remote))

    def test_different_sessions_more_recent_wins_and_conflicts(self) -> None:
        older = _session("a", "2026-07-16T09:00:00+00:00")
        newer = _session("b", "2026-07-16T10:00:00+00:00")
        self.assertEqual(("remote", True), reconcile_current(older, newer))
        self.assertEqual(("local", True), reconcile_current(newer, older))

    def test_comparison_respects_timezone_offsets(self) -> None:
        # Local reads 14:00+03:00 = 11:00 UTC; remote reads 12:00Z = 12:00 UTC,
        # which is genuinely later. A string compare would wrongly pick local.
        local = _session("a", "2026-07-16T14:00:00+03:00")
        remote = _session("b", "2026-07-16T12:00:00+00:00")
        self.assertEqual(("remote", True), reconcile_current(local, remote))


class TestLatestActivity(unittest.TestCase):
    """The most recent instant a session touched anything."""

    def test_none_for_no_session(self) -> None:
        self.assertIsNone(latest_activity(None))

    def test_a_pause_is_more_recent_than_the_start(self) -> None:
        session = _session(
            "a",
            "2026-07-16T09:00:00+00:00",
            pauses=[{"start": "2026-07-16T10:00:00+00:00", "end": "2026-07-16T10:15:00+00:00", "seconds": 900}],
        )
        moment = latest_activity(session)
        assert moment is not None
        self.assertEqual("2026-07-16T10:15:00+00:00", moment.isoformat())

    def test_a_garbled_timestamp_is_skipped_not_raised(self) -> None:
        session = _session("a", "not-a-timestamp")
        self.assertIsNone(latest_activity(session))


class TestStripRoot(unittest.TestCase):
    """``--root`` is a local path and must never be forwarded to the VM."""

    def test_passes_ordinary_arguments_through(self) -> None:
        self.assertEqual(["--json", "status"], _strip_root(["--json", "status"]))

    def test_drops_root_and_its_value(self) -> None:
        self.assertEqual(["status"], _strip_root(["--root", "/tmp/x", "status"]))

    def test_drops_the_equals_form(self) -> None:
        self.assertEqual(["toggle"], _strip_root(["--root=/tmp/x", "toggle"]))

    def test_keeps_a_task_that_looks_nothing_like_root(self) -> None:
        self.assertEqual(
            ["start", "--task", "root canal"],
            _strip_root(["start", "--task", "root canal"]),
        )


class TestRemoteFromEnv(unittest.TestCase):
    """The single switch: WORK_TRACKER_SSH set or not."""

    def test_unset_means_no_remote(self) -> None:
        self.assertIsNone(remote_from_env({}))

    def test_blank_means_no_remote(self) -> None:
        self.assertIsNone(remote_from_env({"WORK_TRACKER_SSH": "   "}))

    def test_set_builds_a_remote_with_defaults(self) -> None:
        remote = remote_from_env({"WORK_TRACKER_SSH": "ubuntu@host"})
        assert remote is not None
        self.assertEqual("ubuntu@host", remote.destination)
        self.assertEqual("work-tracker", remote.path)
        self.assertIsNone(remote.key)

    def test_path_and_key_are_honoured(self) -> None:
        remote = remote_from_env(
            {
                "WORK_TRACKER_SSH": "ubuntu@host",
                "WORK_TRACKER_SSH_PATH": "apps/tracker",
                "WORK_TRACKER_SSH_KEY": "/keys/id",
            }
        )
        assert remote is not None
        self.assertEqual("apps/tracker", remote.path)
        self.assertEqual("/keys/id", remote.key)


class TestPendingMarker(unittest.TestCase):
    """The one bit of state: is a reconcile owed?"""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_absent_by_default(self) -> None:
        self.assertFalse(is_pending(self.root))

    def test_mark_then_clear(self) -> None:
        mark_pending(self.root)
        self.assertTrue(is_pending(self.root))
        clear_pending(self.root)
        self.assertFalse(is_pending(self.root))

    def test_clearing_absent_is_harmless(self) -> None:
        clear_pending(self.root)  # must not raise
        self.assertFalse(is_pending(self.root))


class TestOfflineCooldown(unittest.TestCase):
    """The backoff that stops offline polls each paying the connect timeout."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_not_recent_without_a_marker(self) -> None:
        self.assertFalse(offline_recent(self.root))

    def test_recent_right_after_noting(self) -> None:
        note_offline(self.root)
        self.assertTrue(offline_recent(self.root))

    def test_expires_once_the_cooldown_passes(self) -> None:
        note_offline(self.root)
        # A zero-length window means "the failure is already old enough": re-probe.
        self.assertFalse(offline_recent(self.root, cooldown=0.0))

    def test_clearing_re_probes_immediately(self) -> None:
        note_offline(self.root)
        clear_offline(self.root)
        self.assertFalse(offline_recent(self.root))

    def test_a_garbled_marker_means_try_the_vm(self) -> None:
        (self.root / ".offline_since").write_text("not-a-number", encoding="utf-8")
        self.assertFalse(offline_recent(self.root))


if __name__ == "__main__":
    unittest.main()
