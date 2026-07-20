"""Command-line interface.

This module owns *presentation* and nothing else: parsing arguments, rendering
results as text or JSON, and mapping exceptions onto exit codes. All behaviour
lives in :class:`tracker.tracker.WorkTracker`.

Exit codes
----------
``0``
    The command succeeded.
``1``
    A :class:`~tracker.utils.TrackerError` -- a condition the tracker
    anticipates and reports (wrong state, corrupt file, unwritable directory).
``2``
    Bad usage -- an unknown command, or a contradictory one such as
    ``task "docs" --clear``. Produced by :mod:`argparse` itself.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final, Optional, TextIO

from .models import ActiveSession, Pause, SessionState
from .remote import (
    Remote,
    background_sync,
    mark_pending,
    remote_from_env,
)
from .storage import Storage
from .tracker import Status, ToggleAction, ToggleResult, WorkTracker
from .utils import TrackerError, format_duration, format_timestamp

__all__ = ["build_parser", "main"]


#: Overrides where the tracker keeps its data. Useful for testing and for
#: pointing a Shortcut at a repository checked out somewhere unusual.
ENV_ROOT: Final[str] = "WORK_TRACKER_HOME"

EXIT_OK: Final[int] = 0
EXIT_ERROR: Final[int] = 1


def default_root() -> Path:
    """Return the data directory: ``$WORK_TRACKER_HOME`` or the repository root.

    The repository root is the parent of this package, which makes the tracker
    work out of the box from any working directory -- important, because macOS
    Shortcuts run commands from an unpredictable one.
    """
    override = os.environ.get(ENV_ROOT)
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def _render_started(session: ActiveSession) -> str:
    """Render what ``start`` just did."""
    line = f"Started session {session.id} at {format_timestamp(session.start)}."
    return f"{line}\nTask:    {session.task}" if session.task else line


def _render_paused(session: ActiveSession) -> str:
    """Render what ``pause`` just did."""
    assert session.pause_start is not None  # the session is paused
    return f"Paused at {format_timestamp(session.pause_start)}."


def _render_resumed(pause: Pause) -> str:
    """Render what ``resume`` just did, given the pause it closed."""
    return (
        f"Resumed at {format_timestamp(pause.end)} "
        f"after {format_duration(pause.seconds)} paused."
    )


def _render_toggle(result: ToggleResult) -> str:
    """Render a toggle as whichever of the three sentences above applies."""
    if result.action is ToggleAction.STARTED:
        return _render_started(result.session)
    if result.action is ToggleAction.PAUSED:
        return _render_paused(result.session)

    assert result.pause is not None  # a resume always closes a pause
    return _render_resumed(result.pause)


def _render_status(status: Status) -> str:
    """Render a :class:`Status` as the human-readable ``status`` output."""
    if not status.is_active:
        return "State:   idle (no session in progress)"

    open_pause = " (one in progress)" if status.state is SessionState.PAUSED else ""
    assert status.start is not None  # guaranteed whenever a session is active

    lines = [
        f"State:   {status.state}",
        f"Session: {status.session_id}",
        f"Started: {format_timestamp(status.start)}",
    ]
    # An unlabelled session prints no Task line at all, rather than an empty one:
    # a blank field invites you to read it as "the task is nothing".
    if status.task:
        lines.append(f"Task:    {status.task}")
    lines += [
        f"Worked:  {format_duration(status.worked_seconds)}",
        f"Paused:  {format_duration(status.paused_seconds)}",
        f"Pauses:  {status.pause_count}{open_pause}",
    ]
    return "\n".join(lines)


def _status_payload(status: Status) -> dict[str, object]:
    """Render a :class:`Status` as a JSON-serialisable dict (for ``--json``)."""
    return {
        "state": str(status.state) if status.state else "idle",
        "id": status.session_id,
        "start": format_timestamp(status.start) if status.start else None,
        "task": status.task,
        "workedSeconds": status.worked_seconds,
        "pausedSeconds": status.paused_seconds,
        "pauses": status.pause_count,
    }


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def _cmd_start(tracker: WorkTracker, args: argparse.Namespace, out: TextIO) -> int:
    session = tracker.start(args.task)
    if args.json:
        json.dump(session.to_dict(), out, indent=2)
        out.write("\n")
    else:
        print(_render_started(session), file=out)
    return EXIT_OK


def _cmd_pause(tracker: WorkTracker, args: argparse.Namespace, out: TextIO) -> int:
    session = tracker.pause()
    if args.json:
        json.dump(session.to_dict(), out, indent=2)
        out.write("\n")
    else:
        print(_render_paused(session), file=out)
    return EXIT_OK


def _cmd_resume(tracker: WorkTracker, args: argparse.Namespace, out: TextIO) -> int:
    session, pause = tracker.resume()
    if args.json:
        json.dump(session.to_dict(), out, indent=2)
        out.write("\n")
    else:
        print(_render_resumed(pause), file=out)
    return EXIT_OK


def _cmd_toggle(tracker: WorkTracker, args: argparse.Namespace, out: TextIO) -> int:
    result = tracker.toggle(args.task)
    if args.json:
        # The action is what distinguishes this command's output from start's,
        # pause's or resume's, so it leads; the session document follows.
        json.dump({"action": str(result.action), **result.session.to_dict()}, out, indent=2)
        out.write("\n")
    else:
        print(_render_toggle(result), file=out)
    return EXIT_OK


def _cmd_stop(tracker: WorkTracker, args: argparse.Namespace, out: TextIO) -> int:
    completed, path = tracker.stop()
    if args.json:
        json.dump(completed.to_dict(), out, indent=2)
        out.write("\n")
    else:
        lines = [f"Stopped session {completed.id}."]
        if completed.task:
            lines.append(f"Task:    {completed.task}")
        lines += [
            f"Worked:  {format_duration(completed.worked_seconds)}",
            f"Paused:  {format_duration(completed.paused_seconds)}"
            f" across {len(completed.pauses)} pause(s)",
            f"Gross:   {format_duration(completed.gross_seconds)}",
            f"Saved:   {path}",
        ]
        print("\n".join(lines), file=out)
    return EXIT_OK


def _cmd_status(tracker: WorkTracker, args: argparse.Namespace, out: TextIO) -> int:
    status = tracker.status()
    if args.json:
        json.dump(_status_payload(status), out, indent=2)
        out.write("\n")
    else:
        print(_render_status(status), file=out)
    return EXIT_OK


def _cmd_task(tracker: WorkTracker, args: argparse.Namespace, out: TextIO) -> int:
    """Read, set or clear what a session is being spent on.

    One command in three moods, because they are one question asked three ways::

        tracker.py task                            what am I working on?
        tracker.py task "rewriting the parser"     this.
        tracker.py task --clear                    never mind.

    ``--session ID`` points any of the three at a day already archived, which is
    how you label the one you forgot to label at the time.

    A label and ``--clear`` ask for opposite things; the parser makes them
    mutually exclusive, so that contradiction is refused as bad usage rather than
    resolved by quietly discarding one of them.
    """
    setting = args.clear or args.text is not None
    label = None if args.clear else args.text

    if args.session is not None:
        session = (
            tracker.set_archived_task(args.session, label)
            if setting
            else tracker.archived(args.session)
        )
        task = session.task
    elif setting:
        task = tracker.set_task(label).task
    else:
        task = tracker.task()

    if args.json:
        json.dump({"task": task}, out, indent=2)
        out.write("\n")
    else:
        # "Nothing written down" is a real answer and deserves saying out loud,
        # rather than being printed as a blank line you are left to interpret.
        print(task if task else "(no task recorded)", file=out)
    return EXIT_OK


#: Maps a subcommand name to its handler. Adding a command means adding a row
#: here and a parser below -- no branching logic to touch.
_COMMANDS: Final[dict[str, object]] = {
    "start": _cmd_start,
    "pause": _cmd_pause,
    "resume": _cmd_resume,
    "toggle": _cmd_toggle,
    "stop": _cmd_stop,
    "status": _cmd_status,
    "task": _cmd_task,
}


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def _add_start_arguments(parser: argparse.ArgumentParser) -> None:
    """``--task`` for ``start``, and for the ``toggle`` that turns out to start."""
    parser.add_argument(
        "--task",
        default=None,
        metavar="TEXT",
        help="what you are working on (optional; can be set or changed later)",
    )


def _add_task_arguments(parser: argparse.ArgumentParser) -> None:
    """The ``task`` command's three moods, and the session it points at."""
    parser.add_argument(
        "--session",
        default=None,
        metavar="ID",
        help="act on an archived session instead of the one in progress",
    )
    # Mutually exclusive, so 'task "docs" --clear' is refused as bad usage (exit 2)
    # rather than resolved by quietly throwing away one of the two.
    mood = parser.add_mutually_exclusive_group()
    mood.add_argument(
        "text",
        nargs="?",
        default=None,
        help="the task to record; omit to print the one already recorded",
    )
    mood.add_argument("--clear", action="store_true", help="remove the recorded task")


