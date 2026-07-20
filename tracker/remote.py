"""Driving the tracker on another machine over SSH, with an offline fallback.

The tracker was born as one writer of local files. A viewer on a VM turned it
into something you can also reach from a phone, and this module closes the loop:
it lets *this* machine's CLI -- and therefore the widget and the Shortcuts that
shell out to it -- drive the tracker **on the VM** instead of here, so everything
writes to one place, the one the web viewer reads.

The design keeps the tracker's founding promise (one writer, no second
implementation of the rules) intact by not writing a second implementation at
all. Every command runs against the *local* files through the very same
``tracker.py`` it always did, and a separate, detached ``sync`` folds those files
together with the VM's afterwards. There is nothing new to be right or wrong,
because the rules still live in one place and the sync only moves whole files.

The model is **local-first, VM-as-truth**:

* **Act locally, at once.** A command writes the local files and returns
  immediately -- no network in its path -- so it is instant whether the VM is a
  millisecond away or gone entirely, and drops a ``.sync_pending`` marker to say
  there is local work to push. This is what makes the widget and the Shortcuts
  respond the same offline as on.

* **Sync in the background.** A detached, lock-guarded ``sync`` (kicked after each
  command and by the widget's poll) reconciles with the VM when it can reach it,
  and does nothing when it cannot -- to be retried by the next kick. Every network
  call is hard-bounded, so a dropped wifi can never leave it hanging.

* **The VM stays the source of truth.** When there is nothing pending, the sync
  simply mirrors the VM down, so a pause or a stop done from the *web* appears
  locally. When something *is* pending -- the Mac changed things offline -- local
  and remote are reconciled: archived sessions union both ways and are never
  overwritten; two copies of the *same* live session are **merged** (their closed
  pauses unioned, the live head taken from whichever changed last); two genuinely
  *different* live sessions cannot share one ``current.json``, so the loser is
  stashed under ``conflicts/``, never deleted. A ``stop`` on either side is read
  from the archives, so a stopped session is cleared, not resurrected.

Configuration is three environment variables, so the widget and the Shortcuts
can turn it on by setting them and nothing else:

``WORK_TRACKER_SSH``       the SSH destination, e.g. ``ubuntu@203.0.113.10``.
                           Unset means "no remote": the CLI is purely local, as
                           it has always been. This one variable is the switch.
``WORK_TRACKER_SSH_PATH``  the repo directory on the VM (default ``work-tracker``).
``WORK_TRACKER_SSH_KEY``   the identity file to authenticate with (optional).
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, TextIO, Tuple

from .models import ActiveSession
from .utils import CorruptJSONError, atomic_write_json, now, parse_timestamp, read_json

#: Absolute paths to the tools we shell out to. Resolved once, with a fallback to
#: the standard macOS location, because a GUI app (the widget) spawns us with a
#: minimal PATH -- and a bare "ssh" that could not be found would look exactly
#: like a VM that could not be reached, silently pinning us offline forever.
_SSH_BIN = shutil.which("ssh") or "/usr/bin/ssh"
_RSYNC_BIN = shutil.which("rsync") or "/usr/bin/rsync"

__all__ = [
    "CurrentPlan",
    "Remote",
    "SYNC_PENDING",
    "background_sync",
    "clear_offline",
    "clear_pending",
    "flush_local",
    "is_pending",
    "latest_activity",
    "mark_pending",
    "merge_current",
    "note_offline",
    "note_synced",
    "offline_recent",
    "refresh_local",
    "remote_from_env",
    "resolve_current",
    "synced_recently",
]

#: Marker file, in the data directory, meaning "commands ran locally while the VM
#: was unreachable; reconcile before trusting the remote again". Its presence is
#: the whole state machine -- there is nothing else to remember.
SYNC_PENDING = ".sync_pending"

#: Marker file recording *when* the VM was last found unreachable. It is what lets
#: a run of offline status polls skip the connection attempt -- and its whole
#: several-second timeout -- and answer from local at once, re-probing only once
#: the cooldown has passed. Without it every poll while offline would stall.
OFFLINE_SINCE = ".offline_since"

#: How long after a failed connection to keep answering locally without retrying.
#: Long enough that a burst of once-a-second polls does not each pay the timeout;
#: short enough that coming back online is noticed within a handful of seconds.
_OFFLINE_COOLDOWN = 15.0

#: ssh's own exit code for "could not even connect". Every small command we run on
#: the VM (``true``, ``cat current.json``, ``rm -f current.json``) returns 0 when it
#: reached the VM and answered; only 255 -- or our own timeout, mapped to it -- means
#: the transport failed, which is what tells the syncer the VM is offline.
SSH_UNREACHABLE = 255

#: How long a connection attempt may hang before we call it offline. Short, so a
#: dropped network turns into a local fallback quickly rather than a frozen widget.
_CONNECT_TIMEOUT = 3

#: How long the multiplexed master connection lingers after the last use. Long
#: enough that a burst of once-a-second polls shares one connection; short enough
#: that it does not sit open forever.
_CONTROL_PERSIST = "60s"

#: A hard ceiling on *any* ssh or rsync call, whatever ssh's own timeouts do. It
#: is the backstop for the one case ``ConnectTimeout`` does not cover: a command
#: multiplexed over a master whose network vanished *after* it connected, which
#: attaches to the live socket instantly and then blocks on a dead TCP session for
#: as long as the kernel will retransmit -- minutes. This turns that into seconds.
_EXEC_TIMEOUT = 6

#: Marker file recording *when* the local files were last folded together with the
#: VM. It lets a burst of sync kicks -- one per command, one per widget poll -- do
#: real work only every so often instead of on every tick, when nothing is owed.
SYNCED_AT = ".last_sync"

#: How long a mirror stays "fresh enough" that an idle sync kick can skip the VM.
#: Short, so a web-side change surfaces within a poll or two; long enough that the
#: once-a-second kicks do not each reach across the network for nothing.
_SYNC_FRESH = 5.0


class Remote:
    """An SSH destination running the tracker, and how to talk to it."""

    def __init__(self, destination: str, path: str = "work-tracker", key: Optional[str] = None) -> None:
        self.destination = destination
        self.path = path
        self.key = key

    # -- connection ---------------------------------------------------------

    def _control_path(self) -> str:
        """A per-destination socket for connection multiplexing.

        Keyed by a hash of the destination so two different VMs never share a
        socket, and kept under the user's home so it survives between the many
        short-lived ``tracker.py`` processes the widget spawns -- which is what
        lets them share one connection instead of each dialling afresh.
        """
        digest = hashlib.sha256(self.destination.encode("utf-8")).hexdigest()[:16]
        directory = Path.home() / ".work-tracker"
        directory.mkdir(parents=True, exist_ok=True)
        return str(directory / f"ssh-{digest}.sock")

    def _ssh_command(self) -> List[str]:
        """The ssh invocation, with multiplexing and a bounded connect timeout."""
        command = [
            _SSH_BIN,
            "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={_CONNECT_TIMEOUT}",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ControlMaster=auto",
            "-o", f"ControlPath={self._control_path()}",
            "-o", f"ControlPersist={_CONTROL_PERSIST}",
            # Keepalives so a session multiplexed over a master whose network died
            # gives up on its own in a few seconds, rather than waiting out the
            # kernel's TCP retransmits. The subprocess timeout is the hard backstop.
            "-o", "ServerAliveInterval=2",
            "-o", "ServerAliveCountMax=2",
        ]
        if self.key:
            command += ["-i", self.key]
        command.append(self.destination)
        return command

    # -- running commands ---------------------------------------------------

    def _exec(self, command: str) -> Tuple[int, str, str]:
        """Run one shell command on the VM over the multiplexed connection."""
        try:
            result = subprocess.run(
                self._ssh_command() + [command],
                capture_output=True,
                text=True,
                timeout=_EXEC_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            # The connection hung past the ceiling (a dead multiplexed master, say):
            # indistinguishable, for our purposes, from a VM that cannot be reached.
            return SSH_UNREACHABLE, "", "timed out"
        except OSError as exc:
            # ssh itself missing or unrunnable: treat as unreachable, fall back.
            return SSH_UNREACHABLE, "", str(exc)
        return result.returncode, result.stdout, result.stderr

    def reachable(self) -> bool:
        """A cheap liveness probe that also warms the multiplexed connection."""
        exit_code, _, _ = self._exec("true")
        return exit_code != SSH_UNREACHABLE

    def read_current(self) -> Optional[Dict[str, object]]:
        """The VM's live session as a dict, or ``None`` if it is idle/unreachable.

        A missing ``current.json`` prints nothing and exits cleanly, so an empty
        answer is "idle", not "error"; anything unparseable is treated as idle
        too, because a reconciliation must not crash on a bad remote file.
        """
        remote_file = shlex.quote(self.path + "/current.json")
        exit_code, out, _ = self._exec(f"cat {remote_file} 2>/dev/null || true")
        if exit_code == SSH_UNREACHABLE or not out.strip():
            return None
        try:
            payload = json.loads(out)
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None

    # -- file sync ----------------------------------------------------------

    def _ssh_transport(self) -> str:
        """The ``-e`` value for rsync: our ssh, with all its options, as one word."""
        return " ".join(shlex.quote(part) for part in self._ssh_command()[:-1])

    def _remote_spec(self, *parts: str) -> str:
        """An ``rsync`` remote spec, ``dest:path/inside/repo``, safely quoted."""
        tail = "/".join((self.path,) + parts)
        return f"{self.destination}:{shlex.quote(tail)}"

    def _rsync(self, source: str, destination: str, *, extra: Optional[List[str]] = None) -> bool:
        """Run one rsync over the same SSH options. Returns True on success."""
        command = [_RSYNC_BIN, "-az", "-e", self._ssh_transport()]
        command += extra or []
        command += [source, destination]
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=_EXEC_TIMEOUT
            )
        except subprocess.TimeoutExpired:
            return False
        except OSError:
            return False
        return result.returncode == 0

    def archives_up(self, root: Path) -> bool:
        """Push local archives the VM lacks -- never overwriting one it has."""
        (root / "sessions").mkdir(parents=True, exist_ok=True)
        return self._rsync(
            str(root / "sessions") + "/",
            self._remote_spec("sessions") + "/",
            extra=["--ignore-existing", "--include=*.json", "--exclude=*"],
        )

    def archives_down(self, root: Path) -> bool:
        """Pull archives the VM has that this machine lacks -- never overwriting."""
        (root / "sessions").mkdir(parents=True, exist_ok=True)
        return self._rsync(
            self._remote_spec("sessions") + "/",
            str(root / "sessions") + "/",
            extra=["--ignore-existing", "--include=*.json", "--exclude=*"],
        )

    def push_current(self, root: Path) -> bool:
        """Make the VM's live session match this machine's."""
        return self._rsync(str(root / "current.json"), self._remote_spec("current.json"))

    def pull_current(self, root: Path) -> bool:
        """Make this machine's live session match the VM's (creating or clearing).

        When the VM is idle its ``current.json`` is gone, so mirroring it means
        deleting the local one -- otherwise a session stopped on the VM would seem
        to still be running here.
        """
        remote = self.read_current()
        if remote is None:
            _remove(root / "current.json")
            return True
        return self._rsync(self._remote_spec("current.json"), str(root / "current.json"))

    def clear_current(self) -> bool:
        """Delete the VM's live session, so it reads idle.

        The mirror of an *absence*: rsync can copy a file, but it cannot push the
        fact that one is gone, so a session stopped on this machine has to be
        cleared on the VM by hand. Returns True unless the VM was unreachable.
        """
        remote_file = shlex.quote(self.path + "/current.json")
        exit_code, _, _ = self._exec(f"rm -f {remote_file}")
        return exit_code != SSH_UNREACHABLE


# ---------------------------------------------------------------------------
# pure reconciliation logic (no IO, so it is asserted directly in the tests)
# ---------------------------------------------------------------------------


def _moments(current: Optional[Dict[str, object]]) -> List[str]:
    """Every ISO timestamp a live session carries: its start and its pauses."""
    if not current:
        return []
    found: List[str] = []
    for value in (current.get("start"), current.get("pauseStart")):
        if isinstance(value, str):
            found.append(value)
    pauses = current.get("pauses")
    if isinstance(pauses, list):
        for pause in pauses:
            if isinstance(pause, dict):
                for key in ("start", "end"):
                    value = pause.get(key)
                    if isinstance(value, str):
                        found.append(value)
    return found


def latest_activity(current: Optional[Dict[str, object]]):
    """The most recent instant a live session touched anything, as a datetime.

    That is the latest of its start and its pause boundaries -- a session paused
    an hour after it began has "activity" at the pause, not the start. Returned as
    a timezone-aware ``datetime`` so two sessions written in *different* offsets
    (the Mac at +03:00, the VM at UTC) compare by real time, not by the text of
    the string. ``None`` means no live session, which always loses to one.

    Unparseable timestamps are skipped rather than raised on: a comparison used to
    pick a winner must not crash on a hand-edited file.
    """
    latest = None
    for moment in _moments(current):
        try:
            parsed = parse_timestamp(moment)
        except Exception:
            continue
        if latest is None or parsed > latest:
            latest = parsed
    return latest


def merge_current(
    local: Dict[str, object],
    remote: Dict[str, object],
) -> Optional[Dict[str, object]]:
    """Fold two copies of the *same* live session into one, or ``None`` if they
    cannot be folded.

    Two copies are mergeable exactly when they are the same session -- same ``id``,
    and therefore the same ``start``. When they are, nothing recorded is thrown
    away: the closed pauses are the additive part of a session's history, so they
    are **unioned** (deduped by their exact ``start``/``end``), and only the live
    *head* -- the running/paused state, the open ``pauseStart`` and the ``task`` --
    is taken wholesale, from whichever side has the more recent activity. So a
    pause this machine recorded offline and a pause the web recorded meanwhile both
    survive; only the single fact of "what is the session doing *now*" is decided,
    and recency is the fair way to decide it.

    Returns ``None`` -- "not mergeable, decide it some other way" -- when the ids
    differ or when either document will not parse, so a genuine two-session clash
    or a corrupt file is left for the caller to stash rather than silently blended.
    """
    if local.get("id") != remote.get("id"):
        return None
    try:
        a = ActiveSession.from_dict(local)
        b = ActiveSession.from_dict(remote)
    except CorruptJSONError:
        return None

    unioned: Dict[Tuple[object, object], object] = {}
    for pause in list(a.pauses) + list(b.pauses):
        unioned[(pause.start, pause.end)] = pause
    merged_pauses = sorted(unioned.values(), key=lambda pause: pause.start)

    # The head -- current state, open pause and label -- comes from the side that
    # changed last; ``>=`` keeps local on an exact tie, sparing a needless push.
    a_activity = latest_activity(local)
    b_activity = latest_activity(remote)
    head = a if (b_activity is None or (a_activity is not None and a_activity >= b_activity)) else b

    merged = ActiveSession(
        id=a.id,
        start=a.start,
        state=head.state,
        pause_start=head.pause_start,
        pauses=merged_pauses,
        task=head.task,
    )
    return merged.to_dict()


@dataclass
class CurrentPlan:
    """What a sync should do with the two live sessions it found.

    ``action`` is the move for ``current.json``: ``"none"`` (leave it), ``"push"``
    (local is the truth -- send it up), ``"pull"`` (the VM is the truth -- bring it
    down), or ``"merge"`` (write ``merged`` on both sides). ``clear_local`` and
    ``clear_remote`` delete a ``current.json`` that a ``stop`` left behind, and
    ``stash`` names a live session that must be preserved under ``conflicts/``
    before it is overwritten -- the one thing a reconciliation must never drop.
    """

    action: str
    merged: Optional[Dict[str, object]] = None
    stash: Optional[Dict[str, object]] = None
    clear_local: bool = False
    clear_remote: bool = False


def resolve_current(
    local: Optional[Dict[str, object]],
    remote: Optional[Dict[str, object]],
    is_archived: Callable[[object], bool],
) -> CurrentPlan:
    """Decide what becomes of the live session when local and VM meet again.

    ``is_archived(id)`` reports whether that session already has an archive -- the
    tell that it was *stopped* somewhere, on this machine or from the web. It is
    consulted first, because a live file left behind by a stop is not a live
    session at all: whichever side still shows it is cleared, and the reconciler
    goes on as if that side were idle.

    What remains, in plain terms:

    * Only one side still live -- it wins. Offline work flows up; a session the web
      started (or the mirror has not yet caught) flows down.
    * Both live and the *same* session -- :func:`merge_current` folds them together
      so nothing recorded on either side is lost.
    * Both live and *different* sessions (or a same-session merge that could not be
      built) -- the more recent wins and the loser is stashed, never dropped.
    """
    clear_local = local is not None and is_archived(local.get("id"))
    clear_remote = remote is not None and is_archived(remote.get("id"))
    live_local = None if clear_local else local
    live_remote = None if clear_remote else remote

    if not live_local and not live_remote:
        return CurrentPlan("none", clear_local=clear_local, clear_remote=clear_remote)
    if live_local and not live_remote:
        return CurrentPlan("push", clear_local=clear_local, clear_remote=clear_remote)
    if live_remote and not live_local:
        return CurrentPlan("pull", clear_local=clear_local, clear_remote=clear_remote)

    assert live_local is not None and live_remote is not None
    merged = merge_current(live_local, live_remote)
    if merged is not None:
        return CurrentPlan("merge", merged=merged)

    # Two genuinely different live sessions: the more recent stays, the other is
    # set aside so no session with real time in it is ever silently discarded.
    local_activity = latest_activity(live_local)
    remote_activity = latest_activity(live_remote)
    local_wins = remote_activity is None or (
        local_activity is not None and local_activity >= remote_activity
    )
    if local_wins:
        return CurrentPlan("push", stash=live_remote)
    return CurrentPlan("pull", stash=live_local)


# ---------------------------------------------------------------------------
# synchronisation
# ---------------------------------------------------------------------------


def is_pending(root: Path) -> bool:
    """True if commands ran locally while offline and a reconcile is owed."""
    return (root / SYNC_PENDING).exists()


def mark_pending(root: Path) -> None:
    """Record that a local, offline write happened and must be folded back in."""
    root.mkdir(parents=True, exist_ok=True)
    (root / SYNC_PENDING).touch()


def clear_pending(root: Path) -> None:
    """Forget the owed reconcile -- called once it has been done."""
    _remove(root / SYNC_PENDING)


def note_offline(root: Path) -> None:
    """Record that the VM was just found unreachable, starting the cooldown."""
    root.mkdir(parents=True, exist_ok=True)
    (root / OFFLINE_SINCE).write_text(str(now().timestamp()), encoding="utf-8")


def clear_offline(root: Path) -> None:
    """Forget the last failure -- called the moment the VM answers again."""
    _remove(root / OFFLINE_SINCE)


def offline_recent(root: Path, cooldown: float = _OFFLINE_COOLDOWN) -> bool:
    """True if the VM failed to answer within the last ``cooldown`` seconds.

    While this holds, a command skips the connection attempt and serves from
    local immediately, so a stretch of offline polls costs nothing rather than one
    full connect-timeout each. A garbled or absent marker simply means "try the
    VM", which fails safe toward doing the real check.
    """
    try:
        since = float((root / OFFLINE_SINCE).read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError, OSError):
        return False
    return (now().timestamp() - since) < cooldown


def note_synced(root: Path) -> None:
    """Record that the local files were just folded together with the VM."""
    root.mkdir(parents=True, exist_ok=True)
    (root / SYNCED_AT).write_text(str(now().timestamp()), encoding="utf-8")


def synced_recently(root: Path, window: float = _SYNC_FRESH) -> bool:
    """True if a sync completed within the last ``window`` seconds.

    While it holds, an *idle* sync kick -- one with nothing pending to push -- skips
    the VM entirely, so the once-a-second kicks do not each reach across the network
    for nothing. A garbled or absent marker means "not fresh", which fails safe
    toward doing the sync.
    """
    try:
        since = float((root / SYNCED_AT).read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError, OSError):
        return False
    return (now().timestamp() - since) < window


def _remove(path: Path) -> None:
    """Delete ``path`` if it exists; a missing file is already the goal."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _read_local_current(root: Path) -> Optional[Dict[str, object]]:
    """This machine's live session as a dict, or ``None`` if idle/unreadable."""
    try:
        payload = read_json(root / "current.json")
    except FileNotFoundError:
        return None
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _stash(root: Path, session: Dict[str, object], err: TextIO) -> None:
    """Set a conflicting live session aside under ``conflicts/``, never delete it.

    Reached only in the rare case where both this machine (offline) and the VM
    (driven meanwhile from elsewhere) held a *different* live session. The loser
    is preserved here, and said out loud, so no worked time is ever lost to a
    reconciliation -- only moved somewhere you can recover it from.
    """
    stash_dir = root / "conflicts"
    stash_dir.mkdir(parents=True, exist_ok=True)

    session_id = session.get("id")
    stem = session_id if isinstance(session_id, str) else now().strftime("%Y-%m-%d_%H-%M-%S")
    target = stash_dir / f"{stem}.json"
    suffix = 2
    while target.exists():
        target = stash_dir / f"{stem}-{suffix}.json"
        suffix += 1

    atomic_write_json(target, session)
    print(f"note: a conflicting live session was set aside at {target}", file=err)


