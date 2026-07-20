"""Tests for the local-first sync and its reconciliation logic.

Like the rest of the suite these bind no socket and spawn no ssh: the decisions
that matter -- what becomes of the live session when local and VM meet again, how
two copies of one session are merged, how the environment switches remote mode on
-- are pure functions, asserted directly. The ssh/rsync plumbing is a thin shell
over them and is exercised live against the real VM instead.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tracker.cli import _cmd_sync
from tracker.remote import (
    latest_activity,
    clear_offline,
    clear_pending,
    is_pending,
    mark_pending,
    merge_current,
    note_offline,
    note_synced,
    offline_recent,
    remote_from_env,
    resolve_current,
    synced_recently,
)

#: A well-formed session id and its matching start. Ids must pass the tracker's own
#: validation once a document is parsed (which :func:`merge_current` does), so the
#: bare "a"/"b" of a pure dict comparison will not do here.
_A_ID = "2026-07-16_10-00-00"
_A_START = "2026-07-16T10:00:00+00:00"
_B_ID = "2026-07-16_09-00-00"
_B_START = "2026-07-16T09:00:00+00:00"


def _session(session_id, start, pauses=None, pause_start=None, state="running"):
    """A minimal current.json-shaped dict for the reconciler."""
    return {
        "state": state,
        "id": session_id,
        "start": start,
        "task": None,
        "pauseStart": pause_start,
        "pauses": pauses or [],
    }


def _pause(start, end):
    """A minimal closed-pause dict; ``seconds`` is recomputed on read, so it is 0."""
    return {"start": start, "end": end, "seconds": 0}


def _never_archived(_session_id):
    """An ``is_archived`` that says every session is still live."""
    return False


class TestResolveCurrent(unittest.TestCase):
    """What becomes of the live session when local and VM meet again."""

    def test_both_idle_does_nothing(self) -> None:
        plan = resolve_current(None, None, _never_archived)
        self.assertEqual("none", plan.action)
        self.assertIsNone(plan.stash)

    def test_only_local_flows_up(self) -> None:
        plan = resolve_current(_session(_A_ID, _A_START), None, _never_archived)
        self.assertEqual("push", plan.action)

    def test_only_remote_flows_down(self) -> None:
        plan = resolve_current(None, _session(_A_ID, _A_START), _never_archived)
        self.assertEqual("pull", plan.action)

    def test_same_session_is_merged(self) -> None:
        # Same id on both sides is never a winner-takes-all: it is a merge.
        local = _session(_A_ID, _A_START)
        remote = _session(_A_ID, _A_START, pause_start="2026-07-16T11:00:00+00:00", state="paused")
        plan = resolve_current(local, remote, _never_archived)
        self.assertEqual("merge", plan.action)
        assert plan.merged is not None
        # The web's pause folds down: the merged head is the more recent, paused side.
        self.assertEqual("paused", plan.merged["state"])

    def test_different_sessions_recent_wins_and_loser_is_stashed(self) -> None:
        newer = _session(_A_ID, _A_START)
        older = _session(_B_ID, _B_START)
        up = resolve_current(newer, older, _never_archived)
        self.assertEqual("push", up.action)
        self.assertEqual(older, up.stash)
        down = resolve_current(older, newer, _never_archived)
        self.assertEqual("pull", down.action)
        self.assertEqual(older, down.stash)

    def test_local_stop_clears_the_remote(self) -> None:
        # This machine stopped the session (local idle, its id archived); the VM's
        # lingering live copy must be cleared, not pulled back down.
        remote = _session(_A_ID, _A_START)
        plan = resolve_current(None, remote, lambda i: i == _A_ID)
        self.assertEqual("none", plan.action)
        self.assertTrue(plan.clear_remote)
        self.assertFalse(plan.clear_local)

    def test_web_stop_clears_the_local(self) -> None:
        # The web stopped it (VM idle, its id archived on both sides); the local
        # lingering live copy must be cleared, not pushed back up.
        local = _session(_A_ID, _A_START)
        plan = resolve_current(local, None, lambda i: i == _A_ID)
        self.assertEqual("none", plan.action)
        self.assertTrue(plan.clear_local)
        self.assertFalse(plan.clear_remote)

    def test_a_dead_local_lets_an_independent_remote_win(self) -> None:
        # Local holds a stopped-but-lingering session; the VM holds a genuinely
        # different live one. The dead local is cleared and the remote flows down.
        local = _session(_A_ID, _A_START)
        remote = _session(_B_ID, _B_START)
        plan = resolve_current(local, remote, lambda i: i == _A_ID)
        self.assertEqual("pull", plan.action)
        self.assertTrue(plan.clear_local)
        self.assertIsNone(plan.stash)


class TestMergeCurrent(unittest.TestCase):
    """Folding two copies of one session together without losing anything."""

    def test_different_ids_are_not_mergeable(self) -> None:
        a = _session(_A_ID, _A_START)
        b = _session(_B_ID, _B_START)
        self.assertIsNone(merge_current(a, b))

    def test_closed_pauses_are_unioned(self) -> None:
        # A pause this machine recorded and a pause the web recorded both survive.
        local = _session(_A_ID, _A_START, pauses=[_pause("2026-07-16T10:10:00+00:00", "2026-07-16T10:20:00+00:00")])
        remote = _session(_A_ID, _A_START, pauses=[_pause("2026-07-16T10:30:00+00:00", "2026-07-16T10:40:00+00:00")])
        merged = merge_current(local, remote)
        assert merged is not None
        self.assertEqual(2, len(merged["pauses"]))

    def test_head_comes_from_the_more_recent_side(self) -> None:
        running = _session(_A_ID, _A_START)
        paused = _session(_A_ID, _A_START, pause_start="2026-07-16T11:00:00+00:00", state="paused")
        merged = merge_current(running, paused)
        assert merged is not None
        self.assertEqual("paused", merged["state"])
        self.assertEqual("2026-07-16T11:00:00+00:00", merged["pauseStart"])

    def test_a_corrupt_side_is_not_mergeable(self) -> None:
        # Same id, but the remote will not parse (no 'state'): leave it to the
        # caller to stash rather than blend a broken document in.
        local = _session(_A_ID, _A_START)
        corrupt = {"id": _A_ID, "start": _A_START}
        self.assertIsNone(merge_current(local, corrupt))


class TestLatestActivity(unittest.TestCase):
    """The most recent instant a session touched anything."""

    def test_none_for_no_session(self) -> None:
        self.assertIsNone(latest_activity(None))

    def test_a_pause_is_more_recent_than_the_start(self) -> None:
        session = _session(
            _A_ID,
            "2026-07-16T09:00:00+00:00",
            pauses=[_pause("2026-07-16T10:00:00+00:00", "2026-07-16T10:15:00+00:00")],
        )
        moment = latest_activity(session)
        assert moment is not None
        self.assertEqual("2026-07-16T10:15:00+00:00", moment.isoformat())

    def test_a_garbled_timestamp_is_skipped_not_raised(self) -> None:
        session = _session("a", "not-a-timestamp")
        self.assertIsNone(latest_activity(session))


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
    """The bit of state that says a reconcile is owed."""

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
    """The backoff that stops offline sync kicks each paying the connect timeout."""

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
        self.assertFalse(offline_recent(self.root, cooldown=0.0))

    def test_clearing_re_probes_immediately(self) -> None:
        note_offline(self.root)
        clear_offline(self.root)
        self.assertFalse(offline_recent(self.root))

    def test_a_garbled_marker_means_try_the_vm(self) -> None:
        (self.root / ".offline_since").write_text("not-a-number", encoding="utf-8")
        self.assertFalse(offline_recent(self.root))


class TestSyncFreshness(unittest.TestCase):
    """The throttle that lets an idle sync kick skip the VM when nothing is owed."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_not_fresh_without_a_marker(self) -> None:
        self.assertFalse(synced_recently(self.root))

    def test_fresh_right_after_noting(self) -> None:
        note_synced(self.root)
        self.assertTrue(synced_recently(self.root))

    def test_stales_once_the_window_passes(self) -> None:
        note_synced(self.root)
        self.assertFalse(synced_recently(self.root, window=0.0))

    def test_a_garbled_marker_means_sync(self) -> None:
        (self.root / ".last_sync").write_text("not-a-number", encoding="utf-8")
        self.assertFalse(synced_recently(self.root))


class TestCmdSyncWithoutRemote(unittest.TestCase):
    """With no VM configured, ``sync`` is a clean no-op."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_no_remote_is_a_zero_exit_no_op(self) -> None:
        import io

        exit_code = _cmd_sync(None, self.root, io.StringIO())
        self.assertEqual(0, exit_code)
        # Nothing to reconcile means nothing to lock, either.
        self.assertFalse((self.root / ".sync_lock").exists())


if __name__ == "__main__":
    unittest.main()
