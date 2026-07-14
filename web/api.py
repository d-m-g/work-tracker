"""Builds the JSON payloads the React app consumes, and runs the commands it sends.

Everything here is a function of a :class:`~tracker.storage.Storage` (and a
clock). There is no HTTP, no socket and no global state, so every endpoint --
including the ones that write -- can be asserted in a unit test without binding a
port.

The reads:

``GET /api/status``    what is happening right now (or ``idle``).
``GET /api/sessions``  every archived session, newest first, plus totals.

The writes, all through :func:`run_command`:

``POST /api/start``    begin a session, optionally naming its task.
``POST /api/pause``    pause the running session.
``POST /api/resume``   resume the paused session.
``POST /api/toggle``   whichever of those three the state calls for.
``POST /api/stop``     end the session and archive it.
``POST /api/task``     write down what a session (live or archived) was spent on.

Nothing here is a second implementation of anything. Every command is a single
call into :class:`~tracker.tracker.WorkTracker` -- the same object the CLI and the
Shortcuts drive -- so the browser is one more caller of the one writer, not a
writer of its own. It cannot invent a state transition the CLI would refuse, and
it cannot compute a duration the CLI would disagree with.
"""

from __future__ import annotations

from typing import Any, Dict, List

from tracker.models import SessionState
from tracker.storage import Storage
from tracker.tracker import Clock, InvalidTaskError, WorkTracker
from tracker.utils import CorruptJSONError, TrackerError, format_timestamp, now

__all__ = [
    "COMMANDS",
    "UnknownCommandError",
    "build_sessions_payload",
    "build_status_payload",
    "run_command",
]


#: Every command the browser is allowed to send. A name absent from this set does
#: not reach the tracker: the UI cannot reach an operation by guessing at a URL.
COMMANDS = frozenset({"start", "pause", "resume", "toggle", "stop", "task"})


class UnknownCommandError(TrackerError):
    """Raised when a POST names a command that does not exist."""


def build_status_payload(storage: Storage, clock: Clock = now) -> Dict[str, Any]:
    """Describe the live session, or report an idle tracker.

    The durations are computed server-side at request time, so the browser never
    has to reason about clocks or timezones -- it polls and renders whatever it
    is told.

    Returns:
        A JSON-serialisable dict. ``state`` is ``"running"``, ``"paused"`` or
        ``"idle"``.
    """
    tracker = WorkTracker(storage, clock=clock)
    status = tracker.status()

    if not status.is_active:
        return {
            "state": "idle",
            "id": None,
            "start": None,
            "task": None,
            "grossSeconds": 0,
            "workedSeconds": 0,
            "pausedSeconds": 0,
            "pauseCount": 0,
            "pauseInProgress": False,
            "pauseStart": None,
            "pauses": [],
        }

    session = storage.load_current()
    assert session is not None  # status.is_active says so

    return {
        "state": str(status.state),
        "id": status.session_id,
        "start": format_timestamp(status.start) if status.start else None,
        "task": status.task,
        # gross = worked + paused, so the UI can derive the session's current
        # end as start + gross. That keeps the timeline anchored to the server's
        # clock instead of the browser's, which may be seconds adrift.
        "grossSeconds": status.worked_seconds + status.paused_seconds,
        "workedSeconds": status.worked_seconds,
        "pausedSeconds": status.paused_seconds,
        "pauseCount": status.pause_count,
        # The UI dims the live clock while a pause is open, so it needs to know a
        # pause is running even though pauseCount only counts finished ones.
        "pauseInProgress": status.state is SessionState.PAUSED,
        # The open pause has no end yet; the UI draws it as a gap running up to
        # 'now'. Sent separately from `pauses`, which only holds closed ones.
        "pauseStart": (
            format_timestamp(session.pause_start) if session.pause_start else None
        ),
        # Every finished pause, so the live session can be drawn as a strip with
        # its gaps punched out -- the same shape as an archived session.
        "pauses": [pause.to_dict() for pause in session.pauses],
    }


