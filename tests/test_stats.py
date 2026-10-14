"""The analysis layer: ratios, dodging, slot reliability, and what feeds back."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from support import TZ

from planner.stats import compute, pad_for

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=TZ)


def task(tid, project="ReQ", kind="deep", estimate=60, status="done"):
    return {"id": tid, "title": f"t{tid}", "project": project, "type": kind, "estimate_min": estimate,
            "status": status, "created": (NOW - timedelta(days=9)).isoformat(),
            "closed": (NOW - timedelta(days=1)).isoformat() if status == "done" else None}


class TestEstimates(unittest.TestCase):
    def test_a_project_that_runs_long_is_padded_once_trusted(self) -> None:
        tasks = [task(i) for i in range(3)]
        work = [(i, NOW - timedelta(days=2, hours=i), 120 * 60) for i in range(3)]  # 2h of 1h each
        stats = compute(tasks, work, [], NOW)
        self.assertEqual({"key": "ReQ", "ratio": 2.0, "n": 3}, stats["estimates"]["project"][0])
        self.assertEqual(2.0, pad_for(tasks[0], stats["padding"]))

    def test_too_few_tasks_are_not_trusted(self) -> None:
        stats = compute([task(1)], [(1, NOW - timedelta(days=1), 7200)], [], NOW)
        self.assertEqual(1.0, pad_for(task(1), stats["padding"]))

    def test_quick_kinds_are_fast_wins(self) -> None:
        tasks = [task(i, project=None, kind="shallow") for i in range(2)]
        work = [(i, NOW - timedelta(days=1), 30 * 60) for i in range(2)]
        wins = compute(tasks, work, [], NOW)["fast_wins"]
        self.assertIn({"key": "shallow", "ratio": 0.5, "n": 2}, wins)


class TestSlotsAndDodging(unittest.TestCase):
    def skipped_on(self, day_offset, hour, tid=9):
        start = (NOW - timedelta(days=day_offset)).replace(hour=hour)
        return (tid, start, "skipped", {"start": start.isoformat()})

    def test_a_slot_usually_skipped_is_avoided(self) -> None:
        events = [self.skipped_on(7 * week, 15) for week in range(1, 5)]  # same weekday afternoon
        stats = compute([task(9, status="open")], [], events, NOW)
        day = (NOW - timedelta(days=7)).weekday()
        self.assertIn({"weekday": day, "part": "afternoon"}, stats["avoid"])

    def test_a_task_moved_and_skipped_is_dodged(self) -> None:
        events = [(9, NOW, "rescheduled", {"by": "user"}), self.skipped_on(1, 10)]
        dodged = compute([task(9, status="open")], [], events, NOW)["dodged"]
        self.assertEqual([1, 1], [dodged[0]["pushed"], dodged[0]["skipped"]])
        self.assertEqual(9, dodged[0]["age_days"])


if __name__ == "__main__":
    unittest.main()
