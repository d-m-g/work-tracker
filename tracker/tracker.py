"""Service layer: every operation the tracker performs.

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
so an externally edited (or deleted) file is picked up immediately. That is also
what lets there be more than one caller -- a Shortcut, the widget, the CLI, the
browser -- without any of them holding a stale idea of the state: each one reads
the truth, acts on it, and writes atomically.
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .models import (
    MAX_TASK_LENGTH,
    ActiveSession,
    CompletedSession,
    Pause,
    SessionState,
    _ValueEnum,
    normalise_task,
    validate_session_id,
)
from .storage import NoSuchSessionError, SessionExistsError, Storage
from .utils import CorruptJSONError, TrackerError, now

__all__ = [
    "DayTotal",
    "InvalidTaskError",
    "NoActiveSessionError",
    "Report",
    "SessionAlreadyRunningError",
    "Status",
    "ToggleAction",
    "ToggleResult",
    "WorkTracker",
    "WrongStateError",
    "checked_task",
    "checked_task_id",
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


class InvalidTaskError(TrackerError):
    """Raised when a task label offered by a caller is refused."""


def checked_task(raw: Any) -> str | None:
    """Normalise a task label offered by a *caller*, or refuse it.

    Strict about what is accepted, lenient about what is already held: a label
    typed at the CLI or posted from the browser must be text of a sane length,
    while a file on disk that already carries a longer one still loads. See
    :data:`~tracker.models.MAX_TASK_LENGTH` for why the two directions differ.

    Raises:
        InvalidTaskError: If ``raw`` is not text, or is longer than the cap.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise InvalidTaskError(f"a task must be text, got {type(raw).__name__}")

    task = normalise_task(raw)
    if task is not None and len(task) > MAX_TASK_LENGTH:
        raise InvalidTaskError(
            f"a task may be at most {MAX_TASK_LENGTH} characters "
            f"-- this one is {len(task)}"
        )
    return task


def checked_task_id(raw: Any) -> str | None:
    """A planner task id offered by a caller: short text without spaces, or ``None``.

    Raises:
        InvalidTaskError: If ``raw`` is anything else.
    """
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw or len(raw) > 64 or raw != "".join(raw.split()):
        raise InvalidTaskError(f"a task id must be short text without spaces, got {raw!r}")
    return raw


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
    task: str | None = None
    task_id: str | None = None
    project: str | None = None

    @property
    def is_active(self) -> bool:
        """Whether a session is currently in progress."""
        return self.state is not None


@dataclass(frozen=True)
class DayTotal:
    """One day's worth of work inside a :class:`Report`."""

    day: date
    worked_seconds: int
    session_count: int
    tasks: tuple[str, ...]


