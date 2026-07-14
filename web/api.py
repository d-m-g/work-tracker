"""Builds the JSON payloads the React app consumes.

Everything here is a pure function of a :class:`~tracker.storage.Storage` (and a
clock). There is no HTTP, no socket and no global state, so every endpoint's
response can be asserted in a unit test without binding a port.

Two endpoints:

``GET /api/status``    what is happening right now (or ``idle``).
``GET /api/sessions``  every archived session, newest first, plus totals.
"""

from __future__ import annotations

from typing import Any, Dict, List

from tracker.models import SessionState
from tracker.storage import Storage
from tracker.tracker import Clock, WorkTracker
from tracker.utils import CorruptJSONError, format_timestamp, now

__all__ = ["build_sessions_payload", "build_status_payload"]


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
