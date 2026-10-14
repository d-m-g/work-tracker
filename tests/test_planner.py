"""The planner: the scheduler's placement rules, and one pass through the service."""

from __future__ import annotations

import json
import os
import unittest
from unittest import mock
from datetime import datetime, time, timedelta

from support import TZ, TrackerTestCase

from planner.parse import ParseError
from planner.planner import Planner, PlannerError, checked_hours
from tracker.models import CompletedSession
from planner.schedule import Job, free_slots, plan

MONDAY = datetime(2026, 10, 5, 8, 0, tzinfo=TZ)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """Monday 5 Oct 2026 + ``day`` days, at hour:minute."""
    return (MONDAY + timedelta(days=day)).replace(hour=hour, minute=minute)


class TestFreeSlots(unittest.TestCase):
    def test_busy_time_is_cut_out_with_a_buffer(self) -> None:
        slots = free_slots(MONDAY, [(at(0, 13), at(0, 14))], days=1)
        self.assertEqual(
            [(at(0, 9), at(0, 12)), (at(0, 12), at(0, 12, 50)), (at(0, 14, 10), at(0, 18))],
            slots,
        )

    def test_weekends_and_the_past_are_not_free(self) -> None:
        friday_late = at(4, 17, 2)
        slots = free_slots(friday_late, [], days=3)
        self.assertEqual([(at(4, 17, 5), at(4, 18))], slots)