#: Extra arguments, by subcommand. A command absent from here takes none.
_ARGUMENTS: Final[dict[str, object]] = {
    "start": _add_start_arguments,
    "toggle": _add_start_arguments,
    "task": _add_task_arguments,
}


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="tracker",
        description="A local, dependency-free work time tracker.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        metavar="DIR",
        help=f"data directory (default: ${ENV_ROOT}, else the repository root)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the result as JSON instead of text",
    )

    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    descriptions = {
        "start": "begin a new session (fails if one is already in progress)",
        "pause": "pause the running session",
        "resume": "resume the paused session",
        "toggle": "start, pause or resume, whichever the current state calls for",
        "stop": "end the session and archive it under sessions/",
        "status": "show the current state, worked time, paused time and pause count",
        "task": "show, set or clear what a session is being spent on",
        "sync": "(internal) reconcile the local files with the VM in the background",
    }
    for name, help_text in descriptions.items():
        subparser = subparsers.add_parser(name, help=help_text, description=help_text)
        add_arguments = _ARGUMENTS.get(name)
        if add_arguments is not None:
            add_arguments(subparser)  # type: ignore[operator]

    return parser


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def main(
    argv: Sequence[str] | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Run the CLI and return a process exit code.

    Streams are injectable so the whole interface can be exercised in-process by
    the tests, with no subprocesses and no captured global state.

    Args:
        argv: Arguments *without* the program name. Defaults to ``sys.argv[1:]``.
        out: Where results are written. Defaults to ``sys.stdout``.
        err: Where errors are written. Defaults to ``sys.stderr``.

    Returns:
        ``0`` on success, ``1`` on an anticipated failure.
    """
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr

    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(raw_argv)

    root: Path = args.root if args.root is not None else default_root()

    # ``sync`` is the background half of local-first: it never runs a user's
    # command, it only reconciles the local files with the VM, so it is dispatched
    # here rather than through the local/remote split (and never kicks itself).
    remote = remote_from_env()
    if args.command == "sync":
        return _cmd_sync(remote, root, err)

    # With a VM configured every command still runs against the *local* files --
    # instantly, offline or not -- and a detached sync folds them up afterwards.
    # With no VM it is the purely-local tracker it has always been.
    if remote is None:
        return _run_local(root, args, out, err)
    return _run_remote(root, args, out, err)


def _run_local(
    root: Path, args: argparse.Namespace, out: TextIO, err: TextIO
) -> int:
    """Run one command against the local files -- the original behaviour."""
    tracker = WorkTracker(Storage(root))
    handler = _COMMANDS[args.command]
    try:
        return handler(tracker, args, out)  # type: ignore[operator]
    except TrackerError as exc:
        # Anticipated: report it as a one-line message, not a traceback.
        print(f"error: {exc}", file=err)
        return EXIT_ERROR
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        print("interrupted", file=err)
        return EXIT_ERROR


#: The launcher this package sits beside, spawned for the detached background sync.
#: Resolved from this file's location, not the data root, because the *script* is
#: always here even when ``--root`` points the *data* somewhere else.
_SYNC_SCRIPT: Final[Path] = Path(__file__).resolve().parent.parent / "tracker.py"


def _run_remote(root: Path, args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    """Act locally now; sync to the VM in the background.

    This is the whole of local-first. The command runs against the local files
    exactly as it does with no VM configured -- so it is instant, and works with the
    network gone -- and only two cheap, non-blocking things are added: a write drops
    a ``.sync_pending`` marker, and every command kicks a detached ``sync`` that
    folds the local files together with the VM out of process. Nothing here touches
    the network, so a dead connection can never make the caller wait.
    """
    result = _run_local(root, args, out, err)
    if args.command != "status":  # a write: there is local work to push up
        mark_pending(root)
    _kick_sync(root)
    return result


def _kick_sync(root: Path) -> None:
    """Fire off a detached, best-effort background sync and return at once.

    stdout, stderr and stdin go to ``/dev/null`` and the child starts its own
    session, for two reasons that both matter: it must not hold the parent's stdout
    pipe open -- the widget reads that pipe to the end, and would block until the
    sync finished -- and it must outlive the parent, so a one-shot Shortcut or a
    finished status poll does not take the sync down with it.
    """
    try:
        subprocess.Popen(
            [sys.executable, str(_SYNC_SCRIPT), "--root", str(root), "sync"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except OSError:
        pass  # best-effort: a failed kick just means the next command tries again


def _cmd_sync(remote: Optional[Remote], root: Path, err: TextIO) -> int:
    """Reconcile the local files with the VM, best-effort. Always exits 0.

    The background half of local-first, run detached by :func:`_kick_sync` after
    every command and by the widget's poll. With no VM configured there is nothing
    to do. A non-blocking lock keeps the once-a-second kicks from piling onto one
    another: if a sync already holds it, this one steps aside and lets that one
    finish the work. Every failure is swallowed -- a button must never be handed a
    sync error it did not cause.
    """
    if remote is None:
        return EXIT_OK

    root.mkdir(parents=True, exist_ok=True)
    try:
        lock_fd = os.open(root / ".sync_lock", os.O_CREAT | os.O_WRONLY, 0o644)
    except OSError:
        return EXIT_OK
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return EXIT_OK  # another sync is already running; let it do the work
        try:
            background_sync(remote, root, err)
        except Exception:  # a best-effort daemon must not die on an odd failure
            pass
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        os.close(lock_fd)
    return EXIT_OK