def _is_archived_locally(root: Path) -> Callable[[object], bool]:
    """A predicate: does this id already have an archive under ``sessions/``?

    Built after archives have been unioned both ways, so an archive written by a
    ``stop`` on *either* side is visible here -- which is what lets a stopped-then-
    lingering ``current.json`` be told apart from a session that is genuinely live.
    """

    def archived(session_id: object) -> bool:
        return isinstance(session_id, str) and (root / "sessions" / f"{session_id}.json").exists()

    return archived


def flush_local(remote: "Remote", root: Path, err: TextIO) -> bool:
    """Fold this machine's offline work back together with the VM. Idempotent.

    Returns True if the VM was reachable and the reconcile ran, False if it was
    still offline. Archived sessions union in both directions and are never
    overwritten; the live session is settled by :func:`resolve_current` -- a stop
    on either side is cleared, the same session edited on both is merged, and the
    loser of a genuine two-session clash is stashed by :func:`_stash`, never
    dropped.
    """
    if not remote.reachable():
        return False

    # Union the archives first, so the stop-detection below sees a stop made on
    # either side, and no completed day is ever overwritten.
    remote.archives_down(root)
    remote.archives_up(root)

    local = _read_local_current(root)
    remote_current = remote.read_current()
    plan = resolve_current(local, remote_current, _is_archived_locally(root))

    if plan.stash is not None:
        _stash(root, plan.stash, err)

    if plan.action == "merge" and plan.merged is not None:
        atomic_write_json(root / "current.json", plan.merged)
        remote.push_current(root)
    elif plan.action == "push":
        remote.push_current(root)
    elif plan.action == "pull":
        remote.pull_current(root)  # handles a VM gone idle by deleting the local file
    else:  # "none": nothing live to move; honour any stop left behind below.
        if plan.clear_local:
            _remove(root / "current.json")
        if plan.clear_remote:
            remote.clear_current()

    return True