class TestPlan(unittest.TestCase):
    def test_earlier_deadline_goes_first(self) -> None:
        slots = free_slots(MONDAY, [], days=1)
        blocks, late = plan(
            [Job(1, 60), Job(2, 60, deadline=at(0, 12))], slots)
        self.assertEqual([2, 1], [b.task_id for b in blocks])
        self.assertEqual({}, late)

    def test_deep_work_prefers_the_morning(self) -> None:
        slots = free_slots(at(0, 11), [], days=2)
        blocks, _ = plan([Job(1, 120, deep=True, splittable=False)], slots)
        self.assertEqual(at(1, 9), blocks[0].start)

    def test_a_splittable_task_is_chunked_around_a_meeting(self) -> None:
        slots = free_slots(MONDAY, [(at(0, 10), at(0, 17))], days=1)
        blocks, late = plan([Job(1, 90, min_chunk=30)], slots)
        self.assertEqual(90, sum((b.end - b.start).seconds // 60 for b in blocks))
        self.assertEqual(2, len(blocks))
        self.assertEqual({}, late)

    def test_a_task_that_cannot_fit_before_its_deadline_is_late(self) -> None:
        slots = free_slots(MONDAY, [], days=2)
        blocks, late = plan([Job(1, 600, deadline=at(0, 18))], slots)
        self.assertEqual([1], list(late))
        self.assertTrue(blocks)  # placed as far as it goes, not dropped

    def test_earliest_start_is_respected(self) -> None:
        blocks, _ = plan([Job(1, 30, earliest=at(0, 15))], free_slots(MONDAY, [], days=1))
        self.assertEqual(at(0, 15), blocks[0].start)


class TestHours(unittest.TestCase):
    def test_a_weekend_window_is_planned_into(self) -> None:
        saturday = at(5, 8)
        hours = {5: [(time(10), time(14))]}
        blocks, late = plan([Job(1, 60)], free_slots(saturday, [], 1, hours))
        self.assertEqual(at(5, 10), blocks[0].start)
        self.assertEqual({}, late)

    def test_a_lunch_gap_is_never_filled(self) -> None:
        hours = {0: [(time(9), time(12)), (time(13), time(18))]}
        blocks, _ = plan([Job(1, 240, min_chunk=30)], free_slots(MONDAY, [], 1, hours, None), None)
        for block in blocks:
            self.assertFalse(block.start < at(0, 13) and block.end > at(0, 12), block)

    def test_bad_hours_are_refused(self) -> None:
        for days in ({"0": [["18:00", "09:00"]]},
                     {"0": [["09:00", "13:00"], ["12:00", "15:00"]]},
                     {"0": [["9am", "5pm"]]}):
            with self.assertRaises(PlannerError):
                checked_hours({"days": days, "deep": None})

    def test_good_hours_are_normalised(self) -> None:
        hours = checked_hours({"days": {"6": [["14:00", "16:00"], ["10:00", "12:00"]]},
                               "deep": ["09:00", "11:00"]})
        self.assertEqual([["10:00", "12:00"], ["14:00", "16:00"]], hours["days"]["6"])
        self.assertEqual([], hours["days"]["0"])


class TestSessionsAndBuffers(unittest.TestCase):
    def test_planned_blocks_are_kept_a_buffer_apart(self) -> None:
        slots = free_slots(MONDAY, [], 1, deep=None)
        blocks, _ = plan([Job(1, 60), Job(2, 60)], slots, None, timedelta(minutes=15))
        self.assertEqual(blocks[0].end + timedelta(minutes=15), blocks[1].start)

    def test_the_buffer_holds_across_the_deep_hours_edge(self) -> None:
        # 09:00-12:00 and 12:00-18:00 are two slots that touch; the buffer spans both.
        slots = free_slots(MONDAY, [], 1)
        blocks, _ = plan([Job(1, 180, splittable=False), Job(2, 30)], slots,
                         buffer=timedelta(minutes=10))
        self.assertEqual(at(0, 12, 10), blocks[1].start)

    def test_anything_longer_than_a_session_is_split_even_unticked(self) -> None:
        slots = free_slots(MONDAY, [], 2, deep=None)
        blocks, late = plan([Job(1, 300, splittable=False, max_chunk=180)], slots, None)
        self.assertEqual({}, late)
        self.assertTrue(all((b.end - b.start) <= timedelta(minutes=180) for b in blocks))
        self.assertEqual(300, sum((b.end - b.start).seconds // 60 for b in blocks))

    def test_a_split_never_leaves_a_scrap_shorter_than_a_session(self) -> None:
        # 115 free minutes, then an afternoon: 115 + 5 would leave a 5-minute block.
        slots = [(at(0, 9), at(0, 10, 55)), (at(0, 14), at(0, 18))]
        blocks, late = plan([Job(1, 120, min_chunk=30)], slots, None, timedelta(0))
        self.assertEqual({}, late)
        self.assertEqual([90, 30], [(b.end - b.start).seconds // 60 for b in blocks])

    def test_a_late_job_says_how_far_short_it_falls(self) -> None:
        slots = free_slots(MONDAY, [], 1, deep=None)  # 9 hours on Monday only
        _, late = plan([Job(1, 600)], slots, None)
        self.assertEqual(60, late[1].unplaced)

    def test_session_settings_are_checked(self) -> None:
        with self.assertRaises(PlannerError):
            checked_hours({"days": {}, "min_session": 120, "max_session": 60})
        with self.assertRaises(PlannerError):
            checked_hours({"days": {}, "buffer": 90})
        hours = checked_hours({"days": {}, "buffer": 0, "min_session": 45, "max_session": 120})
        self.assertEqual((0, 45, 120), (hours["buffer"], hours["min_session"], hours["max_session"]))


class FakeCalendar:
    def __init__(self) -> None:
        self.written: list = []
        self.edits: list = []

    def edit(self, change) -> None:
        if change["id"] == "locked":
            raise RuntimeError("the Lectures calendar is read-only")
        self.edits.append(change)

    def busy(self, start, end):
        return []

    def write(self, start, end, events) -> None:
        self.written = events


def fake_parser(text, now):
    if "fail" in text:
        raise ParseError("nope")
    return {"title": text.upper(), "estimate_min": 90, "type": "deep"}


class TestPlanner(TrackerTestCase):
    def setUp(self) -> None:
        super().setUp()
        # Empty, not unset: "no VM" -- and it keeps the planner from adopting the
        # widget's real VM from `defaults`, which would sync this temp dir to it.
        patcher = mock.patch.dict(os.environ, {"WORK_TRACKER_SSH": ""})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.calendar = FakeCalendar()
        self.planner = Planner(self.root, parser=fake_parser, calendar=self.calendar)
        self.addCleanup(self.planner.db.close)

    def events(self, task_id: int) -> list:
        return [row[0] for row in self.planner.db.execute(
            "SELECT kind FROM task_events WHERE task_id = ? ORDER BY id", (task_id,))]

    def test_add_parse_and_schedule(self) -> None:
        task_id = self.planner.add("write the copy")
        self.planner.parse(task_id)
        task = self.planner.payload()["tasks"][0]
        self.assertEqual("WRITE THE COPY", task["title"])
        self.assertEqual("done", task["parsed"])
        self.assertTrue(task["blocks"])
        self.assertEqual("WRITE THE COPY", self.calendar.written[0]["title"])
        self.assertEqual(["created", "parsed", "scheduled"], self.events(task_id))

    def test_a_failed_parse_keeps_the_text(self) -> None:
        task_id = self.planner.add("this will fail")
        self.planner.parse(task_id)
        task = self.planner.payload()["tasks"][0]
        self.assertEqual("this will fail", task["title"])
        self.assertEqual("failed", task["parsed"])

    def test_start_and_stop_go_through_the_tracker_with_the_task_id(self) -> None:
        task_id = self.planner.add("billing fix")
        self.planner.parse(task_id)
        self.planner.edit(task_id, {"project": "ReQ"})
        self.planner.start(task_id)
        current = json.loads((self.root / "current.json").read_text())
        self.assertEqual(str(task_id), current["taskId"])
        self.assertEqual("ReQ", current["project"])

        self.planner.stop("done")
        self.assertFalse((self.root / "current.json").exists())
        self.assertEqual("done", self.planner.payload()["tasks"][0]["status"])
        self.assertEqual(["started", "stopped", "completed"], self.events(task_id)[-3:])

    def test_a_recurring_task_comes_back_when_done(self) -> None:
        task_id = self.planner.add("standup notes")
        self.planner.edit(task_id, {"recurrence": "daily", "deadline": "2026-10-05T18:00+03:00"})
        self.planner.done(task_id)
        tasks = self.planner.payload()["tasks"]
        self.assertEqual(2, len(tasks))
        self.assertTrue(tasks[1]["deadline"].startswith("2026-10-06T18:00"))

    def test_saved_hours_drive_the_plan(self) -> None:
        every_day = {"days": {str(d): [["06:00", "06:30"]] for d in range(7)}, "deep": None}
        self.planner.set_hours(every_day)
        task_id = self.planner.add("x")
        self.planner.parse(task_id)
        block = self.planner.payload()["tasks"][0]["blocks"][0]
        local = datetime.fromisoformat(block["start"]).astimezone()
        self.assertEqual("06:00", local.strftime("%H:%M"))
        self.assertEqual(every_day["days"], self.planner.payload()["hours"]["days"])

    def _next_monday(self, hour: int) -> datetime:
        today = datetime.now().astimezone()
        ahead = (7 - today.weekday()) % 7 or 7
        return (today + timedelta(days=ahead)).replace(hour=hour, minute=0, second=0, microsecond=0)

    def test_a_static_block_stays_and_the_rest_is_planned_around_it(self) -> None:
        task_id = self.planner.add("write the copy")
        self.planner.parse(task_id)  # 90 minutes, deep
        evening = self._next_monday(20)  # outside working hours: allowed for a pin
        self.planner.pin(task_id, evening.isoformat(), (evening + timedelta(minutes=60)).isoformat())

        task = self.planner.payload()["tasks"][0]
        self.assertEqual(1, len(task["pins"]))
        self.assertEqual(evening, datetime.fromisoformat(task["pins"][0]["start"]))
        planned = sum((datetime.fromisoformat(b["end"]) - datetime.fromisoformat(b["start"])).seconds
                      for b in task["blocks"]) // 60
        self.assertEqual(30, planned)  # 90 estimated − 60 pinned
        self.assertIn("pinned", self.events(task_id))

    def test_a_static_block_is_busy_time_for_other_tasks(self) -> None:
        first, second = self.planner.add("one"), self.planner.add("two")
        self.planner.parse(first)
        morning = self._next_monday(9)
        self.planner.pin(first, morning.isoformat(), (morning + timedelta(minutes=90)).isoformat())
        self.planner.parse(second)
        for block in self.planner.payload()["tasks"][1]["blocks"]:
            start = datetime.fromisoformat(block["start"])
            end = datetime.fromisoformat(block["end"])
            self.assertFalse(start < morning + timedelta(minutes=90) and end > morning, block)

    def test_unpinning_hands_the_time_back(self) -> None:
        task_id = self.planner.add("x")
        self.planner.parse(task_id)
        morning = self._next_monday(9)
        self.planner.pin(task_id, morning.isoformat(), (morning + timedelta(minutes=90)).isoformat())
        pin_id = self.planner.payload()["tasks"][0]["pins"][0]["id"]
        self.planner.unpin(pin_id)
        task = self.planner.payload()["tasks"][0]
        self.assertEqual([], task["pins"])
        self.assertTrue(task["blocks"])

    def test_bad_pins_are_refused(self) -> None:
        task_id = self.planner.add("x")
        at9 = self._next_monday(9)
        past = datetime.now().astimezone() - timedelta(days=1)
        for start, end in ((past.isoformat(), (past + timedelta(hours=1)).isoformat()),
                           ("soon", at9.isoformat()),
                           (at9.isoformat(), at9.isoformat()),
                           (at9.replace(tzinfo=None).isoformat(), (at9 + timedelta(hours=1)).isoformat()),
                           (at9.isoformat(), (at9 + timedelta(hours=13)).isoformat())):
            with self.assertRaises(PlannerError):
                self.planner.pin(task_id, start, end)

    def test_an_event_edit_reaches_the_calendar(self) -> None:
        self.planner.edit_event({"id": "E1", "occurrence": "2026-10-05T10:15:00+03:00",
                                 "title": "  Lecture  moved ", "start": "2026-10-05T11:00:00+03:00",
                                 "end": "2026-10-05T12:00:00+03:00"})
        change = self.calendar.edits[0]
        self.assertEqual("Lecture moved", change["title"])
        self.assertEqual("2026-10-05T11:00:00+03:00", change["start"])

    def test_a_read_only_calendar_is_reported_not_crashed(self) -> None:
        with self.assertRaises(PlannerError) as caught:
            self.planner.edit_event({"id": "locked", "occurrence": "2026-10-05T10:15:00+03:00",
                                     "title": "x"})
        self.assertIn("read-only", str(caught.exception))

    def test_bad_event_edits_are_refused(self) -> None:
        for raw in ({"id": "E1"},
                    {"id": "E1", "occurrence": "2026-10-05T10:15:00+03:00", "title": " "},
                    {"id": "E1", "occurrence": "2026-10-05T10:15:00+03:00",
                     "start": "2026-10-05T12:00:00+03:00", "end": "2026-10-05T11:00:00+03:00"}):
            with self.assertRaises(PlannerError):
                self.planner.edit_event(raw)
        self.assertEqual([], self.calendar.edits)

    def test_monthly_repeats_on_the_same_date(self) -> None:
        task_id = self.planner.add("rent")
        self.planner.edit(task_id, {"recurrence": "monthly", "deadline": "2026-01-31T18:00+03:00"})
        self.planner.done(task_id)
        copy = self.planner.payload()["tasks"][1]
        self.assertTrue(copy["deadline"].startswith("2026-02-28T18:00"), copy["deadline"])

    def test_a_deadline_far_off_is_not_planned_before_its_lead(self) -> None:
        far = datetime.now().astimezone() + timedelta(days=12)
        task_id = self.planner.add("x")
        self.planner.edit(task_id, {"deadline": far.isoformat()})
        start = datetime.fromisoformat(self.planner.payload()["tasks"][0]["blocks"][0]["start"])
        self.assertGreaterEqual(start, far - timedelta(days=5))

    def test_a_lead_of_zero_plans_as_soon_as_possible(self) -> None:
        self.planner.set_hours({**self.planner.hours(), "lead_days": 0})
        far = datetime.now().astimezone() + timedelta(days=12)
        task_id = self.planner.add("x")
        self.planner.edit(task_id, {"deadline": far.isoformat()})
        start = datetime.fromisoformat(self.planner.payload()["tasks"][0]["blocks"][0]["start"])
        self.assertLess(start, far - timedelta(days=5))

    def test_something_new_is_made_a_task_and_started(self) -> None:
        task_id = self.planner.start_new("fixing the deploy script")
        current = json.loads((self.root / "current.json").read_text())
        self.assertEqual(str(task_id), current["taskId"])
        self.assertEqual("fixing the deploy script", current["task"])

    def test_a_calendar_it_cannot_read_is_planned_around_as_last_read(self) -> None:
        monday = self._next_monday(9)
        meeting = (monday, monday.replace(hour=18))  # the whole working day, busy
        self.calendar.busy = lambda start, end: [meeting]
        self.planner.replan()  # read once, remembered

        def refused(start, end):
            raise RuntimeError("calendar access refused")
        self.calendar.busy = refused
        task_id = self.planner.add("x")
        self.planner.parse(task_id)
        for block in self.planner.payload()["tasks"][0]["blocks"]:
            start = datetime.fromisoformat(block["start"])
            self.assertFalse(meeting[0] <= start < meeting[1], block)
        self.assertIsNotNone(self.planner.payload()["calendar"]["cached"])

    def test_done_with_entered_time_records_it_just_before(self) -> None:
        task_id = self.planner.add("x")
        before = datetime.now().astimezone()
        self.planner.done(task_id, minutes=45)
        payload = self.planner.payload()
        task = next(t for t in payload["tasks"] if t["id"] == task_id)
        self.assertEqual(45, task["worked_min"])
        entered = payload["done_work"]["entered"][0]
        end = datetime.fromisoformat(entered["end"])
        self.assertLess(abs((end - before).total_seconds()), 60)
        self.assertEqual(timedelta(minutes=45), end - datetime.fromisoformat(entered["start"]))
        with self.assertRaises(PlannerError):
            self.planner.done(self.planner.add("y"), minutes=0)

    def test_entered_work_can_be_moved_but_not_into_the_future(self) -> None:
        self.planner.done(self.planner.add("x"), minutes=30)
        entered = self.planner.payload()["done_work"]["entered"][0]
        start = datetime.fromisoformat(entered["start"]) - timedelta(hours=2)
        self.planner.retime_work(entered["id"], start.isoformat(),
                                 (start + timedelta(minutes=50)).isoformat())
        moved = self.planner.payload()["done_work"]["entered"][0]
        self.assertEqual(start, datetime.fromisoformat(moved["start"]))
        later = datetime.now().astimezone() + timedelta(hours=1)
        with self.assertRaises(PlannerError):
            self.planner.retime_work(entered["id"], later.isoformat(),
                                     (later + timedelta(minutes=30)).isoformat())

    def test_bad_edits_are_refused(self) -> None:
        task_id = self.planner.add("x")
        for change in ({"priority": 9}, {"status": "done"}, {"deadline": "soon"}, {"id": 4}):
            with self.assertRaises(PlannerError):
                self.planner.edit(task_id, change)


if __name__ == "__main__":
    unittest.main()


class TestMatchingAndLearning(TrackerTestCase):
    """The planner's side of matching and analysis, on the tracker's fake clock."""

    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"WORK_TRACKER_SSH": ""})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.planner = Planner(self.root, clock=self.clock, parser=fake_parser, calendar=None,
                               tracker_cli=lambda args: "{}")
        self.addCleanup(self.planner.db.close)

    def manual_session(self, label, minutes=60):
        """A session started by hand, the way the widget or ⌘F8 would."""
        self.tracker.start(label)
        self.clock.advance(minutes * 60)
        completed, _ = self.tracker.stop()
        self.clock.advance(600)
        return completed.id

    def test_a_link_counts_and_its_name_is_learned(self) -> None:
        task_id = self.planner.add("billing")
        first = self.manual_session("pr thing")
        self.planner.link(first, task_id)
        self.assertEqual(60, self.planner.payload()["tasks"][0]["worked_min"])

        second = self.manual_session("pr thing")
        entry = next(e for e in self.planner.payload()["inbox"] if e["session"] == second)
        self.assertEqual(task_id, entry["link"]["task_id"])  # linked on its own, by alias

    def test_a_session_inside_a_planned_block_is_linked_on_its_own(self) -> None:
        task_id = self.planner.add("copy")
        start = self.clock()
        self.planner.pin(task_id, start.isoformat(), (start + timedelta(minutes=90)).isoformat())
        session = self.manual_session(None, minutes=60)
        entry = next(e for e in self.planner.payload()["inbox"] if e["session"] == session)
        self.assertEqual(task_id, entry["link"]["task_id"])

    def test_not_a_task_takes_it_out_of_the_inbox(self) -> None:
        self.planner.add("copy")
        session = self.manual_session("lunch")
        self.planner.link(session, None)
        self.assertNotIn(session, [e["session"] for e in self.planner.payload()["inbox"]])

    def test_a_project_that_runs_long_is_planned_long(self) -> None:
        for _ in range(3):
            task_id = self.planner.add("req job")
            self.planner.edit(task_id, {"project": "ReQ", "estimate_min": 60})
            self.planner.link(self.manual_session("req job", minutes=120), task_id)
            self.planner.done(task_id)
        fresh = self.planner.add("req next")
        self.planner.edit(fresh, {"project": "ReQ", "estimate_min": 60})
        task = next(t for t in self.planner.payload()["tasks"] if t["id"] == fresh)
        self.assertEqual(2.0, task["pad"])
        planned = sum((datetime.fromisoformat(b["end"]) - datetime.fromisoformat(b["start"])).seconds
                      for b in task["blocks"]) // 60
        self.assertEqual(120, planned)

    def test_the_weekly_review_is_kept(self) -> None:
        with mock.patch("planner.parse.review_week", return_value="You dodge nothing.") as asked:
            self.planner.review()
        figures = asked.call_args[0][0]
        self.assertNotIn("titles", figures)  # aggregates, not the raw index
        self.assertEqual("You dodge nothing.", self.planner.stats()["review"]["text"])


class TestMcp(TrackerTestCase):
    """The MCP server, one JSON-RPC line at a time, through the real stdio loop."""

    def ask(self, *messages):
        from io import StringIO

        from planner.mcp import serve
        out = StringIO()
        lines = "".join(json.dumps(m) + "\n" for m in messages)
        serve(lambda: Planner(self.root, parser=fake_parser, calendar=None), StringIO(lines), out)
        return [json.loads(line) for line in out.getvalue().splitlines()]

    def test_the_handshake_and_the_tools(self) -> None:
        answers = self.ask(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        self.assertEqual(2, len(answers))  # the notification gets no reply
        self.assertEqual("2025-06-18", answers[0]["result"]["protocolVersion"])
        names = {tool["name"] for tool in answers[1]["result"]["tools"]}
        self.assertEqual({"add_task", "list_tasks", "update_task", "complete_task", "replan",
                          "query_stats"}, names)

    def test_a_task_added_through_claude_is_planned(self) -> None:
        call = {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "add_task", "arguments": {"text": "write the copy"}}}
        listing = {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                   "params": {"name": "list_tasks", "arguments": {}}}
        added, listed = self.ask(call, listing)
        task = json.loads(added["result"]["content"][0]["text"])
        self.assertEqual("WRITE THE COPY", task["title"])
        self.assertIsNotNone(task["next_block"])
        self.assertEqual(1, len(json.loads(listed["result"]["content"][0]["text"])["tasks"]))

    def test_a_refusal_comes_back_as_a_tool_error(self) -> None:
        (answer,) = self.ask({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                              "params": {"name": "complete_task", "arguments": {"id": 99}}})
        self.assertTrue(answer["result"]["isError"])
        self.assertIn("no such task", answer["result"]["content"][0]["text"])


class TestMissedAndLate(TrackerTestCase):
    """Blocks that start: held, missed, or worked -- and how late."""

    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"WORK_TRACKER_SSH": ""})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.planner = Planner(self.root, clock=self.clock, parser=fake_parser, calendar=None,
                               tracker_cli=lambda args: "{}")
        self.addCleanup(self.planner.db.close)
        # Plannable around the clock, so the first block starts right away.
        self.planner.set_hours({"days": {str(d): [["00:00", "23:59"]] for d in range(7)},
                                "deep": None, "buffer": 0})
        self.task = self.planner.add("write")
        self.planner.edit(self.task, {"estimate_min": 60, "splittable": False})
        self.block = self.first_block()

    def first_block(self):
        task = next(t for t in self.planner.payload()["tasks"] if t["id"] == self.task)
        return datetime.fromisoformat(task["blocks"][0]["start"])

    def kinds(self):
        return [(row[0], json.loads(row[1] or "{}")) for row in self.planner.db.execute(
            "SELECT kind, data FROM task_events WHERE task_id = ? AND kind IN ('block_done', 'skipped')",
            (self.task,))]

    def advance_to(self, moment) -> None:
        self.clock.current = moment

    def test_a_block_within_its_grace_stays_put(self) -> None:
        self.advance_to(self.block + timedelta(minutes=5))
        self.assertEqual(self.block, self.first_block())
        self.assertEqual([], self.kinds())

    def test_a_replan_within_the_grace_leaves_no_scrap_block(self) -> None:
        self.advance_to(self.block + timedelta(minutes=5))
        task = next(t for t in self.planner.replan()["tasks"] if t["id"] == self.task)
        self.assertEqual(1, len(task["blocks"]), task["blocks"])

    def test_a_block_untouched_past_its_grace_is_missed_and_moved(self) -> None:
        self.advance_to(self.block + timedelta(minutes=20))
        moved = self.first_block()  # reading the plan judges it
        self.assertGreater(moved, self.block)
        kinds = self.kinds()
        self.assertEqual(["skipped"], [k for k, _ in kinds])
        self.assertTrue(kinds[0][1]["missed"])

    def test_a_worked_block_records_how_late_it_began(self) -> None:
        self.advance_to(self.block + timedelta(minutes=7))
        self.tracker.start("write", task_id=str(self.task))
        self.advance_to(self.block + timedelta(minutes=50))
        self.assertEqual(self.block, self.first_block())  # held: being worked
        self.tracker.stop()
        self.advance_to(self.block + timedelta(minutes=65))
        self.planner.payload()
        kinds = self.kinds()
        self.assertEqual(["block_done"], [k for k, _ in kinds])
        self.assertEqual(7, kinds[0][1]["late_min"])
        punctuality = self.planner.stats()["punctuality"]
        self.assertEqual({"n": 1, "on_time": 1.0, "late_by": 0}, punctuality["overall"])

    def test_a_late_link_turns_a_skip_into_worked(self) -> None:
        self.advance_to(self.block + timedelta(minutes=70))
        self.planner.payload()
        self.assertEqual(["skipped"], [k for k, _ in self.kinds()])
        # The work happened, logged by hand on another device and only now seen.
        begin = self.block + timedelta(minutes=30)
        self.storage.archive(CompletedSession(
            id=begin.strftime("%Y-%m-%d_%H-%M-%S"), start=begin, end=begin + timedelta(minutes=20),
            gross_seconds=1200, paused_seconds=0, worked_seconds=1200, task="other words"))
        self.planner.link(begin.strftime("%Y-%m-%d_%H-%M-%S"), self.task)
        stats = self.planner.stats()
        self.assertEqual(0, stats["punctuality"]["missed"])
        self.assertEqual(30, [d for k, d in self.kinds() if k == "block_done"][0]["late_min"])

    def test_the_task_planned_now_is_the_first_guess(self) -> None:
        other = self.planner.add("other")
        self.advance_to(self.block + timedelta(minutes=2))
        guesses = self.planner.suggest()
        self.assertEqual(self.task, guesses[0]["id"])
        self.assertEqual("planned now", guesses[0]["reason"])
        self.assertIn(other, [g["id"] for g in self.planner.suggest(limit=5)])
