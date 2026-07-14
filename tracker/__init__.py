"""A local, dependency-free work time tracker.

Layering, from the bottom up -- each layer only knows about the ones below it:

``utils``    time, formatting and atomic-JSON helpers; no domain knowledge.
``models``   the dataclasses that mirror the JSON documents, plus the duration
             arithmetic. Pure; performs no I/O.
``storage``  the only module that touches the filesystem.
``tracker``  the service layer: the six operations, over an injectable clock
             and storage.
``cli``      argument parsing, rendering and exit codes.

The public surface is re-exported here so that embedding the tracker in another
program is a one-line import::

    from tracker import Storage, WorkTracker

    tracker = WorkTracker(Storage(Path("~/work-tracker").expanduser()))
    tracker.start()
"""

from __future__ import annotations

from .models import ActiveSession, CompletedSession, Pause, SessionState, SessionStatus
from .storage import SessionExistsError, Storage, StorageError
from .tracker import (
    NoActiveSessionError,
    SessionAlreadyRunningError,
    Status,
    ToggleAction,
    ToggleResult,
    WorkTracker,
    WrongStateError,
)
from .utils import CorruptJSONError, TrackerError

__version__ = "1.0.0"

__all__ = [
    "ActiveSession",
    "CompletedSession",
    "CorruptJSONError",
    "NoActiveSessionError",
    "Pause",
    "SessionAlreadyRunningError",
    "SessionExistsError",
    "SessionState",
    "SessionStatus",
    "Status",
    "Storage",
    "StorageError",
    "ToggleAction",
    "ToggleResult",
    "TrackerError",
    "WorkTracker",
    "WrongStateError",
    "__version__",
]
