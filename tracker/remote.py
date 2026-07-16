"""Driving the tracker on another machine over SSH, with an offline fallback.

The tracker was born as one writer of local files. A viewer on a VM turned it
into something you can also reach from a phone, and this module closes the loop:
it lets *this* machine's CLI -- and therefore the widget and the Shortcuts that
shell out to it -- drive the tracker **on the VM** instead of here, so everything
writes to one place, the one the web viewer reads.

The design keeps the tracker's founding promise (one writer, no second
implementation of the rules) intact by not writing a second implementation at
all. When a remote is configured, the CLI runs *the very same* ``tracker.py`` on
the VM over SSH and prints back what it said. ``--json status`` there is the
``--json status`` here; ``stop`` prints the same sentences. There is nothing to
drift, because there is nothing new to be right or wrong.

Three behaviours, in order of how often they happen:

* **Online (the common path).** The command runs on the VM over a *persistent,
  multiplexed* SSH connection, so the widget's once-a-second status poll reuses
  one connection rather than paying for a handshake each time. This is the whole
  reason the poll can be remote without hammering either end.

* **Offline (the fallback).** If the VM cannot be reached, the command runs
  locally exactly as it always did, and a ``.sync_pending`` marker is dropped so
  the next reconnection knows there is local work to fold back in.

* **Reconnect (the reconciliation).** Before the next *online* command, if the
  marker is set, local and remote are merged -- once -- and the marker cleared.
  Archived sessions union in both directions and are never overwritten; the live
  session is decided by whichever side has the more recent activity, and a
  genuine two-sided conflict is stashed, never deleted. The heavy work happens
  here, at reconnection, and nowhere near the per-second poll.

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
from pathlib import Path
from typing import Dict, List, Optional, TextIO, Tuple

from .utils import atomic_write_json, now, parse_timestamp, read_json

#: Absolute paths to the tools we shell out to. Resolved once, with a fallback to
#: the standard macOS location, because a GUI app (the widget) spawns us with a
#: minimal PATH -- and a bare "ssh" that could not be found would look exactly
#: like a VM that could not be reached, silently pinning us offline forever.
_SSH_BIN = shutil.which("ssh") or "/usr/bin/ssh"
_RSYNC_BIN = shutil.which("rsync") or "/usr/bin/rsync"

__all__ = [
    "Remote",
    "SYNC_PENDING",
    "clear_offline",
    "clear_pending",
    "is_pending",
    "latest_activity",
    "mark_pending",
    "note_offline",
    "offline_recent",
    "reconcile_current",
    "refresh_local",
    "remote_from_env",
    "synchronise",
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

#: ssh's own exit code for "could not even connect". The remote CLI's exits (0, 1,
#: 2) all mean the command *reached* the VM and answered; only 255 means the
#: transport failed, and only that should trigger the offline fallback.
SSH_UNREACHABLE = 255

#: How long a connection attempt may hang before we call it offline. Short, so a
#: dropped network turns into a local fallback quickly rather than a frozen widget.
_CONNECT_TIMEOUT = 3

#: How long the multiplexed master connection lingers after the last use. Long
#: enough that a burst of once-a-second polls shares one connection; short enough
#: that it does not sit open forever.
_CONTROL_PERSIST = "60s"


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
            )
        except OSError as exc:
            # ssh itself missing or unrunnable: treat as unreachable, fall back.
            return SSH_UNREACHABLE, "", str(exc)
        return result.returncode, result.stdout, result.stderr

    def run(self, argv: List[str]) -> Tuple[int, str, str]:
        """Run ``tracker.py argv`` on the VM. Returns (exit, stdout, stderr).

        The exit code is the remote CLI's own -- so a wrong-state refusal on the
        VM comes back as its exit 1 and its message, indistinguishable from
        running it here -- *except* for :data:`SSH_UNREACHABLE`, which means the
        VM was never reached and the caller should fall back to local.
        """
        # `cd` into the repo so tracker.py's own path resolution works, then exec
        # the interpreter macOS-and-Ubuntu both ship. shlex.quote keeps a task
        # with a space or a quote in it an argument, never shell.
        inner = "cd {} && exec /usr/bin/python3 tracker.py {}".format(
            shlex.quote(self.path),
            " ".join(shlex.quote(a) for a in argv),
        )
        return self._exec(inner)

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
            return subprocess.run(command, capture_output=True, text=True).returncode == 0
        except OSError:
            return False

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


def reconcile_current(
    local: Optional[Dict[str, object]],
    remote: Optional[Dict[str, object]],
) -> Tuple[str, bool]:
    """Decide whose live session survives a reconnection.

    Returns ``(winner, conflict)`` where ``winner`` is ``"local"``, ``"remote"``
    or ``"none"`` (both idle), and ``conflict`` is True only when both sides hold
    a *different* live session and one had to be set aside.

    The rules, in plain terms:

    * If only one side has a live session, it wins -- offline work flows up, and a
      session started on the phone flows down.
    * If both hold the *same* session (same id), local wins: while the VM was
      unreachable, this machine is the only one that could have changed it (a
      pause, a task edit), so its copy is the newer truth.
    * If both hold *different* sessions, the one with the more recent activity
      wins and the other is stashed rather than dropped -- never silently discard
      a session that has real time in it. This is the only ``conflict``.
    """
    if not local and not remote:
        return "none", False
    if local and not remote:
        return "local", False
    if remote and not local:
        return "remote", False

    assert local is not None and remote is not None
    if local.get("id") == remote.get("id"):
        return "local", False

    local_activity = latest_activity(local)
    remote_activity = latest_activity(remote)
    if remote_activity is None:
        return "local", True
    if local_activity is None:
        return "remote", True
    winner = "local" if local_activity >= remote_activity else "remote"
    return winner, True


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


def synchronise(remote: "Remote", root: Path, err: TextIO) -> bool:
    """Fold local and remote back together after a reconnection. Idempotent.

    Returns True if the VM was reachable and the merge ran, False if it was still
    offline and nothing changed. Archived sessions union in both directions and
    are never overwritten; the live session is settled by
    :func:`reconcile_current`, and the losing side of a genuine conflict is
    stashed by :func:`_stash`, never dropped.
    """
    if not remote.reachable():
        return False

    remote.archives_down(root)
    remote.archives_up(root)

    local = _read_local_current(root)
    remote_current = remote.read_current()
    winner, conflict = reconcile_current(local, remote_current)

    if winner == "local":
        if conflict and remote_current is not None:
            _stash(root, remote_current, err)
        remote.push_current(root)
    elif winner == "remote":
        if conflict and local is not None:
            _stash(root, local, err)
        remote.pull_current(root)
    # "none": both idle, nothing to move.
    return True


def refresh_local(remote: "Remote", root: Path) -> None:
    """Keep the local files a warm offline mirror after an online write.

    Cheap by design: it pulls only archives the VM has and this machine lacks, and
    the one small ``current.json``. Called after a *write* the VM performed --
    never on a status poll -- so the cost lands a few times a day, not once a
    second. It is what makes a sudden drop to offline pick up from the right
    place instead of a stale one.
    """
    remote.archives_down(root)
    remote.pull_current(root)


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
