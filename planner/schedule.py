"""The scheduler: open tasks + busy time in, blocks out. Pure -- no I/O, no clock.

Greedy, in three steps:

1. free slots = working hours − busy events (each padded by a buffer);
2. tasks in order of deadline, then priority;
3. each task takes the earliest slots that fit it -- deep work trying the
   deep-work hours first -- split into chunks of at least ``min_chunk`` when it
   is splittable, in one piece when it is not.

A task that cannot be placed in full, or only after its deadline, is *late*:
it is still placed as far as it goes, and flagged, never silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional, Tuple

Window = Tuple[time, time]

#: Monday=0 .. Sunday=6 -> the windows the planner may fill. The defaults; the
#: ones in use are edited from the viewer and kept in planner.db.
WORK_HOURS: Dict[int, List[Window]] = {day: [(time(9), time(18))] for day in range(5)}
#: The hours deep work is placed into first.
DEEP_HOURS: Window = (time(9), time(12))
#: Kept clear on each side of a calendar event and between planned blocks. The
#: default; the one in use is edited with the hours and kept in planner.db.
BUFFER = timedelta(minutes=10)
#: Blocks start on this grid, so the calendar reads 10:15, not 10:13.
GRID = 5

Interval = Tuple[datetime, datetime]


@dataclass(frozen=True)
class Job:
    """What the scheduler needs to know about one task."""

    id: int
    minutes: int  # still to do
    deadline: Optional[datetime] = None
    priority: int = 2  # 1 high .. 3 low
    earliest: Optional[datetime] = None
    splittable: bool = True
    min_chunk: int = 30
    deep: bool = False
    #: No block runs longer; a job longer than this is split even if not splittable.
    max_chunk: Optional[int] = None


@dataclass(frozen=True)
class Block:
    task_id: int
    start: datetime
    end: datetime


def _ceil_grid(moment: datetime) -> datetime:
    moment = moment.replace(second=0, microsecond=0)
    return moment + timedelta(minutes=-moment.minute % GRID)


def free_slots(
    now: datetime,
    busy: List[Interval],
    days: int,
    hours: Optional[Dict[int, List[Window]]] = None,
    deep: Optional[Window] = DEEP_HOURS,
    buffer: timedelta = BUFFER,
) -> List[Interval]:
    """The working windows from ``now`` for ``days`` days, minus busy time."""
    hours = WORK_HOURS if hours is None else hours
    padded = sorted((s - buffer, e + buffer) for s, e in busy)
    slots: List[Interval] = []
    for offset in range(days):
        day = (now + timedelta(days=offset)).date()
        for opens, closes in hours.get(day.weekday(), []):
            cursor = max(datetime.combine(day, opens, now.tzinfo), _ceil_grid(now))
            end = datetime.combine(day, closes, now.tzinfo)
            for busy_start, busy_end in padded:
                if busy_end <= cursor or busy_start >= end:
                    continue
                if busy_start > cursor:
                    slots.append((cursor, busy_start))
                cursor = max(cursor, _ceil_grid(busy_end))
            if cursor < end:
                slots.append((cursor, end))
    # Cut at the deep-hours edges, so every slot is wholly deep or wholly not.
    cut: List[Interval] = []
    for start, end in slots:
        for edge in deep or ():
            mark = datetime.combine(start.date(), edge, start.tzinfo)
            if start < mark < end:
                cut.append((start, mark))
                start = mark
        cut.append((start, end))
    return [(s, e) for s, e in cut if e - s >= timedelta(minutes=GRID)]


@dataclass(frozen=True)
class Shortfall:
    """Why a job is late: minutes not placed at all, and minutes placed past its deadline."""

    unplaced: int
    past_deadline: int


def _without(free: List[Interval], lo: datetime, hi: datetime) -> List[Interval]:
    """Every free slot with [lo, hi) cut out of it."""
    kept: List[Interval] = []
    for start, end in free:
        for s, e in ((start, min(end, lo)), (max(start, hi), end)):
            if e - s >= timedelta(minutes=GRID):
                kept.append((s, e))
    return kept


def part_of_day(moment: datetime) -> str:
    """The same three parts the analysis judges slots by (see stats.py)."""
    return "morning" if moment.hour < 12 else "afternoon" if moment.hour < 17 else "evening"


def plan(
    jobs: List[Job],
    slots: List[Interval],
    deep: Optional[Window] = DEEP_HOURS,
    buffer: timedelta = BUFFER,
    avoid: frozenset = frozenset(),
) -> Tuple[List[Block], Dict[int, Shortfall]]:
    """Place ``jobs`` into ``slots``, ``buffer`` apart.

    ``avoid`` holds (weekday, part of day) slots that history says usually go
    unworked; deep work tries everywhere else first.

    Returns the blocks, and for each late job how far short it falls.
    """
    far = datetime.max.replace(tzinfo=slots[0][0].tzinfo) if slots else None
    # ponytail: deadline-then-priority ordering; slack-based (deadline − work left)
    # ordering if long tasks keep losing to short ones with nearer deadlines.
    order = sorted(jobs, key=lambda j: (j.deadline or far or datetime.max, j.priority, j.id))
    free = list(slots)
    blocks: List[Block] = []
    late: Dict[int, Shortfall] = {}

    def is_deep(slot: Interval) -> bool:
        return deep is not None and deep[0] <= slot[0].time() < deep[1]

    for job in order:
        left = job.minutes
        # Too long for one sitting is split, whether or not it was marked splittable.
        split = job.splittable or (job.max_chunk is not None and job.minutes > job.max_chunk)
        deadline = job.deadline or far
        while left > 0:
            # Before the deadline first; deep work into deep hours first; then time.
            free.sort(key=lambda s: (deadline is not None and s[0] >= deadline,
                                     job.deep and (s[0].weekday(), part_of_day(s[0])) in avoid,
                                     job.deep and not is_deep(s), s[0]))
            placed = False
            for start, end in free:
                begin = max(start, _ceil_grid(job.earliest)) if job.earliest else start
                room = int((end - begin).total_seconds() // 60)
                need = left if not split else min(left, job.min_chunk)
                if room < need:
                    continue
                take = left if not split else min(left, room, job.max_chunk or left)
                # Never leave a scrap shorter than a session behind: take less now.
                if 0 < left - take < job.min_chunk:
                    take = left - job.min_chunk
                    if take < need:
                        continue
                stop = begin + timedelta(minutes=take)
                blocks.append(Block(job.id, begin, stop))
                # The block and its buffer leave every slot -- not only this one,
                # since a slot cut at the deep-hours edge has a neighbour touching it.
                free = _without(free, begin - buffer, _ceil_grid(stop + buffer))
                left -= take
                placed = True
                break
            if not placed:
                break
        mine = [b for b in blocks if b.task_id == job.id]
        past = sum(
            int((b.end - max(b.start, job.deadline)).total_seconds() // 60)
            for b in mine if job.deadline and b.end > job.deadline
        )
        if left > 0 or past > 0:
            late[job.id] = Shortfall(unplaced=left, past_deadline=past)

    blocks.sort(key=lambda b: b.start)
    return blocks, late