def build_sessions_payload(storage: Storage) -> Dict[str, Any]:
    """List every archived session, newest first, with aggregate totals.

    A session file that cannot be parsed does not sink the whole response. It is
    reported in ``unreadable`` and the rest are still returned -- one corrupt
    file should not blank out a year of history. The UI surfaces those entries
    rather than hiding them, because a file the tracker cannot read is exactly
    the thing you want to be told about.
    """
    sessions: List[Dict[str, Any]] = []
    unreadable: List[Dict[str, str]] = []

    for path in storage.list_sessions():
        try:
            sessions.append(storage.load_session(path).to_dict())
        except CorruptJSONError as exc:
            unreadable.append({"file": path.name, "error": str(exc)})

    # list_sessions() is oldest-first; the UI wants the most recent day on top.
    sessions.reverse()

    return {
        "sessions": sessions,
        "unreadable": unreadable,
        "totals": {
            "count": len(sessions),
            "workedSeconds": sum(item["workedSeconds"] for item in sessions),
            "pausedSeconds": sum(item["pausedSeconds"] for item in sessions),
        },
    }


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def _task_of(body: Dict[str, Any]) -> Any:
    """Pull the task out of a request body, without interpreting it.

    The value is handed to the tracker exactly as it arrived, because the tracker
    is what decides whether a task is acceptable (see
    :func:`tracker.tracker.checked_task`). A missing key and an explicit ``null``
    both mean "no task", which for ``task`` is how the UI clears one: emptying the
    box is not a different operation from clearing it.
    """
    return body.get("task")


def run_command(
    storage: Storage,
    command: str,
    body: Dict[str, Any],
    clock: Clock = now,
) -> Dict[str, Any]:
    """Perform one write command and report the state it left behind.

    Every command answers in the same shape: what it did, plus the full status
    payload as read back *afterwards*. The browser therefore renders what the
    server actually sees, never what the click optimistically assumed -- so a
    button press that raced a Shortcut, or a widget, shows the real outcome rather
    than a hopeful one, and the next poll has nothing to correct.

    ``stop`` additionally returns the session it archived, which is what lets the
    UI drop the finished day straight into its history.

    Args:
        storage: Where the session lives.
        command: One of :data:`COMMANDS`.
        body: The decoded JSON request body. ``{}`` if there was none.
        clock: Injectable, exactly as for the tracker itself.

    Returns:
        ``{"action": ..., "status": {...}}``, plus ``"session"`` for a ``stop``.

    Raises:
        UnknownCommandError: If ``command`` is not one of :data:`COMMANDS`.
        TrackerError: Whatever the operation itself refuses with -- a wrong state,
            a task that is too long, a session id that names nothing.
    """
    if command not in COMMANDS:
        raise UnknownCommandError(f"no such command: {command!r}")

    tracker = WorkTracker(storage, clock=clock)
    result: Dict[str, Any] = {"action": command}

    if command == "start":
        tracker.start(_task_of(body))
    elif command == "pause":
        tracker.pause()
    elif command == "resume":
        tracker.resume()
    elif command == "toggle":
        # The action a toggle *chose* is the interesting half of its answer:
        # "started", "paused" or "resumed" is what the UI announces.
        result["action"] = str(tracker.toggle(_task_of(body)).action)
    elif command == "stop":
        completed, _path = tracker.stop()
        result["session"] = completed.to_dict()
    elif command == "task":
        session_id = body.get("id")
        if session_id is None:
            tracker.set_task(_task_of(body))
        elif isinstance(session_id, str):
            result["session"] = tracker.set_archived_task(session_id, _task_of(body)).to_dict()
        else:
            raise InvalidTaskError(f"a session id must be text, got {type(session_id).__name__}")

    result["status"] = build_status_payload(storage, clock=clock)
    return result
