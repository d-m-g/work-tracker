"""The planner: tasks in SQLite, an append-only event log, and the plan.

The work tracker stays the single source of truth for *actual* time: the planner
never times anything itself. It starts and stops the tracker through the same
CLI the Shortcuts use (so the VM sync applies), and reads worked time back from
the sessions that carry a ``taskId``.

``task_events`` is append-only and written from day one -- created, parsed,
edited, scheduled, rescheduled (by the user or the scheduler), started,
stopped, block_done, skipped, completed, deleted -- because the analysis can
come later and the data cannot be reconstructed.
"""

from __future__ import annotations

import io
import json
import os
from calendar import monthrange
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from tracker.storage import Storage
from tracker.utils import TrackerError, now

from . import match, schedule, stats
from .parse import TYPES, ParseError, parse_task

HORIZON_DAYS = 14
#: How far back sessions are matched to tasks, and shown to be sorted.
MATCH_DAYS = 14
#: How long a planned block may run with nothing done on it before it counts as
#: missed. Until then it stays where it was planned -- you may still start.
GRACE = timedelta(minutes=15)
#: Days between occurrences; "monthly" is a calendar month (see _next_month).
RECURRENCE = {"daily": 1, "weekdays": 1, "weekly": 7, "monthly": 0}


def _next_month(moment: datetime) -> datetime:
    """The same date next month -- or that month's last day, when it has no such date."""
    year, month = (moment.year + 1, 1) if moment.month == 12 else (moment.year, moment.month + 1)
    return moment.replace(year=year, month=month, day=min(moment.day, monthrange(year, month)[1]))

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id             INTEGER PRIMARY KEY,
    raw            TEXT NOT NULL,
    title          TEXT NOT NULL,
    estimate_min   INTEGER NOT NULL DEFAULT 60,
    deadline       TEXT,
    deadline_hard  INTEGER NOT NULL DEFAULT 0,
    priority       INTEGER NOT NULL DEFAULT 2,
    earliest_start TEXT,
    splittable     INTEGER NOT NULL DEFAULT 1,
    min_chunk      INTEGER NOT NULL DEFAULT 30,
    recurrence     TEXT,
    project        TEXT,
    type           TEXT NOT NULL DEFAULT 'shallow',
    status         TEXT NOT NULL DEFAULT 'open',   -- open | blocked | done | deleted
    parsed         INTEGER NOT NULL DEFAULT 0,     -- 0 parsing, 1 parsed, -1 failed
    late           INTEGER NOT NULL DEFAULT 0,
    created        TEXT NOT NULL,
    closed         TEXT
);
CREATE TABLE IF NOT EXISTS task_events (
    id      INTEGER PRIMARY KEY,
    task_id INTEGER,
    at      TEXT NOT NULL,
    kind    TEXT NOT NULL,
    data    TEXT
);
CREATE TABLE IF NOT EXISTS blocks (
    task_id INTEGER NOT NULL,
    start   TEXT NOT NULL,
    end     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
-- Which task a session started by hand was for: matched, or chosen by you.
-- task_id NULL is "not a task" -- a session you said belongs to none.
CREATE TABLE IF NOT EXISTS links (
    session_id TEXT PRIMARY KEY,
    task_id    INTEGER,
    source     TEXT NOT NULL,   -- auto | you
    score      REAL,
    at         TEXT NOT NULL
);
-- Time you said a task took, when the clock was not running for it. Counted for
-- the task, the plan and the analysis -- never written into the tracker, whose
-- history holds only what its clock recorded.
CREATE TABLE IF NOT EXISTS manual_work (
    id      INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL,
    start   TEXT NOT NULL,
    end     TEXT NOT NULL,
    at      TEXT NOT NULL
);
-- Session names you linked to a task, so the next one with that name links itself.
CREATE TABLE IF NOT EXISTS aliases (name TEXT PRIMARY KEY, task_id INTEGER NOT NULL);
-- Static blocks: put somewhere by hand, and never moved by the scheduler.
CREATE TABLE IF NOT EXISTS pins (
    id      INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL,
    start   TEXT NOT NULL,
    end     TEXT NOT NULL
);
"""

EDITABLE = {
    "title", "estimate_min", "deadline", "deadline_hard", "priority", "earliest_start",
    "splittable", "min_chunk", "recurrence", "project", "type", "status",
}


def _aware(raw: Any, name: str) -> datetime:
    """An ISO timestamp from a caller, which must carry its offset."""
    try:
        moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        raise PlannerError(f"{name} must be a date and time, got {raw!r}") from None
    if moment.tzinfo is None:
        raise PlannerError(f"{name} must carry a UTC offset")
    return moment


def _utc(moment: datetime) -> str:
    """Blocks are stored in UTC, so comparing the strings compares the instants."""
    return moment.astimezone(timezone.utc).isoformat()


def _earliest(row: sqlite3.Row, lead_days: int) -> Optional[datetime]:
    """When a task may first be planned: its own "not before", or else -- when it has
    a deadline -- ``lead_days`` before it, so work is not booked weeks too early."""
    if row["earliest_start"]:
        return datetime.fromisoformat(row["earliest_start"])
    if row["deadline"] and lead_days:
        return datetime.fromisoformat(row["deadline"]) - timedelta(days=lead_days)
    return None


class PlannerError(TrackerError):
    """A planner request refused, in a sentence written to be shown."""


def _hhmm(value: time) -> str:
    return value.strftime("%H:%M")


#: The hours as stored and as the viewer edits them: per weekday (Monday "0"),
#: a list of [open, close] windows; and the deep-work window, or null.
DEFAULT_HOURS = {
    "days": {str(day): [[_hhmm(a), _hhmm(b)] for a, b in schedule.WORK_HOURS.get(day, [])]
             for day in range(7)},
    "deep": [_hhmm(t) for t in schedule.DEEP_HOURS],
    "buffer": int(schedule.BUFFER.total_seconds() // 60),
    "min_session": 30,
    "max_session": 180,
    "lead_days": 5,
}


def _clock_time(raw: Any) -> time:
    """"HH:MM" -> time; "24:00" is the end of the day, kept as 23:59."""
    if not isinstance(raw, str):
        raise PlannerError(f"a time is HH:MM, got {raw!r}")
    if raw == "24:00":
        return time(23, 59)
    try:
        return time.fromisoformat(raw)
    except ValueError:
        raise PlannerError(f"a time is HH:MM, got {raw!r}") from None


def _window(raw: Any) -> Tuple[time, time]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise PlannerError("a window is [open, close]")
    opens, closes = _clock_time(raw[0]), _clock_time(raw[1])
    if not opens < closes:
        raise PlannerError(f"a window must open before it closes: {raw[0]}–{raw[1]}")
    return opens, closes


def checked_hours(raw: Any) -> Dict[str, Any]:
    """Validate the hours the viewer sent into their stored form, or refuse them."""
    if not isinstance(raw, dict) or not isinstance(raw.get("days"), dict):
        raise PlannerError("hours need a 'days' object")
    days: Dict[str, List[List[str]]] = {}
    for day in range(7):
        windows = sorted(_window(w) for w in raw["days"].get(str(day), []) or [])
        if len(windows) > 6:
            raise PlannerError("at most six windows a day")
        for (_, closes), (opens, _) in zip(windows, windows[1:]):
            if opens < closes:
                raise PlannerError("a day's windows may not overlap")
        days[str(day)] = [[_hhmm(a), _hhmm(b)] for a, b in windows]
    deep = raw.get("deep")
    def minutes(key: str, low: int, high: int, what: str) -> int:
        value = raw.get(key, DEFAULT_HOURS[key])
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise PlannerError(f"{what} is a whole number of minutes, {low} to {high}")
        return value

    buffer = minutes("buffer", 0, 60, "the buffer")
    shortest = minutes("min_session", 5, 240, "the shortest session")
    longest = minutes("max_session", 15, 720, "the longest session")
    if longest < shortest:
        raise PlannerError("the longest session cannot be shorter than the shortest")
    lead = raw.get("lead_days", DEFAULT_HOURS["lead_days"])
    if isinstance(lead, bool) or not isinstance(lead, int) or not 0 <= lead <= 60:
        raise PlannerError("the days before a deadline are a whole number, 0 to 60")
    return {"days": days, "deep": [_hhmm(t) for t in _window(deep)] if deep else None,
            "buffer": buffer, "min_session": shortest, "max_session": longest,
            "lead_days": lead}

def _checked(field: str, value: Any) -> Any:
    """Validate one edited field into its column value, or refuse it."""
    def number(low: int, high: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise PlannerError(f"{field} must be a whole number from {low} to {high}")
        return value

    if field not in EDITABLE:
        raise PlannerError(f"{field} cannot be edited")
    if field == "title":
        title = " ".join(str(value or "").split())
        if not title or len(title) > 200:
            raise PlannerError("a title must be 1 to 200 characters")
        return title
    if field in ("estimate_min", "min_chunk"):
        return number(5, 7 * 24 * 60)
    if field == "priority":
        return number(1, 3)
    if field in ("deadline_hard", "splittable"):
        return int(bool(value))
    if field in ("deadline", "earliest_start"):
        if value in (None, ""):
            return None
        try:
            moment = datetime.fromisoformat(str(value))
        except ValueError:
            raise PlannerError(f"{field} must be a date and time, got {value!r}") from None
        return (moment if moment.tzinfo else moment.replace(tzinfo=now().tzinfo)).isoformat()
    if field == "recurrence":
        if value not in (None, "", *RECURRENCE):
            raise PlannerError(f"recurrence must be one of {', '.join(RECURRENCE)} or none")
        return value or None
    if field == "project":
        project = " ".join(str(value or "").split())[:80]
        return project or None
    if field == "type":
        if value not in TYPES:
            raise PlannerError(f"type must be one of {', '.join(TYPES)}")
        return value
    if value not in ("open", "blocked"):  # status; done and deleted have their own commands
        raise PlannerError("status may only be set to open or blocked")
    return value


def _adopt_widget_remote() -> None:
    """Drive the VM the widget drives, when this process was not told of one.

    The widget keeps its VM in ``defaults`` (WorkTrackerRemote, WorkTrackerRemoteKey)
    rather than the environment. A server started without WORK_TRACKER_SSH would
    otherwise start sessions on this Mac alone -- and the widget's next sync, which
    mirrors the VM down when nothing is pending, would quietly undo them.
    """
    if "WORK_TRACKER_SSH" in os.environ or sys.platform != "darwin":
        return
    for key, env in (("WorkTrackerRemote", "WORK_TRACKER_SSH"),
                     ("WorkTrackerRemoteKey", "WORK_TRACKER_SSH_KEY")):
        try:
            done = subprocess.run(["defaults", "read", "com.work-tracker.widget", key],
                                  capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return
        if done.returncode == 0 and done.stdout.strip():
            os.environ[env] = done.stdout.strip()


class Planner:
    """Every planner operation. ``calendar`` is ``None`` where there is no EventKit."""

    def __init__(
        self,
        root: Path,
        clock: Callable[[], datetime] = now,
        parser: Callable[[str, datetime], Dict[str, Any]] = parse_task,
        calendar: Any = "auto",
        tracker_cli: Optional[Callable[[List[str]], str]] = None,
    ) -> None:
        self.root = root
        self.clock = clock
        self.parser = parser
        if calendar == "auto":
            calendar = None
            # Off by the variable, or by a `calendar.off` file in this data root --
            # which reaches the widget too, where no shell variable does. One
            # planner per calendar: two would rewrite each other's Planned blocks.
            wanted = (os.environ.get("PLANNER_CALENDAR", "on") != "off"
                      and not (root / "calendar.off").exists())
            if wanted and os.environ.get("PLANNER_CALDAV_USER"):
                # iCloud over CalDAV: the server's way in, where EventKit is not.
                from .caldav import from_env
                calendar = from_env()
            elif wanted and sys.platform == "darwin" and shutil.which("swiftc"):
                from .eventkit import AppleCalendar
                calendar = AppleCalendar(root / ".planner-build")
        self.calendar = calendar
        self.tracker_cli = tracker_cli or self._tracker_cli
        root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(root / "planner.db"), timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # -- plumbing -------------------------------------------------------------

    def _tracker_cli(self, args: List[str]) -> str:
        """Run a tracker CLI command in-process -- the CLI, so the VM sync applies."""
        from tracker.cli import main

        _adopt_widget_remote()
        out, err = io.StringIO(), io.StringIO()
        if main(["--root", str(self.root), *args], out=out, err=err) != 0:
            raise PlannerError(err.getvalue().strip().removeprefix("error: "))
        return out.getvalue()

    def _log(self, task_id: Optional[int], kind: str, **data: Any) -> None:
        self.db.execute(
            "INSERT INTO task_events (task_id, at, kind, data) VALUES (?, ?, ?, ?)",
            (task_id, self.clock().isoformat(), kind, json.dumps(data) if data else None),
        )

    def _task(self, task_id: Any) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM tasks WHERE id = ? AND status != 'deleted'",
                              (task_id,)).fetchone()
        if row is None:
            raise PlannerError(f"no such task: {task_id}")
        return row

    def _all_sessions(self) -> List[Dict[str, Any]]:
        """Every tracker session, archived and live: id, label, task, start, end, worked."""
        storage = Storage(self.root)
        found = []
        for path in storage.list_sessions():
            try:
                session = storage.load_session(path)
            except TrackerError:
                continue
            found.append({"id": session.id, "label": session.task, "task": session.task_id,
                          "start": session.start, "end": session.end,
                          "worked": session.worked_seconds, "live": False})
        current = storage.load_current()
        if current is not None:
            moment = self.clock()
            found.append({"id": current.id, "label": current.task, "task": current.task_id,
                          "start": current.start, "end": moment,
                          "worked": current.worked_seconds(moment), "live": True})
        return found

    def _sessions(self) -> List[Dict[str, Any]]:
        """Every stretch of work that counts toward a task: sessions started for it,
        sessions linked to it since, and time you said it took when marking it done."""
        links = {row["session_id"]: row["task_id"]
                 for row in self.db.execute("SELECT * FROM links WHERE task_id IS NOT NULL")}
        found = []
        for session in self._all_sessions():
            task = session["task"] or (str(links[session["id"]]) if session["id"] in links else None)
            if task:
                found.append({**session, "task": task})
        for row in self.db.execute("SELECT * FROM manual_work"):
            start, end = datetime.fromisoformat(row["start"]), datetime.fromisoformat(row["end"])
            found.append({"id": f"manual-{row['id']}", "label": None, "task": str(row["task_id"]),
                          "start": start, "end": end, "live": False, "manual": True,
                          "worked": int((end - start).total_seconds())})
        return found

    def _worked_min(self) -> Dict[int, int]:
        worked: Dict[int, int] = {}
        for session in self._sessions():
            if session["task"].isdigit():
                key = int(session["task"])
                worked[key] = worked.get(key, 0) + session["worked"]
        return {key: seconds // 60 for key, seconds in worked.items()}

    def _meta(self, key: str, value: Any = None) -> Any:
        if value is None:
            row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return json.loads(row[0]) if row else None
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, json.dumps(value)))
        return value

    # -- matching sessions to tasks -------------------------------------------

    def _match_inputs(self) -> Tuple[List[match.Task], match.History, List[Dict[str, Any]]]:
        """The tasks a session may be for, what history teaches, and the recent sessions."""
        first = self.db.execute("SELECT MIN(created) FROM tasks").fetchone()[0]
        since = self.clock() - timedelta(days=MATCH_DAYS)
        if first:  # nothing before the first task can have been for one
            since = max(since, datetime.fromisoformat(first))
        blocks: Dict[int, List[match.Interval]] = {}
        for row in self.db.execute("SELECT task_id, start, end FROM blocks UNION ALL "
                                   "SELECT task_id, start, end FROM pins"):
            blocks.setdefault(row[0], []).append(
                (datetime.fromisoformat(row[1]), datetime.fromisoformat(row[2])))
        for row in self.db.execute("SELECT task_id, data FROM task_events WHERE kind IN "
                                   "('block_done', 'skipped') AND at >= ?", (since.isoformat(),)):
            data = json.loads(row[1] or "{}")
            if row[0] is not None and data.get("start"):
                blocks.setdefault(row[0], []).append(
                    (datetime.fromisoformat(data["start"]), datetime.fromisoformat(data["end"])))
        tasks = [
            match.Task(row["id"], row["title"], row["project"], tuple(blocks.get(row["id"], ())))
            for row in self.db.execute(
                "SELECT * FROM tasks WHERE status IN ('open', 'blocked')"
                " OR (status = 'done' AND closed >= ?)", (since.isoformat(),))
        ]
        sessions = [s for s in self._all_sessions() if s["start"] >= since]
        links = {row["session_id"]: row["task_id"] for row in self.db.execute("SELECT * FROM links")}
        history = match.History(
            aliases={row["name"]: row["task_id"] for row in self.db.execute("SELECT * FROM aliases")},
            linked=[(match.Session(s["id"], s["label"], s["start"], s["end"]),
                     int(s["task"]) if s["task"] and s["task"].isdigit() else links.get(s["id"]))
                    for s in sessions if s["task"] or links.get(s["id"])],
        )
        return tasks, history, sessions

    def _match(self) -> List[Dict[str, Any]]:
        """Link what is certain; return every recent session still to sort, with choices.

        A session the planner started already knows its task and is never here. Of
        the rest, one scoring at least ``match.AUTO`` is linked on its own (and still
        listed, so it can be changed); the others wait, with their best candidates.
        """
        tasks, history, sessions = self._match_inputs()
        titles = {task.id: task.title for task in tasks}
        links = {row["session_id"]: row for row in self.db.execute("SELECT * FROM links")}
        inbox = []
        with self.db:
            for session in sorted(sessions, key=lambda s: s["start"], reverse=True):
                if session["task"]:
                    continue
                link = links.get(session["id"])
                if link is not None and link["source"] == "you":
                    continue  # you said: settled
                found = match.candidates(
                    match.Session(session["id"], session["label"], session["start"], session["end"]),
                    tasks, history)
                best = found[0] if found else None
                if link is None and best is not None and best.score >= match.AUTO:
                    self.db.execute("INSERT INTO links VALUES (?, ?, 'auto', ?, ?)",
                                    (session["id"], best.task_id, best.score, self.clock().isoformat()))
                    self._log(best.task_id, "linked", session=session["id"], by="matcher",
                              score=best.score, reason=best.reason)
                    link = {"task_id": best.task_id, "source": "auto", "score": best.score}
                inbox.append({
                    "session": session["id"], "label": session["label"], "live": session["live"],
                    "start": session["start"].isoformat(), "end": session["end"].isoformat(),
                    "worked_min": session["worked"] // 60,
                    "link": ({"task_id": link["task_id"], "title": titles.get(link["task_id"], ""),
                              "score": link["score"]} if link is not None else None),
                    "suggestions": [{"task_id": c.task_id, "title": titles[c.task_id],
                                     "score": c.score, "reason": c.reason}
                                    for c in found[:3] if c.score >= match.SUGGEST],
                })
        return inbox

    def link(self, session_id: Any, task_id: Any) -> None:
        """Say which task a session was for (``None``: none). Learns its name as an alias."""
        session = next((s for s in self._all_sessions() if s["id"] == session_id), None)
        if session is None:
            raise PlannerError(f"no such session: {session_id}")
        if session["task"]:
            raise PlannerError("that session was started for a task already")
        if task_id is not None:
            task_id = self._task(task_id)["id"]
        name = match.normalise(session["label"])
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO links VALUES (?, ?, 'you', NULL, ?)",
                            (session_id, task_id, self.clock().isoformat()))
            if task_id is not None and name:
                self.db.execute("INSERT OR REPLACE INTO aliases VALUES (?, ?)", (name, task_id))
            self._log(task_id, "linked", session=session_id, by="user")
            # A block of this task judged skipped while the session was unlinked was
            # worked after all: say so. The analysis takes the later word.
            for row in self.db.execute("SELECT data FROM task_events WHERE task_id = ? AND kind = 'skipped'",
                                       (task_id,)).fetchall() if task_id is not None else []:
                data = json.loads(row["data"] or "{}")
                start, end = datetime.fromisoformat(data["start"]), datetime.fromisoformat(data["end"])
                if session["start"] < end and session["end"] > start:
                    self._log(task_id, "block_done", start=data["start"], end=data["end"],
                              corrects="skipped",
                              late_min=max(0, int((session["start"] - start).total_seconds() // 60)))
        self.replan()

    # -- analysis -------------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        """The analysis over everything recorded: see planner/stats.py."""
        tasks = [dict(row) for row in self.db.execute("SELECT * FROM tasks WHERE status != 'deleted'")]
        work = [(int(s["task"]), s["start"], s["worked"])
                for s in self._sessions() if s["task"].isdigit()]
        events = [(row["task_id"], datetime.fromisoformat(row["at"]), row["kind"],
                   json.loads(row["data"] or "{}"))
                  for row in self.db.execute("SELECT * FROM task_events")]
        result = stats.compute(tasks, work, events, self.clock())
        result["titles"] = {str(t["id"]): t["title"] for t in tasks}
        result["review"] = self._meta("review")
        return result

    def review(self) -> Dict[str, Any]:
        """A short, honest weekly review by Claude, from the aggregates alone."""
        from .parse import review_week
        figures = self.stats()
        figures.pop("review", None)
        titles = figures.pop("titles")
        for row in figures["dodged"]:
            row["title"] = titles.get(str(row["task_id"]), row.get("title"))
        try:
            text = review_week(figures)
        except ParseError as exc:
            raise PlannerError(f"the review could not be written: {exc}") from exc
        with self.db:
            return self._meta("review", {"at": self.clock().isoformat(), "text": text})

    # -- commands -------------------------------------------------------------

    def add(self, text: str) -> int:
        """Store the raw text at once, as a task that is still being parsed."""
        text = " ".join(str(text or "").split())
        if not text or len(text) > 500:
            raise PlannerError("say what the task is, in at most 500 characters")
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO tasks (raw, title, created) VALUES (?, ?, ?)",
                (text, text[:200], self.clock().isoformat()),
            )
            self._log(cursor.lastrowid, "created", raw=text)
        return int(cursor.lastrowid)

    def parse(self, task_id: int) -> None:
        """Fill in a task's fields from its raw text, then replan. A failed parse
        keeps the task with its defaults -- the text is never lost."""
        row = self._task(task_id)
        try:
            fields = self.parser(row["raw"], self.clock())
        except ParseError as exc:
            with self.db:
                self.db.execute("UPDATE tasks SET parsed = -1 WHERE id = ?", (task_id,))
                self._log(task_id, "parse_failed", error=str(exc))
            self.replan()
            return
        with self.db:
            columns = ", ".join(f"{name} = ?" for name in fields)
            self.db.execute(f"UPDATE tasks SET {columns}, parsed = 1 WHERE id = ?",
                            (*fields.values(), task_id))
            self._log(task_id, "parsed", **fields)
        self.replan()

    def edit(self, task_id: Any, changes: Dict[str, Any]) -> None:
        row = self._task(task_id)
        clean = {field: _checked(field, value) for field, value in changes.items()}
        if not clean:
            return
        with self.db:
            columns = ", ".join(f"{name} = ?" for name in clean)
            self.db.execute(f"UPDATE tasks SET {columns}, parsed = 1 WHERE id = ?",
                            (*clean.values(), row["id"]))
            self._log(row["id"], "edited", **clean)
            for field in ("deadline", "earliest_start"):
                if field in clean and clean[field] != row[field]:
                    self._log(row["id"], "rescheduled", by="user", field=field,
                              before=row[field], after=clean[field])
        self.replan()

    def done(self, task_id: Any, minutes: Any = None) -> None:
        """Mark a task done. ``minutes``: how long it took, when the clock was not
        running for it -- recorded as work ending now, right before Done was pressed."""
        row = self._task(task_id)
        moment = self.clock()
        if minutes is not None:
            if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 24 * 60:
                raise PlannerError("how long it took is a whole number of minutes, 1 to 1440")
        with self.db:
            if minutes:
                self.db.execute("INSERT INTO manual_work (task_id, start, end, at) VALUES (?, ?, ?, ?)",
                                (row["id"], _utc(moment - timedelta(minutes=minutes)), _utc(moment),
                                 moment.isoformat()))
                self._log(row["id"], "recorded", minutes=minutes)
            self.db.execute("UPDATE tasks SET status = 'done', closed = ? WHERE id = ?",
                            (moment.isoformat(), row["id"]))
            self._log(row["id"], "completed")
            if row["recurrence"] in RECURRENCE:
                self._repeat(row, RECURRENCE[row["recurrence"]])
        self.replan()

    def _repeat(self, row: sqlite3.Row, step: int) -> None:
        """The next occurrence of a recurring task, its dates moved on one period."""
        def shift(value: Optional[str]) -> Optional[str]:
            if not value:
                return None
            moment = datetime.fromisoformat(value)
            moment = _next_month(moment) if row["recurrence"] == "monthly" else moment + timedelta(days=step)
            while row["recurrence"] == "weekdays" and moment.weekday() >= 5:
                moment += timedelta(days=1)
            return moment.isoformat()

        copy = {k: row[k] for k in row.keys() if k not in ("id", "status", "late", "closed")}
        copy.update(deadline=shift(row["deadline"]), earliest_start=shift(row["earliest_start"]),
                    created=self.clock().isoformat())
        cursor = self.db.execute(
            f"INSERT INTO tasks ({', '.join(copy)}) VALUES ({', '.join('?' * len(copy))})",
            tuple(copy.values()),
        )
        self._log(cursor.lastrowid, "created", recurring_from=row["id"])

    def pin(self, task_id: Any, start: Any, end: Any, pin_id: Any = None) -> None:
        """Fix a block of a task at a time of your choosing -- a static block.

        The scheduler plans around it as it would around a meeting, and counts its
        minutes against the task, so the rest of the task is planned elsewhere.
        With ``pin_id`` the existing static block is moved instead.
        """
        row = self._task(task_id)
        if row["status"] != "open":
            raise PlannerError("only an open task can be put on the calendar")
        begins, ends = (_aware(value, name) for value, name in ((start, "start"), (end, "end")))
        if not timedelta(minutes=5) <= ends - begins <= timedelta(hours=12):
            raise PlannerError("a static block runs from 5 minutes to 12 hours")
        if ends <= self.clock():
            raise PlannerError("that time has already passed")
        with self.db:
            if pin_id is None:
                self.db.execute("INSERT INTO pins (task_id, start, end) VALUES (?, ?, ?)",
                                (row["id"], _utc(begins), _utc(ends)))
                before = None
            else:
                old = self.db.execute("SELECT * FROM pins WHERE id = ? AND task_id = ?",
                                      (pin_id, row["id"])).fetchone()
                if old is None:
                    raise PlannerError(f"no such static block: {pin_id}")
                before = old["start"]
                self.db.execute("UPDATE pins SET start = ?, end = ? WHERE id = ?",
                                (_utc(begins), _utc(ends), pin_id))
            self._log(row["id"], "pinned", start=_utc(begins), end=_utc(ends), before=before)
        self.replan()

    def retime_work(self, work_id: Any, start: Any, end: Any) -> None:
        """Move time entered by hand (Done's "it took N min"). Tracker sessions are
        never rewritten -- only these, which the planner itself recorded."""
        begins, ends = (_aware(value, name) for value, name in ((start, "start"), (end, "end")))
        if not timedelta(minutes=1) <= ends - begins <= timedelta(hours=24):
            raise PlannerError("entered work runs from 1 minute to 24 hours")
        if ends > self.clock():
            raise PlannerError("work that is done cannot end in the future")
        with self.db:
            old = self.db.execute("SELECT * FROM manual_work WHERE id = ?", (work_id,)).fetchone()
            if old is None:
                raise PlannerError(f"no such entered work: {work_id}")
            self.db.execute("UPDATE manual_work SET start = ?, end = ? WHERE id = ?",
                            (_utc(begins), _utc(ends), work_id))
            self._log(old["task_id"], "work_moved", start=_utc(begins), end=_utc(ends),
                      before=[old["start"], old["end"]])
        self.replan()

    def unpin(self, pin_id: Any) -> None:
        """Hand a static block back to the scheduler."""
        with self.db:
            old = self.db.execute("SELECT * FROM pins WHERE id = ?", (pin_id,)).fetchone()
            if old is None:
                raise PlannerError(f"no such static block: {pin_id}")
            self.db.execute("DELETE FROM pins WHERE id = ?", (pin_id,))
            self._log(old["task_id"], "unpinned", start=old["start"], end=old["end"])
        self.replan()

    def edit_event(self, raw: Any) -> None:
        """Change one occurrence of an Apple Calendar event, then replan around it."""
        if self.calendar is None:
            raise PlannerError("no calendar is connected on this server")
        if not isinstance(raw, dict) or not raw.get("id") or not raw.get("occurrence"):
            raise PlannerError("name the event by its id and occurrence")
        change = {"id": str(raw["id"]), "occurrence": _utc(_aware(raw["occurrence"], "occurrence"))}
        if raw.get("title") is not None:
            title = " ".join(str(raw["title"]).split())
            if not title or len(title) > 200:
                raise PlannerError("a title must be 1 to 200 characters")
            change["title"] = title
        if raw.get("start") is not None or raw.get("end") is not None:
            begins, ends = _aware(raw.get("start"), "start"), _aware(raw.get("end"), "end")
            if not timedelta(minutes=5) <= ends - begins <= timedelta(hours=24):
                raise PlannerError("an event runs from 5 minutes to 24 hours")
            change["start"], change["end"] = begins.isoformat(), ends.isoformat()
        try:
            self.calendar.edit(change)
        except Exception as exc:  # the bridge's own sentence, e.g. a read-only calendar
            raise PlannerError(str(exc)) from exc
        with self.db:
            self._log(None, "event_edited", **change)
        self.replan()

    def delete(self, task_id: Any) -> None:
        row = self._task(task_id)
        with self.db:
            self.db.execute("UPDATE tasks SET status = 'deleted', closed = ? WHERE id = ?",
                            (self.clock().isoformat(), row["id"]))
            self._log(row["id"], "deleted")
        self.replan()

    def start(self, task_id: Any) -> None:
        """Put this task on the tracker's clock, ending whatever session was running."""
        row = self._task(task_id)
        status = json.loads(self.tracker_cli(["--json", "status"]))
        if status["state"] != "idle":
            if status.get("taskId") == str(row["id"]):
                raise PlannerError("that task is already on the clock")
            self.stop(None)
        args = ["start", "--task", row["title"], "--task-id", str(row["id"])]
        if row["project"]:
            args += ["--project", row["project"]]
        self.tracker_cli(args)
        with self.db:
            if row["status"] == "blocked":
                self.db.execute("UPDATE tasks SET status = 'open' WHERE id = ?", (row["id"],))
            self._log(row["id"], "started")

    def stop(self, outcome: Optional[str]) -> None:
        """Stop the tracker. ``outcome`` -- done, more or blocked -- says how the task went."""
        if outcome not in (None, "done", "more", "blocked"):
            raise PlannerError("an outcome is done, more or blocked")
        stopped = json.loads(self.tracker_cli(["--json", "stop"]))
        task_id = stopped.get("taskId")
        linked = task_id is not None and task_id.isdigit()
        with self.db:
            self._log(int(task_id) if linked else None, "stopped", outcome=outcome,
                      session=stopped["id"], worked=stopped["workedSeconds"])
            if linked and outcome == "blocked":
                self.db.execute("UPDATE tasks SET status = 'blocked' WHERE id = ?", (int(task_id),))
        if linked and outcome == "done":
            self.done(int(task_id))
        else:
            self.replan()

    # -- the hours it may plan into ----------------------------------------------

    def hours(self) -> Dict[str, Any]:
        return {**DEFAULT_HOURS, **(self._meta("hours") or {})}  # older saves lack "buffer"

    def set_hours(self, raw: Any) -> None:
        hours = checked_hours(raw)
        with self.db:
            self._meta("hours", hours)
            self._log(None, "hours_changed", **hours)
        self.replan()

    def _schedule_hours(
        self,
    ) -> Tuple[Dict[int, List[schedule.Window]], Optional[schedule.Window], timedelta]:
        hours = self.hours()
        days = {int(day): [_window(w) for w in windows] for day, windows in hours["days"].items()}
        deep = _window(hours["deep"]) if hours["deep"] else None
        return days, deep, timedelta(minutes=hours["buffer"])

    # -- the plan -------------------------------------------------------------

    def replan(self) -> Dict[str, Any]:
        """Score the blocks that have passed, then plan from now and write the calendar."""
        moment = self.clock()
        horizon = moment + timedelta(days=HORIZON_DAYS)
        self._match()
        sessions = self._sessions()
        worked = self._worked_min()

        with self.db:
            # Every block whose time has passed is judged once: was the task worked
            # on during it? That is the "slot reliability" the analysis wants.
            # Static blocks are judged the same way, and once past are gone too.
            # A worked block also records how late its work began.
            for table in ("blocks", "pins"):
                for block in self.db.execute(f"SELECT * FROM {table} WHERE end <= ?",
                                             (_utc(moment),)).fetchall():
                    self._judge(block, sessions, static=table == "pins")
            # A block under way: kept where it is while it is being worked, or for
            # GRACE in case you are about to start; past that, untouched, it is
            # missed -- recorded once, here, and its time planned elsewhere. Without
            # this it would slide to "from now" at every replan and never be missed.
            held: List[schedule.Block] = []
            held_min: Dict[int, int] = {}
            reviewed = set(self._meta("reviewed") or [])
            for block in self.db.execute("SELECT * FROM blocks WHERE start <= ? AND end > ?",
                                         (_utc(moment), _utc(moment))).fetchall():
                start, end = (datetime.fromisoformat(block[k]) for k in ("start", "end"))
                touched = any(s["task"] == str(block["task_id"]) and s["start"] < moment
                              and s["end"] > start for s in sessions)
                if touched or moment - start < GRACE:
                    held.append(schedule.Block(block["task_id"], start, end))
                    held_min[block["task_id"]] = (held_min.get(block["task_id"], 0)
                                                  + int((end - moment).total_seconds() // 60))
                    if touched:
                        reviewed.add(f"{block['task_id']}@{block['start']}")
                else:
                    self._log(block["task_id"], "skipped", start=block["start"], end=block["end"],
                              missed=True, noticed=_utc(moment))
            # Forget reviews of blocks long over (no block runs past a day).
            keep_from = _utc(moment - timedelta(days=1))
            self._meta("reviewed", sorted(r for r in reviewed if r.split("@")[1] > keep_from))
            self.db.execute("DELETE FROM pins WHERE end <= ?", (_utc(moment),))
            pins = self.db.execute(
                "SELECT pins.* FROM pins JOIN tasks ON tasks.id = pins.task_id"
                " WHERE tasks.status = 'open'").fetchall()
            pinned_min: Dict[int, int] = {}
            pinned: List[schedule.Interval] = []
            for pin in pins:
                start, end = (datetime.fromisoformat(pin[k]) for k in ("start", "end"))
                pinned.append((start, end))
                left = max(start, moment)
                if end > left:
                    pinned_min[pin["task_id"]] = (pinned_min.get(pin["task_id"], 0)
                                                  + int((end - left).total_seconds() // 60))

            before = {row["task_id"]: row["first"] for row in self.db.execute(
                "SELECT task_id, MIN(start) AS first FROM blocks WHERE end > ? GROUP BY task_id",
                (_utc(moment),))}

            busy: List[schedule.Interval] = []
            error = None
            cached = None
            if self.calendar is not None:
                try:
                    busy = self.calendar.busy(moment, horizon)
                    self._meta("busy_cache", {"at": moment.isoformat(),
                                              "busy": [[_utc(a), _utc(b)] for a, b in busy]})
                except Exception as exc:
                    # Never plan blind: this process may not be allowed the calendar
                    # (the widget, before you have said yes) -- so plan around the
                    # calendar as it was last read, rather than around nothing.
                    error = str(exc)
                    cache = self._meta("busy_cache")
                    if cache:
                        cached = cache["at"]
                        busy = [(datetime.fromisoformat(a), datetime.fromisoformat(b))
                                for a, b in cache["busy"] if b > _utc(moment)]

            rows = self.db.execute("SELECT * FROM tasks WHERE status = 'open'").fetchall()
            settings = self.hours()
            # What the record says: estimates padded by how long such work really
            # takes, and the slots deep work usually does not happen in.
            learned = self.stats()
            pads = {row["id"]: stats.pad_for(dict(row), learned["padding"]) for row in rows}
            avoid = frozenset((slot["weekday"], slot["part"]) for slot in learned["avoid"])
            self._meta("pads", {str(k): v for k, v in pads.items() if v != 1.0})
            jobs = [
                schedule.Job(
                    id=row["id"],
                    minutes=max(0, round(row["estimate_min"] * pads[row["id"]])
                                - worked.get(row["id"], 0) - pinned_min.get(row["id"], 0)
                                - held_min.get(row["id"], 0)),
                    deadline=datetime.fromisoformat(row["deadline"]) if row["deadline"] else None,
                    priority=row["priority"],
                    earliest=_earliest(row, settings["lead_days"]),
                    splittable=bool(row["splittable"]),
                    min_chunk=settings["min_session"],
                    max_chunk=settings["max_session"],
                    deep=row["type"] == "deep",
                )
                for row in rows
            ]
            # A held block only credits the minutes left in it, so a task whose
            # block is under way can come out a few minutes short -- a scrap the
            # held block absorbs, not a 5-minute block of its own.
            jobs = [replace(job, minutes=0)
                    if job.id in held_min and job.minutes < settings["min_session"] else job
                    for job in jobs]
            days, deep, buffer = self._schedule_hours()
            blocks, short = schedule.plan(
                [job for job in jobs if job.minutes > 0],
                schedule.free_slots(moment, busy + pinned + [(b.start, b.end) for b in held],
                                    HORIZON_DAYS, days, deep, buffer),
                deep,
                buffer,
                avoid,
            )
            late = list(short)
            open_ids = {row["id"] for row in rows}
            blocks = sorted(blocks + [b for b in held if b.task_id in open_ids],
                            key=lambda b: b.start)

            self.db.execute("DELETE FROM blocks")
            self.db.executemany("INSERT INTO blocks VALUES (?, ?, ?)",
                                [(b.task_id, _utc(b.start), _utc(b.end)) for b in blocks])
            self.db.execute("UPDATE tasks SET late = 0")
            deadlines = {row["id"]: row["deadline"] for row in rows}
            shortfall = {task_id: {"unplaced": s.unplaced, "past_deadline": s.past_deadline}
                         for task_id, s in short.items()}
            for pin in pins:  # a static block past its task's deadline is late too
                due = deadlines.get(pin["task_id"])
                if due and datetime.fromisoformat(pin["end"]) > datetime.fromisoformat(due):
                    late.append(pin["task_id"])
                    over = int((datetime.fromisoformat(pin["end"]) - max(
                        datetime.fromisoformat(pin["start"]), datetime.fromisoformat(due)
                    )).total_seconds() // 60)
                    entry = shortfall.setdefault(pin["task_id"], {"unplaced": 0, "past_deadline": 0})
                    entry["past_deadline"] += over
            self._meta("shortfall", {str(k): v for k, v in shortfall.items()})
            self.db.executemany("UPDATE tasks SET late = 1 WHERE id = ?", [(i,) for i in late])

            after: Dict[int, str] = {}
            for block in blocks:
                after.setdefault(block.task_id, _utc(block.start))
            for task_id, first in after.items():
                if task_id not in before:
                    self._log(task_id, "scheduled", start=first)
                elif before[task_id] != first:
                    self._log(task_id, "rescheduled", by="scheduler",
                              before=before[task_id], after=first)

            if self.calendar is not None and error is None:
                titles = {row["id"]: row["title"] for row in rows}
                try:
                    self.calendar.write(moment, horizon, [
                        {"title": titles[b.task_id], "start": b.start.isoformat(),
                         "end": b.end.isoformat(), "notes": f"planner task #{b.task_id}",
                         "kind": "planned"}
                        for b in blocks
                    ] + [
                        {"title": titles[p["task_id"]], "start": p["start"], "end": p["end"],
                         "notes": f"planner task #{p['task_id']} (static)", "kind": "static"}
                        for p in pins if p["task_id"] in titles
                    ])
                except Exception as exc:
                    error = str(exc)

            self._meta("replanned", moment.isoformat())
            self._meta("calendar", {"error": error, "available": self.calendar is not None,
                                    "cached": cached})
        return self.payload(refresh=False)

    def _judge(self, block: sqlite3.Row, sessions: List[Dict[str, Any]], static: bool) -> None:
        """Record a finished block once: worked (and how late its work began) or skipped."""
        start, end = (datetime.fromisoformat(block[k]) for k in ("start", "end"))
        overlapping = [s for s in sessions if s["task"] == str(block["task_id"])
                       and s["start"] < end and s["end"] > start]
        if overlapping:
            began = min(s["start"] for s in overlapping)
            self._log(block["task_id"], "block_done", start=block["start"], end=block["end"],
                      static=static, late_min=max(0, int((began - start).total_seconds() // 60)))
        else:
            self._log(block["task_id"], "skipped", start=block["start"], end=block["end"],
                      static=static)

    def _stale(self) -> bool:
        """Has a block passed, or run past its grace untouched, since the last replan?

        The widget and the page only read the plan; this is what lets a block be
        judged on time without anyone pressing anything.
        """
        moment = self.clock()
        reviewed = set(self._meta("reviewed") or [])
        current = Storage(self.root).load_current()
        live = current.task_id if current is not None else None
        for row in self.db.execute("SELECT * FROM blocks WHERE start <= ?",
                                   (_utc(moment - GRACE),)):
            if row["end"] <= _utc(moment):
                return True
            if str(row["task_id"]) != live and f"{row['task_id']}@{row['start']}" not in reviewed:
                return True
        return self.db.execute("SELECT 1 FROM pins WHERE end <= ? LIMIT 1",
                               (_utc(moment),)).fetchone() is not None

    def suggest(self, limit: int = 2) -> List[Dict[str, Any]]:
        """What you are most likely about to start, best first, with why.

        The task planned for now, then the next planned one, then what you were
        last on, then whatever is due soonest -- the widget's start chooser.
        """
        moment = self.clock()
        rows = {row["id"]: row for row in self.db.execute("SELECT * FROM tasks WHERE status = 'open'")}
        found: List[Dict[str, Any]] = []

        def offer(task_id: Any, reason: str) -> None:
            if task_id in rows and all(f["id"] != task_id for f in found):
                found.append({"id": task_id, "title": rows[task_id]["title"], "reason": reason})

        stamp = _utc(moment)
        for row in self.db.execute("SELECT task_id, start, end FROM blocks UNION ALL "
                                   "SELECT task_id, start, end FROM pins ORDER BY start"):
            if row["start"] <= stamp < row["end"]:
                offer(row["task_id"], "planned now")
        nxt = self.db.execute("SELECT task_id, start FROM blocks WHERE start > ? UNION ALL "
                              "SELECT task_id, start FROM pins WHERE start > ? ORDER BY start LIMIT 1",
                              (stamp, stamp)).fetchone()
        if nxt is not None:
            when = datetime.fromisoformat(nxt["start"]).astimezone()
            offer(nxt["task_id"], f"planned {when.strftime('%a %H:%M')}")
        recent = sorted((s for s in self._sessions() if s["task"].isdigit()),
                        key=lambda s: s["end"], reverse=True)
        if recent:
            offer(int(recent[0]["task"]), "you were on it last")
        for row in sorted(rows.values(), key=lambda r: (r["deadline"] is None, r["deadline"] or "",
                                                         r["priority"])):
            if row["deadline"]:
                due = datetime.fromisoformat(row["deadline"]).astimezone()
                offer(row["id"], f"due {due.strftime('%a %H:%M')}")
            else:
                offer(row["id"], "open")
        return found[:limit]

    def _done_work(self) -> Dict[str, Any]:
        done = {row["id"]: row["title"] for row in
                self.db.execute("SELECT id, title FROM tasks WHERE status = 'done'")}
        sessions: Dict[str, Dict[str, Any]] = {}
        entered: List[Dict[str, Any]] = []
        for session in self._sessions():
            if not session["task"].isdigit() or int(session["task"]) not in done:
                continue
            task_id = int(session["task"])
            if session.get("manual"):
                entered.append({"id": int(session["id"][len("manual-"):]),
                                "task_id": task_id, "title": done[task_id],
                                "start": _utc(session["start"]), "end": _utc(session["end"])})
            else:
                sessions[session["id"]] = {"task_id": task_id, "title": done[task_id]}
        return {"sessions": sessions, "entered": entered}

    def start_new(self, text: str) -> int:
        """Something not on the list: make it a task and start it now, parsed later."""
        task_id = self.add(text)
        self.start(task_id)
        return task_id

    def payload(self, refresh: bool = True) -> Dict[str, Any]:
        """Everything the viewer draws: tasks with their blocks, and what is on now.

        A block that has passed, or run past its grace untouched, is judged first
        (by replanning) -- reading the plan is what keeps the record on time.
        """
        if refresh and self._stale():
            return self.replan()
        moment = self.clock()
        worked = self._worked_min()
        blocks: Dict[int, List[Dict[str, Any]]] = {}
        for row in self.db.execute("SELECT * FROM blocks ORDER BY start"):
            blocks.setdefault(row["task_id"], []).append({"start": row["start"], "end": row["end"]})
        pins: Dict[int, List[Dict[str, Any]]] = {}
        for row in self.db.execute("SELECT * FROM pins ORDER BY start"):
            pins.setdefault(row["task_id"], []).append(
                {"id": row["id"], "start": row["start"], "end": row["end"]})
        week_ago = (moment - timedelta(days=7)).isoformat()
        shortfall = self._meta("shortfall") or {}
        pads = self._meta("pads") or {}
        inbox = self._match()
        rows = self.db.execute(
            "SELECT * FROM tasks WHERE status IN ('open', 'blocked')"
            " OR (status = 'done' AND closed >= ?) ORDER BY id", (week_ago,)
        ).fetchall()
        tasks = []
        for row in rows:
            task = {k: row[k] for k in row.keys()}
            for flag in ("deadline_hard", "splittable", "late"):
                task[flag] = bool(task[flag])
            task["parsed"] = {0: "pending", 1: "done", -1: "failed"}[row["parsed"]]
            task["worked_min"] = worked.get(row["id"], 0)
            task["blocks"] = blocks.get(row["id"], [])
            task["pins"] = pins.get(row["id"], [])
            # How a late task falls short: minutes with no room at all, and minutes
            # booked past its deadline. Absent when it fits.
            task["shortfall"] = (shortfall.get(str(row["id"])) if task["late"] else None)
            # Planned at estimate × this, learned from how long such work has taken.
            task["pad"] = pads.get(str(row["id"]), 1.0)
            tasks.append(task)

        stamp = _utc(moment)
        flat = sorted((b["start"], b["end"], tid)
                      for source in (blocks, pins) for tid, bs in source.items() for b in bs)
        def described(entry: Any) -> Optional[Dict[str, Any]]:
            if entry is None:
                return None
            title = next((t["title"] for t in tasks if t["id"] == entry[2]), "")
            return {"task_id": entry[2], "title": title, "start": entry[0], "end": entry[1]}

        return {
            "tasks": tasks,
            "now": described(next((b for b in flat if b[0] <= stamp < b[1]), None)),
            "next": described(next((b for b in flat if b[0] > stamp), None)),
            "replanned": self._meta("replanned"),
            "hours": self.hours(),
            "inbox": inbox,
            # The work of finished tasks, for the calendar to draw as done: clocked
            # sessions by id, and time you entered, with where it falls.
            "done_work": self._done_work(),
            "suggestions": self.suggest(),
            "calendar": self._meta("calendar") or {"error": None,
                                                   "available": self.calendar is not None},
        }
