"""Persistence layer: the only module that touches the filesystem.

:class:`Storage` maps the domain objects in :mod:`tracker.models` onto files:

* ``<root>/current.json``          -- the active session, if any;
* ``<root>/sessions/<id>.json``    -- one file per completed session.

Keeping every path and every read/write behind this class means the service
layer above can be tested against a temporary directory -- or a fake -- without
any monkey-patching.
"""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path

from .models import ActiveSession, CompletedSession
from .utils import CorruptJSONError, TrackerError, atomic_write_json, read_json

__all__ = ["NoSuchSessionError", "SessionExistsError", "Storage", "StorageError"]


class StorageError(TrackerError):
    """Raised when the filesystem refuses an operation (permissions, disk, ...)."""


class SessionExistsError(StorageError):
    """Raised when creating a file that must not already exist."""


class NoSuchSessionError(StorageError):
    """Raised when an operation names an archived session that does not exist."""


class Storage:
    """Reads and writes the tracker's JSON files under a single root directory.

    Args:
        root: Directory holding ``current.json`` and the ``sessions/`` folder.
    """

    __slots__ = ("_root",)

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    # -- paths --------------------------------------------------------------

    @property
    def root(self) -> Path:
        """The directory this storage instance is rooted at."""
        return self._root

    @property
    def current_path(self) -> Path:
        """Path of the active-session file."""
        return self._root / "current.json"

    @property
    def sessions_dir(self) -> Path:
        """Directory holding the archived sessions."""
        return self._root / "sessions"

    def session_path(self, session_id: str) -> Path:
        """Path of the archive file for ``session_id``.

        ``session_id`` is validated on construction of every model, so it cannot
        contain a path separator and cannot escape :attr:`sessions_dir`.
        """
        return self.sessions_dir / f"{session_id}.json"

    # -- current.json -------------------------------------------------------

    def has_current(self) -> bool:
        """Whether an active session file exists."""
        return self.current_path.is_file()

    def load_current(self) -> ActiveSession | None:
        """Load the active session, or ``None`` if no session is in progress.

        Raises:
            CorruptJSONError: If ``current.json`` exists but is unusable.
        """
        try:
            raw = read_json(self.current_path)
        except FileNotFoundError:
            return None
        return ActiveSession.from_dict(raw)

    def create_current(self, session: ActiveSession) -> None:
        """Create ``current.json`` for a new session.

        Uses ``O_CREAT | O_EXCL`` so that "does a session already exist?" and
        "claim the session" happen in one indivisible step. A plain
        ``exists()``-then-write would leave a window in which two concurrently
        launched Shortcuts could both pass the check and the second would
        silently discard the first session.

        Raises:
            SessionExistsError: If ``current.json`` already exists.
            StorageError: If the file cannot be written.
        """
        payload = json.dumps(session.to_dict(), indent=2, ensure_ascii=False) + "\n"
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.current_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError as exc:
            raise SessionExistsError(f"{self.current_path} already exists") from exc
        except OSError as exc:
            raise StorageError(f"cannot create {self.current_path}: {exc}") from exc

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            # The file exists but its contents are unknown -- remove it rather
            # than leave a half-written session behind.
            self.current_path.unlink(missing_ok=True)
            raise StorageError(f"cannot write {self.current_path}: {exc}") from exc

    def save_current(self, session: ActiveSession) -> None:
        """Overwrite ``current.json`` atomically with ``session``.

        Raises:
            StorageError: If the file cannot be written.
        """
        try:
            atomic_write_json(self.current_path, session.to_dict())
        except OSError as exc:
            raise StorageError(f"cannot write {self.current_path}: {exc}") from exc

    def delete_current(self) -> None:
        """Remove ``current.json``. Missing is not an error.

        Raises:
            StorageError: If the file exists but cannot be removed.
        """
        try:
            self.current_path.unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError(f"cannot remove {self.current_path}: {exc}") from exc

    # -- sessions/ ----------------------------------------------------------

    def has_session(self, session_id: str) -> bool:
        """Whether an archive with this id exists.

        ``session_id`` must already have been validated (see
        :func:`tracker.models.validate_session_id`), like every id that is used
        to build a path.
        """
        return self.session_path(session_id).is_file()

    def archive(self, session: CompletedSession) -> Path:
        """Write ``session`` into ``sessions/`` and return the path written.

        Refuses to overwrite an existing archive: a completed session is
        immutable, so a collision means something is wrong and destroying the
        older record would be the worst possible response.

        Raises:
            SessionExistsError: If an archive with this id already exists.
            StorageError: If the file cannot be written.
        """
        path = self.session_path(session.id)
        if path.exists():
            raise SessionExistsError(f"a session archive already exists at {path}")
        try:
            atomic_write_json(path, session.to_dict())
        except OSError as exc:
            raise StorageError(f"cannot write {path}: {exc}") from exc
        return path

    def update_session(self, session: CompletedSession) -> Path:
        """Rewrite an archive that already exists, atomically.

        This is the only way an archived session is ever rewritten, and it exists
        for one reason: to let you write down what a day was spent on when you
        forgot to say so at ``start``.

        It **refuses to create** a file. :meth:`archive` therefore remains the
        only thing in the system that can bring a session into existence, and a
        mistyped id here can only ever fail -- it can never quietly mint a new,
        half-empty day next to the real ones.

        Raises:
            NoSuchSessionError: If no archive with this id exists.
            StorageError: If the file cannot be written.
        """
        path = self.session_path(session.id)
        if not path.is_file():
            raise NoSuchSessionError(f"no session archive at {path}")
        try:
            atomic_write_json(path, session.to_dict())
        except OSError as exc:
            raise StorageError(f"cannot write {path}: {exc}") from exc
        return path

    def load_session(self, path: Path) -> CompletedSession:
        """Load one archived session.

        Raises:
            CorruptJSONError: If the file is missing or malformed.
        """
        try:
            raw = read_json(path)
        except FileNotFoundError as exc:
            raise CorruptJSONError(f"no session archive at {path}") from exc
        return CompletedSession.from_dict(raw)

    def list_sessions(self) -> list[Path]:
        """Return the archived session files, oldest first.

        Session ids are zero-padded timestamps, so a lexicographic sort by
        filename is also a chronological sort.
        """
        try:
            paths = sorted(self.sessions_dir.glob("*.json"))
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                return []
            raise StorageError(f"cannot list {self.sessions_dir}: {exc}") from exc
        return [path for path in paths if path.is_file()]
