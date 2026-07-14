"""Low-level helpers shared across the package.

This module deliberately contains no knowledge of the tracker's domain model.
It provides three groups of utilities:

* time helpers   -- timezone-aware "now", ISO-8601 formatting and parsing;
* format helpers -- human readable durations;
* io helpers     -- atomic JSON writes and defensive JSON reads.

Everything here is pure or filesystem-only, which keeps it trivially testable.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

__all__ = [
    "CorruptJSONError",
    "TrackerError",
    "atomic_write_json",
    "format_duration",
    "format_timestamp",
    "now",
    "parse_timestamp",
    "read_json",
]


#: Seconds are the smallest unit we ever persist; sub-second precision would
#: only add noise to the JSON files and to every comparison in the tests.
_TIMESPEC: Final[str] = "seconds"


class TrackerError(Exception):
    """Base class for every error the tracker raises deliberately.

    The CLI catches exactly this type and turns it into a clean message plus a
    non-zero exit code. Anything that escapes it is a genuine bug and is allowed
    to print a traceback.
    """


class CorruptJSONError(TrackerError):
    """Raised when a JSON file exists but cannot be decoded or has a bad shape.

    The tracker treats JSON as its only source of truth, so a malformed file is
    an unrecoverable condition that must be reported loudly rather than being
    silently repaired.
    """


# ---------------------------------------------------------------------------
# time
# ---------------------------------------------------------------------------


def now() -> datetime:
    """Return the current local time as a timezone-aware ``datetime``.

    ``datetime.now()`` alone yields a naive value; attaching the system's
    current UTC offset via :meth:`~datetime.datetime.astimezone` makes every
    timestamp in the system unambiguous and safely comparable.

    Sub-second precision is discarded so that a value produced here round-trips
    exactly through :func:`format_timestamp` and :func:`parse_timestamp`.
    """
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0)


def format_timestamp(moment: datetime) -> str:
    """Serialise ``moment`` as an ISO-8601 string with an explicit UTC offset.

    Args:
        moment: A timezone-aware datetime.

    Returns:
        For example ``"2026-07-14T19:42:18+03:00"``.

    Raises:
        ValueError: If ``moment`` is naive. Persisting a naive timestamp would
            make the stored data depend on the machine that wrote it.
    """
    if moment.tzinfo is None:
        raise ValueError("refusing to serialise a naive datetime")
    return moment.isoformat(timespec=_TIMESPEC)


def parse_timestamp(raw: str) -> datetime:
    """Parse an ISO-8601 timestamp produced by :func:`format_timestamp`.

    Args:
        raw: The string to parse.

    Returns:
        A timezone-aware datetime.

    Raises:
        CorruptJSONError: If ``raw`` is not a string, is not valid ISO-8601, or
            carries no UTC offset.
    """
    if not isinstance(raw, str):
        raise CorruptJSONError(f"expected an ISO-8601 string, got {type(raw).__name__}")

    # Python 3.9's fromisoformat rejects a trailing 'Z' (3.11+ accepts it). We
    # never write one, but a hand-edited or imported file may contain one, so
    # normalise it and behave identically on every supported interpreter.
    text = raw[:-1] + "+00:00" if raw.endswith("Z") else raw

    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise CorruptJSONError(f"invalid ISO-8601 timestamp: {raw!r}") from exc
    if moment.tzinfo is None:
        raise CorruptJSONError(f"timestamp is missing a UTC offset: {raw!r}")
    return moment


def format_duration(seconds: int) -> str:
    """Render a duration as ``H:MM:SS`` (hours are never zero-padded).

    Negative input is clamped to zero: a negative duration can only come from a
    corrupt file or a clock that jumped backwards, and neither is worth
    surfacing as a nonsensical ``-0:01:00`` in the status output.
    """
    seconds = max(0, seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


# ---------------------------------------------------------------------------
# io
# ---------------------------------------------------------------------------


def read_json(path: Path) -> Any:
    """Read and decode the JSON document at ``path``.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        CorruptJSONError: If the file cannot be read or decoded.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise CorruptJSONError(f"cannot read {path}: {exc}") from exc

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise CorruptJSONError(f"{path} is not valid JSON: {exc}") from exc


def atomic_write_json(path: Path, payload: Any) -> None:
    """Write ``payload`` to ``path`` as JSON, atomically.

    The write goes to a temporary file in the *same directory* as the target,
    which guarantees both files live on one filesystem and therefore that
    :func:`os.replace` is a genuine atomic rename rather than a copy. The
    sequence is:

    1. write the full document to a temporary file;
    2. ``flush`` + ``fsync`` so the bytes are on the platter, not just in the
       page cache;
    3. ``os.replace`` the temporary file over the target.

    A reader therefore only ever observes the complete old document or the
    complete new one -- never a truncated file, even if the process is killed
    or the machine loses power mid-write.

    Raises:
        OSError: If the directory cannot be created or the file cannot be
            written or renamed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    # ``delete=False`` because we hand ownership of the file to os.replace.
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        # Never leave a stray ".current.json.xxxx.tmp" behind on failure.
        temp_path.unlink(missing_ok=True)
        raise
