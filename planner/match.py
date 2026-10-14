"""Session -> task matching: which planner task a session you started by hand was for.

Pure: sessions, tasks and history in, scored candidates out. The cascade is the
cheap end of the spec's, cheapest first -- it runs on every refresh, in
milliseconds, and never blocks a start:

1. **alias** -- you linked this exact name to a task before;
2. **context** -- the session ran inside one of the task's planned blocks, or
   carries on from a session of that task that ended under two hours earlier;
3. **text** -- trigram similarity between the session's name and the task's
   title (and project).

A score of 0.8 or more links the session on its own; between 0.4 and 0.8 the
best few are offered as one-tap choices; below that the session waits, unsorted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Set, Tuple

AUTO = 0.8
SUGGEST = 0.4
CONTINUES_WITHIN = timedelta(hours=2)

Interval = Tuple[datetime, datetime]


@dataclass(frozen=True)
class Session:
    id: str
    label: Optional[str]
    start: datetime
    end: datetime


@dataclass(frozen=True)
class Task:
    id: int
    title: str
    project: Optional[str] = None
    blocks: Tuple[Interval, ...] = ()


@dataclass(frozen=True)
class Candidate:
    task_id: int
    score: float
    reason: str


@dataclass
class History:
    """What earlier choices teach: names you linked, and which task each earlier session was."""

    aliases: Dict[str, int] = field(default_factory=dict)
    linked: List[Tuple[Session, int]] = field(default_factory=list)


def normalise(text: Optional[str]) -> str:
    """Lowercase words only: "ReQ: billing-fix!" -> "req billing fix"."""
    return " ".join(re.findall(r"\w+", (text or "").casefold()))


def _trigrams(text: str) -> Set[str]:
    grams: Set[str] = set()
    for word in text.split():
        padded = f"  {word} "
        grams.update(padded[i:i + 3] for i in range(len(padded) - 2))
    return grams


def similarity(a: Optional[str], b: Optional[str]) -> float:
    """Trigram Jaccard of two names, 0..1 -- forgiving of typos, order and word endings."""
    left, right = _trigrams(normalise(a)), _trigrams(normalise(b))
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _overlap(session: Session, block: Interval) -> float:
    """The share of the session that fell inside ``block``."""
    span = (session.end - session.start).total_seconds()
    inside = (min(session.end, block[1]) - max(session.start, block[0])).total_seconds()
    return max(0.0, inside) / span if span > 0 else 0.0


def candidates(session: Session, tasks: Sequence[Task], history: History) -> List[Candidate]:
    """Every task worth naming for ``session``, best first."""
    name = normalise(session.label)
    found: List[Candidate] = []
    for task in tasks:
        signals: List[Tuple[float, str]] = []

        if name and history.aliases.get(name) == task.id:
            signals.append((1.0, "you linked this name to it before"))

        inside = max((_overlap(session, block) for block in task.blocks), default=0.0)
        if inside >= 0.5:
            signals.append((0.85, "ran during its planned block"))
        elif inside > 0:
            signals.append((0.55, "overlapped its planned block"))

        for earlier, earlier_task in history.linked:
            gap = session.start - earlier.end
            if earlier_task == task.id and timedelta(0) <= gap <= CONTINUES_WITHIN and (
                not name or normalise(earlier.label) == name
            ):
                signals.append((0.8, "carries on from the session before it"))
                break

        text = max(similarity(session.label, task.title),
                   similarity(session.label, f"{task.project or ''} {task.title}"))
        if text > 0:
            signals.append((text, "its name is like the task's"))

        if not signals:
            continue
        score, reason = max(signals)
        # Two independent signals agreeing is worth more than either alone.
        if inside > 0 and text >= 0.3:
            score = min(1.0, score + 0.1)
        found.append(Candidate(task.id, round(score, 2), reason))

    return sorted(found, key=lambda c: -c.score)
