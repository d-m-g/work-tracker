"""What the event log and the tracker say about how you actually work. Pure.

* **Estimates** -- actual ÷ estimate for finished tasks, by project, kind and the
  time of day the work began. 1.0 is a good guess; 2.0 took twice as long.
* **Dodging** -- open tasks you keep moving, and whose planned blocks keep passing
  unworked, with how long they have been around.
* **Slots** -- for each weekday and part of the day, how many planned blocks were
  actually worked when they came round ("Friday afternoons rarely happen").
* **Fast wins** -- kinds of work that reliably come in under estimate.
* **Punctuality** -- of the planned blocks you worked, how many began on time (within
  ten minutes) and how late the rest typically were; and how many you missed.

And the feedback the scheduler takes from it: a padding ratio per project and kind
(once there are enough finished tasks to trust one), and the slots deep work should
stay out of (once enough blocks have shown they are usually skipped).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Tuple

#: How many finished tasks a ratio needs before it is trusted to pad estimates.
PAD_AFTER = 3
#: How many planned blocks a slot needs before it can be judged unreliable.
JUDGE_SLOT_AFTER = 4
#: A slot worked less often than this is one deep work is kept out of.
UNRELIABLE = 0.4
#: Starting within this many minutes of a planned block counts as on time.
ON_TIME = 10

PARTS = ("morning", "afternoon", "evening")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def part_of_day(moment: datetime) -> str:
    hour = moment.astimezone().hour
    return "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"


def _ratios(groups: Dict[str, List[float]]) -> List[Dict[str, Any]]:
    return sorted(
        ({"key": key, "ratio": round(median(values), 2), "n": len(values)}
         for key, values in groups.items() if values),
        key=lambda row: -row["n"],
    )


def compute(
    tasks: Iterable[Dict[str, Any]],
    work: Iterable[Tuple[int, datetime, int]],
    events: Iterable[Tuple[Optional[int], datetime, str, Dict[str, Any]]],
    now: datetime,
) -> Dict[str, Any]:
    """``work`` is (task id, session start, worked seconds); ``events`` the task log."""
    tasks = list(tasks)
    worked: Dict[int, int] = defaultdict(int)
    first_start: Dict[int, datetime] = {}
    week_worked = 0
    for task_id, start, seconds in work:
        worked[task_id] += seconds
        if task_id not in first_start or start < first_start[task_id]:
            first_start[task_id] = start
        if start >= now - timedelta(days=7):
            week_worked += seconds

    # -- estimates ------------------------------------------------------------
    by: Dict[str, Dict[str, List[float]]] = {"project": defaultdict(list), "type": defaultdict(list),
                                             "time": defaultdict(list)}
    for task in tasks:
        if task["status"] != "done" or not worked.get(task["id"]) or not task["estimate_min"]:
            continue
        ratio = worked[task["id"]] / 60 / task["estimate_min"]
        by["project"][task["project"] or "no project"].append(ratio)
        by["type"][task["type"]].append(ratio)
        if task["id"] in first_start:
            by["time"][part_of_day(first_start[task["id"]])].append(ratio)
    estimates = {name: _ratios(groups) for name, groups in by.items()}

    # -- dodging and slots ----------------------------------------------------
    pushed: Dict[int, int] = defaultdict(int)
    skipped: Dict[int, int] = defaultdict(int)
    slots: Dict[Tuple[int, str], List[int]] = defaultdict(lambda: [0, 0])  # [worked, skipped]
    week_blocks = [0, 0]
    # One outcome per block. A block judged skipped and later found worked (its
    # session was linked afterwards) is worked: the later word wins.
    outcomes: Dict[Tuple[Optional[int], str], Tuple[str, Dict[str, Any]]] = {}
    for task_id, at, kind, data in events:
        if kind == "rescheduled" and data.get("by") == "user" and task_id is not None:
            pushed[task_id] += 1
        elif kind in ("block_done", "skipped") and data.get("start"):
            key = (task_id, data["start"])
            if kind == "block_done" or key not in outcomes:
                outcomes[key] = (kind, data)

    lateness: Dict[str, Dict[str, List[int]]] = {"type": defaultdict(list), "time": defaultdict(list)}
    late_all: List[int] = []
    missed = 0
    kinds = {t["id"]: t["type"] for t in tasks}
    for (task_id, raw_start), (kind, data) in outcomes.items():
        start = datetime.fromisoformat(raw_start).astimezone()
        hit = kind == "block_done"
        slots[(start.weekday(), part_of_day(start))][0 if hit else 1] += 1
        if not hit and task_id is not None:
            skipped[task_id] += 1
            missed += 1
        if start >= now - timedelta(days=7):
            week_blocks[0 if hit else 1] += 1
        if hit and data.get("late_min") is not None:
            late_all.append(data["late_min"])
            lateness["time"][part_of_day(start)].append(data["late_min"])
            if task_id in kinds:
                lateness["type"][kinds[task_id]].append(data["late_min"])

    def punctual(values: List[int]) -> Dict[str, Any]:
        on_time = sum(1 for v in values if v <= ON_TIME)
        late = [v for v in values if v > ON_TIME]
        return {"n": len(values), "on_time": round(on_time / len(values), 2),
                "late_by": int(median(late)) if late else 0}

    dodged = sorted(
        ({"task_id": t["id"], "title": t["title"], "pushed": pushed[t["id"]], "skipped": skipped[t["id"]],
          "age_days": (now - datetime.fromisoformat(t["created"])).days}
         for t in tasks if t["status"] in ("open", "blocked") and pushed[t["id"]] + skipped[t["id"]] > 0),
        key=lambda row: -(row["pushed"] + row["skipped"]),
    )
    slot_rows = [
        {"weekday": day, "part": part, "worked": counts[0], "skipped": counts[1],
         "rate": round(counts[0] / (counts[0] + counts[1]), 2)}
        for (day, part), counts in sorted(slots.items())
    ]

    # -- what the scheduler takes back ---------------------------------------
    padding = {name: {row["key"]: max(0.5, min(3.0, row["ratio"]))
                      for row in estimates[name] if row["n"] >= PAD_AFTER}
               for name in ("project", "type")}
    avoid = [(row["weekday"], row["part"]) for row in slot_rows
             if row["worked"] + row["skipped"] >= JUDGE_SLOT_AFTER and row["rate"] < UNRELIABLE]

    blocks_judged = len(late_all) + missed
    return {
        "punctuality": {
            "overall": punctual(late_all) if late_all else None,
            "missed": round(missed / blocks_judged, 2) if blocks_judged else None,
            "blocks": blocks_judged,
            **{name: sorted(({"key": key, **punctual(values)} for key, values in groups.items()),
                            key=lambda row: -row["n"])
               for name, groups in lateness.items()},
        },
        "estimates": estimates,
        "fast_wins": [row for name in ("project", "type") for row in estimates[name]
                      if row["ratio"] < 0.8 and row["n"] >= 2],
        "dodged": dodged,
        "slots": slot_rows,
        "padding": padding,
        "avoid": [{"weekday": d, "part": p} for d, p in avoid],
        "week": {
            "worked_seconds": week_worked,
            "done": sum(1 for t in tasks if t["status"] == "done" and t.get("closed")
                        and datetime.fromisoformat(t["closed"]) >= now - timedelta(days=7)),
            "blocks_worked": week_blocks[0],
            "blocks_skipped": week_blocks[1],
        },
    }


def pad_for(task: Dict[str, Any], padding: Dict[str, Dict[str, float]]) -> float:
    """The ratio to plan this task's estimate by: its project's if known, else its kind's."""
    return padding["project"].get(task["project"] or "no project") or padding["type"].get(task["type"]) or 1.0
