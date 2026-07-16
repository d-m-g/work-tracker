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
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final, TextIO

from .models import ActiveSession, Pause, SessionState
from .remote import (
    SSH_UNREACHABLE,
    clear_offline,
    clear_pending,
    is_pending,
    mark_pending,
    note_offline,
    offline_recent,
    refresh_local,
    remote_from_env,
    synchronise,
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

    # With a remote configured the command runs on the VM; without one, or when
    # the VM cannot be reached, it runs here exactly as it always has.
    remote = remote_from_env()
    if remote is not None:
        return _run_remote(remote, root, raw_argv, args, out, err)
    return _run_local(root, args, out, err)


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


def _strip_root(raw_argv: Sequence[str]) -> list[str]:
    """Drop any ``--root`` from the arguments forwarded to the VM.

    ``--root`` names a directory on *this* machine; the VM has its own data
    directory and must not be pointed at a path that means nothing there. Every
    other argument -- ``--json``, the command, a task with spaces in it -- is
    forwarded untouched.
    """
    forwarded: list[str] = []
    skip = False
    for token in raw_argv:
        if skip:
            skip = False
            continue
        if token == "--root":
            skip = True  # also drop its value, the next token
            continue
        if token.startswith("--root="):
            continue
        forwarded.append(token)
    return forwarded


def _run_remote(
    remote: object,
    root: Path,
    raw_argv: Sequence[str],
    args: argparse.Namespace,
    out: TextIO,
    err: TextIO,
) -> int:
    """Run one command on the VM, falling back to local if it cannot be reached.

    ``status`` is a read and needs no reconciliation; every other command is a
    write. On reconnection -- the first command after an offline stretch -- the
    owed merge runs once before the command, and after a successful online write
    the local files are refreshed so a later drop to offline resumes from the
    right place.
    """
    is_write = args.command != "status"

    def fall_back_to_local() -> int:
        """Run here and, for a write, remember to reconcile on reconnection."""
        result = _run_local(root, args, out, err)
        if is_write:
            mark_pending(root)
        return result

    # Just failed to reach the VM? Don't pay the connect timeout again yet --
    # answer from local at once. The cooldown re-probes on its own before long.
    if offline_recent(root):
        return fall_back_to_local()

    # Reconnecting: fold in anything done offline before trusting the VM again.
    # A failed sync means we are still offline, so note it and serve local.
    if is_pending(root):
        if synchronise(remote, root, err):  # type: ignore[arg-type]
            clear_pending(root)
            clear_offline(root)
        else:
            note_offline(root)
            return fall_back_to_local()

    exit_code, remote_out, remote_err = remote.run(_strip_root(raw_argv))  # type: ignore[attr-defined]

    if exit_code == SSH_UNREACHABLE:
        note_offline(root)
        return fall_back_to_local()

    # Online: relay exactly what the VM said, then keep local a warm mirror.
    clear_offline(root)
    if remote_out:
        out.write(remote_out)
    if remote_err:
        err.write(remote_err)
    if is_write and exit_code == EXIT_OK:
        refresh_local(remote, root)  # type: ignore[arg-type]
    return exit_code
