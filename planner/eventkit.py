"""Calls the Swift EventKit bridge, compiling it on first use (macOS only)."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

SOURCE = Path(__file__).with_name("eventkit.swift")


class CalendarError(Exception):
    pass


def _iso(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))  # 3.9 cannot read "Z"


class AppleCalendar:
    """Busy time from every calendar, and the plan written to one called "Planned"."""

    def __init__(self, build_dir: Path) -> None:
        self._binary = build_dir / "eventkit"

    def _run(self, *args: str, stdin: str = "") -> str:
        if not self._binary.exists() or self._binary.stat().st_mtime < SOURCE.stat().st_mtime:
            self._binary.parent.mkdir(parents=True, exist_ok=True)
            built = subprocess.run(["swiftc", "-O", str(SOURCE), "-o", str(self._binary)],
                                   capture_output=True, text=True)
            if built.returncode != 0:
                raise CalendarError(f"could not build the calendar bridge: {built.stderr.strip()}")
        done = subprocess.run([str(self._binary), *args], input=stdin,
                              capture_output=True, text=True, timeout=60)
        if done.returncode != 0:
            raise CalendarError(done.stderr.strip() or "calendar bridge failed")
        return done.stdout

    def events(self, start: datetime, end: datetime) -> List[Dict[str, str]]:
        """Busy events with their titles: ``{"start", "end", "title", "calendar"}``."""
        return json.loads(self._run("busy", start.isoformat(), end.isoformat()))

    def busy(self, start: datetime, end: datetime) -> List[Tuple[datetime, datetime]]:
        return [(_iso(e["start"]), _iso(e["end"])) for e in self.events(start, end)]

    def edit(self, change: Dict[str, str]) -> None:
        """Change one occurrence of an event: ``{"id", "occurrence", "title"?, "start"?, "end"?}``."""
        around = _iso(change["occurrence"])
        self._run("edit", (around - timedelta(days=1)).isoformat(),
                  (around + timedelta(days=1)).isoformat(), stdin=json.dumps(change))

    def write(self, start: datetime, end: datetime, events: List[Dict[str, str]]) -> None:
        self._run("write", start.isoformat(), end.isoformat(), stdin=json.dumps(events))
