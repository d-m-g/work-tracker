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
    Bad usage. Produced by :mod:`argparse` itself.
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
    return f"Started session {session.id} at {format_timestamp(session.start)}."


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

    return "\n".join(
        [
            f"State:   {status.state}",
            f"Session: {status.session_id}",
            f"Started: {format_timestamp(status.start)}",
            f"Worked:  {format_duration(status.worked_seconds)}",
            f"Paused:  {format_duration(status.paused_seconds)}",
            f"Pauses:  {status.pause_count}{open_pause}",
        ]
    )


def _status_payload(status: Status) -> dict[str, object]:
    """Render a :class:`Status` as a JSON-serialisable dict (for ``--json``)."""
    return {
        "state": str(status.state) if status.state else "idle",
        "id": status.session_id,
        "start": format_timestamp(status.start) if status.start else None,
        "workedSeconds": status.worked_seconds,
        "pausedSeconds": status.paused_seconds,
        "pauses": status.pause_count,
    }


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def _cmd_start(tracker: WorkTracker, args: argparse.Namespace, out: TextIO) -> int:
    session = tracker.start()
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
    result = tracker.toggle()
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
        print(
            "\n".join(
                [
                    f"Stopped session {completed.id}.",
                    f"Worked:  {format_duration(completed.worked_seconds)}",
                    f"Paused:  {format_duration(completed.paused_seconds)}"
                    f" across {len(completed.pauses)} pause(s)",
                    f"Gross:   {format_duration(completed.gross_seconds)}",
                    f"Saved:   {path}",
                ]
            ),
            file=out,
        )
    return EXIT_OK


def _cmd_status(tracker: WorkTracker, args: argparse.Namespace, out: TextIO) -> int:
    status = tracker.status()
    if args.json:
        json.dump(_status_payload(status), out, indent=2)
        out.write("\n")
    else:
        print(_render_status(status), file=out)
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
}


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


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
    }
    for name, help_text in descriptions.items():
        subparsers.add_parser(name, help=help_text, description=help_text)

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

    parser = build_parser()
    args = parser.parse_args(argv)

    root: Path = args.root if args.root is not None else default_root()
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