@dataclass(frozen=True)
class Report:
    """The work done inside one calendar period, as reported by ``report``.

    ``days`` holds only the days on which something was worked, oldest first;
    a day absent from it is a day off. Rendering lives in the CLI -- this is
    data, not text.
    """

    period: str  # "week" or "month"
    start: date
    end: date  # inclusive
    days: tuple[DayTotal, ...]
    worked_seconds: int
    session_count: int


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

    def start(
        self, task: str | None = None, task_id: str | None = None, project: str | None = None
    ) -> ActiveSession:
        """Begin a new session, optionally naming what it is for.

        Args:
            task: What you are about to work on. Optional, and never required:
                an unlabelled session is a perfectly good session, and the label
                can always be written -- or corrected -- later.

        Returns:
            The session that was created.

        Raises:
            InvalidTaskError: If ``task`` is not text, or is too long.
            SessionAlreadyRunningError: If a session is already in progress.
            CorruptJSONError: If ``current.json`` exists but is unreadable.
            StorageError: On filesystem failure.
        """
        session = ActiveSession.begin(
            self._clock(),
            task=checked_task(task),
            task_id=checked_task_id(task_id),
            project=checked_task(project),
        )
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

    def toggle(
        self, task: str | None = None, task_id: str | None = None, project: str | None = None
    ) -> ToggleResult:
        """Advance the session by one step, whatever step the state calls for.

        Idle starts, running pauses, paused resumes -- the play/pause button the
        Shortcut binds to a single key. ``stop`` is deliberately not reachable
        from here: ending a day's session should stay a deliberate act, not
        something a mistyped key can do.

        The state is read once to choose the operation, and the operation then
        re-reads it before acting. A session that changed underneath us in that
        window therefore raises rather than acting on a stale reading.

        Args:
            task: Used only if this toggle turns out to be a *start*. A toggle
                that pauses or resumes leaves the label alone -- coming back from
                lunch does not change what you are working on, and a toggle bound
                to a key has nothing to ask you anyway.

        Returns:
            What was done, the resulting session, and -- when resuming -- the
            pause that just closed.

        Raises:
            InvalidTaskError: If ``task`` is not text, or is too long.
            CorruptJSONError: If ``current.json`` exists but is unreadable.
            SessionAlreadyRunningError: If a session appeared while we chose.
            WrongStateError: If the state changed while we chose.
            StorageError: On filesystem failure.
        """
        session = self._storage.load_current()

        if session is None:
            return ToggleResult(ToggleAction.STARTED, self.start(task, task_id, project))

        if session.state is SessionState.RUNNING:
            return ToggleResult(ToggleAction.PAUSED, self.pause())

        resumed, pause = self.resume()
        return ToggleResult(ToggleAction.RESUMED, resumed, pause)

    def set_task(
        self, task: str | None, task_id: str | None = None, project: str | None = None
    ) -> ActiveSession:
        """Write down what the running session is being spent on.

        Legal in either state, running or paused: what you are working on is not
        a fact about the clock, and noting it down while you are away from the
        desk is a perfectly reasonable thing to want to do.

        Args:
            task: The label. ``None`` (or a blank string) clears it.

        Returns:
            The updated session.

        Raises:
            InvalidTaskError: If ``task`` is not text, or is too long.
            NoActiveSessionError: If no session is in progress.
            StorageError: On filesystem failure.
        """
        session = self._require_active()
        session.set_task(checked_task(task), checked_task_id(task_id), checked_task(project))
        self._storage.save_current(session)
        return session

    def set_archived_task(self, session_id: str, task: str | None) -> CompletedSession:
        """Write down what an *already finished* session was spent on.

        The one amendment the tracker permits to a day it has archived. Nothing
        it touches is a measurement: every timestamp and every duration in the
        file still comes from what actually happened, and is rewritten unchanged.

        Args:
            session_id: Which archived session.
            task: The label. ``None`` (or a blank string) clears it.

        Returns:
            The updated session.

        Raises:
            InvalidTaskError: If ``task`` is not text, or is too long.
            NoSuchSessionError: If no archive with that id exists.
            CorruptJSONError: If the archive exists but is unreadable.
            StorageError: On filesystem failure.
        """
        label = checked_task(task)
        updated = self.archived(session_id).with_task(label)
        self._storage.update_session(updated)
        return updated

    # -- queries ------------------------------------------------------------

    def task(self) -> str | None:
        """What the session in progress is being spent on, if anything.

        Raises:
            NoActiveSessionError: If no session is in progress.
        """
        return self._require_active().task

    def archived(self, session_id: str) -> CompletedSession:
        """Load one archived session by id.

        Raises:
            NoSuchSessionError: If no archive with that id exists.
            CorruptJSONError: If the archive exists but is unreadable.
        """
        return self._storage.load_session(self._storage.session_path(self._archive_id(session_id)))

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
            task=session.task,
            task_id=session.task_id,
            project=session.project,
        )

    def report(self, period: str, on: date | None = None) -> Report:
        """Total the work done in the week or month containing ``on``.

        A week runs Monday to Sunday; a month is the calendar month. Sessions
        are attributed whole to the day they *started* on, and the session in
        progress counts too -- today's work belongs in this week's report even
        before the day is stopped.

        Args:
            period: ``"week"`` or ``"month"``.
            on: Any day inside the period wanted. Defaults to today, i.e. the
                current week or month.

        Returns:
            The per-day totals and the period's overall total.

        Raises:
            ValueError: If ``period`` is neither ``"week"`` nor ``"month"``.
            CorruptJSONError: If an archive inside the period is unreadable.
        """
        moment = self._clock()
        anchor = on if on is not None else moment.date()

        if period == "week":
            start = anchor - timedelta(days=anchor.weekday())
            end = start + timedelta(days=6)
        elif period == "month":
            start = anchor.replace(day=1)
            end = anchor.replace(day=monthrange(anchor.year, anchor.month)[1])
        else:
            raise ValueError(f"unknown report period: {period!r}")

        worked: dict[date, int] = {}
        count: dict[date, int] = {}
        tasks: dict[date, list[str]] = {}

        def add(day: date, seconds: int, task: str | None) -> None:
            worked[day] = worked.get(day, 0) + seconds
            count[day] = count.get(day, 0) + 1
            if task and task not in tasks.setdefault(day, []):
                tasks[day].append(task)

        for path in self._storage.list_sessions():
            # An id begins with its start date, so a file outside the period
            # is skipped by name alone, without being opened.
            if not (str(start) <= path.stem[:10] <= str(end)):
                continue
            session = self._storage.load_session(path)
            add(session.start.date(), session.worked_seconds, session.task)

        current = self._storage.load_current()
        if current is not None and start <= current.start.date() <= end:
            add(current.start.date(), current.worked_seconds(moment), current.task)

        return Report(
            period=period,
            start=start,
            end=end,
            days=tuple(
                DayTotal(day, worked[day], count[day], tuple(tasks.get(day, ())))
                for day in sorted(worked)
            ),
            worked_seconds=sum(worked.values()),
            session_count=sum(count.values()),
        )

    # -- internals ----------------------------------------------------------

    def _require_active(self) -> ActiveSession:
        """Load the active session or raise :class:`NoActiveSessionError`."""
        session = self._storage.load_current()
        if session is None:
            raise NoActiveSessionError("no session is in progress -- run 'start' first")
        return session

    def _archive_id(self, session_id: str) -> str:
        """Return ``session_id`` if it names an archive that exists, else raise.

        Session ids reach this method from *outside* a file -- typed at the CLI,
        posted from the browser -- so this is the choke point where one is checked
        before it is ever allowed to become a path. A malformed id and an id that
        names nothing are reported identically, and neither reaches the disk: from
        the caller's side both mean the same thing, that there is no such session.

        Raises:
            NoSuchSessionError: If the id is malformed, or names no archive.
        """
        try:
            valid_id = validate_session_id(session_id)
        except CorruptJSONError as exc:
            raise NoSuchSessionError(f"no such session: {session_id!r}") from exc

        if not self._storage.has_session(valid_id):
            raise NoSuchSessionError(f"no such session: {valid_id}")
        return valid_id
