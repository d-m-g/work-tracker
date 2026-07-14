"""Service layer: the six operations the CLI exposes.

:class:`WorkTracker` is the seam the whole design turns on. It receives its
:class:`~tracker.storage.Storage` and its *clock* from the caller, so tests can
drive it against a temporary directory and a fake clock and assert on exact
durations without sleeping.

Each operation follows the same shape:

1. load the current state from disk (the single source of truth);
2. reject the operation if that state does not allow it;
3. mutate the domain object;
4. persist, then return a report describing what happened.

Nothing is cached between calls: an operation always re-reads ``current.json``,
so an externally edited (or deleted) file is picked up immediately.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .models import ActiveSession, CompletedSession, Pause, SessionState, _ValueEnum
from .storage import SessionExistsError, Storage
from .utils import TrackerError, now

__all__ = [
    "NoActiveSessionError",
    "SessionAlreadyRunningError",
    "Status",
    "ToggleAction",
    "ToggleResult",
    "WorkTracker",
    "WrongStateError",
]


#: A clock is any zero-argument callable returning an aware datetime. The real
#: one is :func:`tracker.utils.now`; tests pass a deterministic stand-in.
Clock = Callable[[], datetime]


class NoActiveSessionError(TrackerError):
    """Raised when an operation needs a session in progress and there is none."""


class SessionAlreadyRunningError(TrackerError):
    """Raised by :meth:`WorkTracker.start` when a session is already in progress."""


class WrongStateError(TrackerError):
    """Raised when the session exists but is in the wrong state for the operation."""


class ToggleAction(_ValueEnum):
    """What :meth:`WorkTracker.toggle` decided to do.

    It reuses ``models._ValueEnum`` so that ``str()`` renders the value itself on
    every interpreter the tracker supports -- see that class for why that matters.
    """

    STARTED = "started"
    PAUSED = "paused"
    RESUMED = "resumed"


@dataclass(frozen=True)
class ToggleResult:
    """The outcome of a :meth:`WorkTracker.toggle`.

    ``pause`` is the pause that just closed, and is set only when ``action`` is
    ``RESUMED``; there is no closed pause to report in the other two cases.
    """

    action: ToggleAction
    session: ActiveSession
    pause: Pause | None = None


@dataclass(frozen=True)
class Status:
    """A point-in-time snapshot of the tracker, as reported by ``status``.

    ``state`` is ``None`` when no session is in progress; every duration is then
    zero. Rendering lives in the CLI -- this is data, not text.
    """

    state: SessionState | None
    session_id: str | None = None
    start: datetime | None = None
    worked_seconds: int = 0
    paused_seconds: int = 0
    pause_count: int = 0

    @property
    def is_active(self) -> bool:
        """Whether a session is currently in progress."""
        return self.state is not None


class WorkTracker:
    """Coordinates the session state machine over a :class:`Storage`.

    Args:
        storage: Where sessions are read from and written to.
        clock: Returns the current timezone-aware time. Defaults to the system
            clock; inject a stub in tests.
    """

    __slots__ = ("_clock", "_storage")

    def __init__(self, storage: Storage, clock: Clock = now) -> None:
        self._storage = storage
        self._clock = clock

    # -- commands -----------------------------------------------------------

    def start(self) -> ActiveSession:
        """Begin a new session.

        Returns:
            The session that was created.

        Raises:
            SessionAlreadyRunningError: If a session is already in progress.
            CorruptJSONError: If ``current.json`` exists but is unreadable.
            StorageError: On filesystem failure.
        """
        session = ActiveSession.begin(self._clock())
        try:
            # Atomic create: this *is* the "fail if current.json already exists"
            # check, not a follow-up to a separate one.
            self._storage.create_current(session)
        except SessionExistsError as exc:
            raise SessionAlreadyRunningError(
                "a session is already in progress -- run 'stop' before starting a new one"
            ) from exc
        return session

    def pause(self) -> ActiveSession:
        """Pause the running session.

        Returns:
            The updated session.

        Raises:
            NoActiveSessionError: If no session is in progress.
            WrongStateError: If the session is already paused.
            StorageError: On filesystem failure.
        """
        session = self._require_active()
        if session.state is not SessionState.RUNNING:
            raise WrongStateError("the session is already paused")

        session.pause(self._clock())
        self._storage.save_current(session)
        return session

    def resume(self) -> tuple[ActiveSession, Pause]:
        """Resume the paused session.

        Returns:
            The updated session and the pause that just closed.

        Raises:
            NoActiveSessionError: If no session is in progress.
            WrongStateError: If the session is not paused.
            StorageError: On filesystem failure.
        """
        session = self._require_active()
        if session.state is not SessionState.PAUSED:
            raise WrongStateError("the session is not paused")

        completed = session.resume(self._clock())
        self._storage.save_current(session)
        return session, completed

    def stop(self) -> tuple[CompletedSession, Path]:
        """End the session, archive it, and remove ``current.json``.

        A session that is still paused has its open pause closed automatically at
        the stop time, so ``pausedSeconds`` accounts for every paused second.

        The archive is written *before* ``current.json`` is deleted. If the
        process dies between the two steps the worst case is a stale
        ``current.json`` next to a complete archive -- recoverable by hand. The
        opposite order could destroy the session outright.

        Returns:
            The archived session and the path it was written to.

        Raises:
            NoActiveSessionError: If no session is in progress.
            SessionExistsError: If an archive with this id already exists.
            StorageError: On filesystem failure.
        """
        session = self._require_active()
        end = self._clock()

        if session.state is SessionState.PAUSED:
            session.resume(end)

        completed = CompletedSession.from_active(session, end)
        path = self._storage.archive(completed)
        self._storage.delete_current()
        return completed, path

    def toggle(self) -> ToggleResult:
        """Advance the session by one step, whatever step the state calls for.

        Idle starts, running pauses, paused resumes -- the play/pause button the
        Shortcut binds to a single key. ``stop`` is deliberately not reachable
        from here: ending a day's session should stay a deliberate act, not
        something a mistyped key can do.

        The state is read once to choose the operation, and the operation then
        re-reads it before acting. A session that changed underneath us in that
        window therefore raises rather than acting on a stale reading.

        Returns:
            What was done, the resulting session, and -- when resuming -- the
            pause that just closed.

        Raises:
            CorruptJSONError: If ``current.json`` exists but is unreadable.
            SessionAlreadyRunningError: If a session appeared while we chose.
            WrongStateError: If the state changed while we chose.
            StorageError: On filesystem failure.
        """
        session = self._storage.load_current()

        if session is None:
            return ToggleResult(ToggleAction.STARTED, self.start())

        if session.state is SessionState.RUNNING:
            return ToggleResult(ToggleAction.PAUSED, self.pause())

        resumed, pause = self.resume()
        return ToggleResult(ToggleAction.RESUMED, resumed, pause)

    # -- queries ------------------------------------------------------------

    def status(self) -> Status:
        """Describe the tracker right now. Never raises on an idle tracker.

        Raises:
            CorruptJSONError: If ``current.json`` exists but is unreadable.
        """
        session = self._storage.load_current()
        if session is None:
            return Status(state=None)

        moment = self._clock()
        return Status(
            state=session.state,
            session_id=session.id,
            start=session.start,
            worked_seconds=session.worked_seconds(moment),
            paused_seconds=session.paused_seconds(moment),
            pause_count=len(session.pauses),
        )

    # -- internals ----------------------------------------------------------

    def _require_active(self) -> ActiveSession:
        """Load the active session or raise :class:`NoActiveSessionError`."""
        session = self._storage.load_current()
        if session is None:
            raise NoActiveSessionError("no session is in progress -- run 'start' first")
        return session
