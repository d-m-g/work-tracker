"""Domain model: the dataclasses that JSON on disk maps onto.

The model layer owns two things and nothing else:

* the *shape* of the persisted documents (``to_dict`` / ``from_dict``);
* the arithmetic that turns raw timestamps into durations.

It performs no I/O, so every rule encoded here can be tested with plain
in-memory values.

Two documents exist:

``ActiveSession``
    Mirrors ``current.json``. It exists only while a session is in progress and
    is deleted when the session stops.

``CompletedSession``
    Mirrors one file in ``sessions/``. It is written once and never mutated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Final

from .utils import CorruptJSONError, format_timestamp, parse_timestamp

__all__ = [
    "ActiveSession",
    "CompletedSession",
    "Pause",
    "SessionState",
    "SessionStatus",
]


#: Session identifiers double as filenames, so they must be filesystem-safe.
#: Validating on read stops a hand-edited or hostile ``id`` from escaping the
#: ``sessions/`` directory when it is used to build a path.
_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$")

#: Format used to derive a session id from its start time.
_ID_FORMAT: Final[str] = "%Y-%m-%d_%H-%M-%S"


class _ValueEnum(str, Enum):
    """A string enum whose text form is always its *value*.

    ``enum.StrEnum`` would be the natural choice, but it needs Python 3.11 and
    this package targets the interpreter macOS ships (3.9), so that the Shortcuts
    can call ``/usr/bin/python3`` and never break.

    ``__str__`` is pinned deliberately: Python 3.11 changed how a mixin enum
    formats itself, so a bare ``f"{state}"`` would render as ``running`` on 3.9
    but ``SessionState.RUNNING`` on 3.12+. Defining it here makes the rendered
    output identical on every supported interpreter.
    """

    def __str__(self) -> str:
        return str(self.value)


class SessionState(_ValueEnum):
    """The state of the session described by ``current.json``."""

    RUNNING = "running"
    PAUSED = "paused"


class SessionStatus(_ValueEnum):
    """The terminal status of a session archived in ``sessions/``.

    Only ``COMPLETED`` is produced today. The field exists so that future
    outcomes (an abandoned or auto-closed session, say) can be added without
    changing the on-disk schema.
    """

    COMPLETED = "completed"


# ---------------------------------------------------------------------------
# helpers shared by the from_dict implementations
# ---------------------------------------------------------------------------


def _require_mapping(raw: Any, what: str) -> dict[str, Any]:
    """Return ``raw`` as a dict or raise :class:`CorruptJSONError`."""
    if not isinstance(raw, dict):
        raise CorruptJSONError(f"{what} must be a JSON object, got {type(raw).__name__}")
    return raw


def _require_key(payload: dict[str, Any], key: str, what: str) -> Any:
    """Return ``payload[key]`` or raise :class:`CorruptJSONError`."""
    if key not in payload:
        raise CorruptJSONError(f"{what} is missing the required key {key!r}")
    return payload[key]


def _validate_id(raw: Any) -> str:
    """Return ``raw`` if it is a well-formed session id, else raise."""
    if not isinstance(raw, str) or not _ID_PATTERN.match(raw):
        raise CorruptJSONError(f"malformed session id: {raw!r}")
    return raw


# ---------------------------------------------------------------------------
# pause
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Pause:
    """A single completed pause interval.

    Every pause is stored separately rather than being folded into a running
    total, so a session's history stays fully auditable after the fact.

    ``seconds`` is stored redundantly: it is derivable from ``start`` and
    ``end``, but persisting it keeps the JSON readable without a calculator and
    lets external consumers sum pauses without parsing timestamps.
    """

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("a pause cannot end before it starts")

    @property
    def seconds(self) -> int:
        """Duration of the pause, in whole seconds."""
        return int((self.end - self.start).total_seconds())

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the persisted representation."""
        return {
            "start": format_timestamp(self.start),
            "end": format_timestamp(self.end),
            "seconds": self.seconds,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "Pause":
        """Rebuild a :class:`Pause` from its persisted representation.

        The stored ``seconds`` value is intentionally *not* trusted: it is
        recomputed from the timestamps, which remain the source of truth.

        Raises:
            CorruptJSONError: If the object is malformed.
        """
        payload = _require_mapping(raw, "pause")
        try:
            return cls(
                start=parse_timestamp(_require_key(payload, "start", "pause")),
                end=parse_timestamp(_require_key(payload, "end", "pause")),
            )
        except ValueError as exc:
            raise CorruptJSONError(f"invalid pause: {exc}") from exc


def _pauses_from_list(raw: Any) -> list[Pause]:
    """Parse the ``pauses`` array."""
    if not isinstance(raw, list):
        raise CorruptJSONError(f"'pauses' must be an array, got {type(raw).__name__}")
    return [Pause.from_dict(item) for item in raw]


# ---------------------------------------------------------------------------
# active session (current.json)
# ---------------------------------------------------------------------------


@dataclass
class ActiveSession:
    """The in-progress session, persisted as ``current.json``.

    Invariant, enforced in :meth:`__post_init__`: ``pause_start`` is set if and
    only if the state is ``PAUSED``. Every state transition on this object is a
    method, so the invariant cannot be broken from the outside without going
    through validation.
    """

    id: str
    start: datetime
    state: SessionState = SessionState.RUNNING
    pause_start: datetime | None = None
    pauses: list[Pause] = field(default_factory=list)

    def __post_init__(self) -> None:
        is_paused = self.state is SessionState.PAUSED
        if is_paused and self.pause_start is None:
            raise CorruptJSONError("session is paused but 'pauseStart' is null")
        if not is_paused and self.pause_start is not None:
            raise CorruptJSONError("session is running but 'pauseStart' is set")

    # -- construction -------------------------------------------------------

    @classmethod
    def begin(cls, moment: datetime) -> "ActiveSession":
        """Start a brand-new session at ``moment``."""
        return cls(id=moment.strftime(_ID_FORMAT), start=moment, state=SessionState.RUNNING)

    # -- transitions --------------------------------------------------------

    def pause(self, moment: datetime) -> None:
        """Move from ``RUNNING`` to ``PAUSED``.

        Raises:
            ValueError: If the session is not running.
        """
        if self.state is not SessionState.RUNNING:
            raise ValueError("session is not running")
        self.state = SessionState.PAUSED
        self.pause_start = moment

    def resume(self, moment: datetime) -> Pause:
        """Move from ``PAUSED`` back to ``RUNNING``.

        The pause that just ended is appended to :attr:`pauses` and returned.

        Raises:
            ValueError: If the session is not paused.
        """
        if self.state is not SessionState.PAUSED or self.pause_start is None:
            raise ValueError("session is not paused")
        completed = Pause(start=self.pause_start, end=moment)
        self.pauses.append(completed)
        self.pause_start = None
        self.state = SessionState.RUNNING
        return completed

    # -- derived values -----------------------------------------------------

    def gross_seconds(self, moment: datetime) -> int:
        """Wall-clock seconds since the session started."""
        return max(0, int((moment - self.start).total_seconds()))

    def paused_seconds(self, moment: datetime) -> int:
        """Total paused seconds, *including* a pause still in progress.

        Counting the open pause is what makes ``status`` report a paused
        session's numbers as frozen: while paused, gross and paused time grow in
        lockstep, so worked time stands still.
        """
        total = sum(pause.seconds for pause in self.pauses)
        if self.pause_start is not None:
            total += max(0, int((moment - self.pause_start).total_seconds()))
        return total

    def worked_seconds(self, moment: datetime) -> int:
        """Gross seconds minus paused seconds, clamped at zero."""
        return max(0, self.gross_seconds(moment) - self.paused_seconds(moment))

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the ``current.json`` representation."""
        return {
            "state": self.state.value,
            "id": self.id,
            "start": format_timestamp(self.start),
            "pauseStart": format_timestamp(self.pause_start) if self.pause_start else None,
            "pauses": [pause.to_dict() for pause in self.pauses],
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "ActiveSession":
        """Rebuild an :class:`ActiveSession` from ``current.json``.

        Raises:
            CorruptJSONError: If the document is malformed.
        """
        payload = _require_mapping(raw, "current.json")

        state_raw = _require_key(payload, "state", "current.json")
        try:
            state = SessionState(state_raw)
        except ValueError as exc:
            raise CorruptJSONError(f"unknown session state: {state_raw!r}") from exc

        pause_start_raw = payload.get("pauseStart")
        pause_start = parse_timestamp(pause_start_raw) if pause_start_raw is not None else None

        return cls(
            id=_validate_id(_require_key(payload, "id", "current.json")),
            start=parse_timestamp(_require_key(payload, "start", "current.json")),
            state=state,
            pause_start=pause_start,
            pauses=_pauses_from_list(payload.get("pauses", [])),
        )


# ---------------------------------------------------------------------------
# completed session (sessions/<id>.json)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompletedSession:
    """An archived session, persisted as one immutable file in ``sessions/``."""

    id: str
    start: datetime
    end: datetime
    gross_seconds: int
    paused_seconds: int
    worked_seconds: int
    pauses: list[Pause] = field(default_factory=list)
    status: SessionStatus = SessionStatus.COMPLETED

    @classmethod
    def from_active(cls, session: ActiveSession, end: datetime) -> "CompletedSession":
        """Close ``session`` at ``end`` and compute its totals.

        The caller is responsible for closing any pause still in progress first
        (see :meth:`ActiveSession.resume`), which keeps the arithmetic here to a
        single, obvious subtraction.
        """
        gross = session.gross_seconds(end)
        paused = session.paused_seconds(end)
        return cls(
            id=session.id,
            start=session.start,
            end=end,
            gross_seconds=gross,
            paused_seconds=paused,
            worked_seconds=max(0, gross - paused),
            pauses=list(session.pauses),
            status=SessionStatus.COMPLETED,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the archived representation."""
        return {
            "id": self.id,
            "start": format_timestamp(self.start),
            "end": format_timestamp(self.end),
            "status": self.status.value,
            "grossSeconds": self.gross_seconds,
            "pausedSeconds": self.paused_seconds,
            "workedSeconds": self.worked_seconds,
            "pauses": [pause.to_dict() for pause in self.pauses],
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "CompletedSession":
        """Rebuild a :class:`CompletedSession` from an archived file.

        Raises:
            CorruptJSONError: If the document is malformed.
        """
        payload = _require_mapping(raw, "session")

        status_raw = payload.get("status", SessionStatus.COMPLETED.value)
        try:
            status = SessionStatus(status_raw)
        except ValueError as exc:
            raise CorruptJSONError(f"unknown session status: {status_raw!r}") from exc

        def _seconds(key: str) -> int:
            value = _require_key(payload, key, "session")
            # bool is an int subclass; reject it explicitly.
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise CorruptJSONError(f"{key!r} must be a non-negative integer, got {value!r}")
            return value

        return cls(
            id=_validate_id(_require_key(payload, "id", "session")),
            start=parse_timestamp(_require_key(payload, "start", "session")),
            end=parse_timestamp(_require_key(payload, "end", "session")),
            gross_seconds=_seconds("grossSeconds"),
            paused_seconds=_seconds("pausedSeconds"),
            worked_seconds=_seconds("workedSeconds"),
            pauses=_pauses_from_list(payload.get("pauses", [])),
            status=status,
        )