def refresh_local(remote: "Remote", root: Path) -> None:
    """Mirror the VM down: pull the archives it has and this machine lacks, and the
    one small ``current.json``.

    This is the *nothing-pending* path -- the Mac has made no un-synced change, so
    the VM is the truth, and copying it down is what makes a pause, resume, stop or
    start done from the web surface on the widget. It never pushes, so it can only
    make the local files agree with the VM, never the other way.
    """
    remote.archives_down(root)
    remote.pull_current(root)


def background_sync(remote: "Remote", root: Path, err: TextIO) -> None:
    """One turn of the background syncer: reconcile with the VM, or note it is gone.

    Called by the detached ``sync`` command -- after every local command and on the
    widget's poll -- so it must be cheap in the common case and never raise. The
    order is chosen so the once-a-second kicks mostly cost nothing:

    * nothing owed and the mirror is fresh -> return without touching the network;
    * the VM was just found unreachable -> wait out the cooldown, return;
    * probe it once (this also warms the multiplexed connection). Unreachable ->
      note it and return, to be retried by the next kick;
    * otherwise, if there is offline work to fold in, :func:`flush_local` does the
      reconcile; if not, :func:`refresh_local` mirrors the VM down. Either way the
      sync is stamped, so the next idle kick can skip it.
    """
    pending = is_pending(root)
    if not pending and synced_recently(root):
        return
    if offline_recent(root):
        return
    if not remote.reachable():
        note_offline(root)
        return

    clear_offline(root)
    if pending:
        if flush_local(remote, root, err):
            clear_pending(root)
    else:
        refresh_local(remote, root)
    note_synced(root)


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------


def remote_from_env(environ: Optional[Dict[str, str]] = None) -> Optional[Remote]:
    """Build a :class:`Remote` from the environment, or ``None`` if none is set.

    ``WORK_TRACKER_SSH`` unset is the switch in its off position: the CLI is
    purely local, exactly as before this module existed.
    """
    env = environ if environ is not None else os.environ
    destination = (env.get("WORK_TRACKER_SSH") or "").strip()
    if not destination:
        return None
    path = (env.get("WORK_TRACKER_SSH_PATH") or "work-tracker").strip()
    key = (env.get("WORK_TRACKER_SSH_KEY") or "").strip() or None
    return Remote(destination=destination, path=path, key=key)
