"""The CalDAV calendar, against a fake server that answers the way iCloud does."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Dict, List, Tuple

from planner import caldav
from planner.caldav import CalDAVCalendar, new_event
from planner.eventkit import CalendarError

HOME = "https://p1-caldav.icloud.com/1/calendars/"


def _ms(*responses: str) -> bytes:
    return ('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" '
            'xmlns:c="urn:ietf:params:xml:ns:caldav" xmlns:a="http://apple.com/ns/ical/">'
            + "".join(responses) + "</d:multistatus>").encode()


def _resp(href: str, props: str) -> str:
    return (f"<d:response><d:href>{href}</d:href><d:propstat><d:prop>{props}</d:prop>"
            "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>")


def _cal(href: str, name: str, writable: bool = True) -> str:
    privilege = "<d:privilege><d:write/></d:privilege>" if writable else "<d:privilege><d:read/></d:privilege>"
    return _resp(href, f"<d:resourcetype><d:collection/><c:calendar/></d:resourcetype>"
                       f"<d:displayname>{name}</d:displayname><a:calendar-color>#112233FF</a:calendar-color>"
                       f"<d:current-user-privilege-set>{privilege}</d:current-user-privilege-set>")


def _data(href: str, ics: str) -> str:
    return _resp(href, f'<d:getetag>"e"</d:getetag><c:calendar-data>{ics}</c:calendar-data>')


WORK_ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:a
DTSTART:20261020T090000Z
DTEND:20261020T100000Z
SUMMARY:Stand\\, up
BEGIN:VALARM
TRIGGER:-PT5M
SUMMARY:not this
END:VALARM
END:VEVENT
BEGIN:VEVENT
UID:b
DTSTART;VALUE=DATE:20261021
SUMMARY:all day
END:VEVENT
BEGIN:VEVENT
UID:c
DTSTART;TZID=Europe/Moscow:20261022T120000
DURATION:PT30M
SUMMARY:lunch
END:VEVENT
BEGIN:VEVENT
UID:d
DTSTART:20261023T090000Z
DTEND:20261023T100000Z
TRANSP:TRANSPARENT
SUMMARY:free
END:VEVENT
END:VCALENDAR"""


class FakeICloud(CalDAVCalendar):
    def __init__(self) -> None:
        self._tmp = TemporaryDirectory()
        password = Path(self._tmp.name) / "pw"
        password.write_text("secret")
        super().__init__("me@icloud.com", password, "https://caldav.icloud.com/")
        self.planned: Dict[str, str] = {}  # href -> ics in "Planned"
        self.calls: List[Tuple[str, str]] = []
        self.files: Dict[str, Tuple[str, Dict[str, str]]] = {}

    def _feed(self, url):
        assert url == "https://uni.example/feed.ics", url
        return ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:l\nDTSTART:20261021T090000Z\n"
                "DTEND:20261021T103000Z\nSUMMARY:Lecture\nEND:VEVENT\nEND:VCALENDAR")

    def _request(self, method, url, body="", headers=None):
        self.calls.append((method, url))
        if method == "PROPFIND" and url == "https://caldav.icloud.com/":
            return 207, _ms(_resp("/", "<d:current-user-principal><d:href>/1/principal/</d:href>"
                                       "</d:current-user-principal>")), {}
        if method == "PROPFIND" and url.endswith("/principal/"):
            return 207, _ms(_resp("/1/principal/", f"<c:calendar-home-set><d:href>{HOME}</d:href>"
                                                   "</c:calendar-home-set>")), {}
        if method == "PROPFIND" and url == HOME:
            return 207, _ms(_resp("/1/calendars/", "<d:resourcetype><d:collection/></d:resourcetype>"),
                            _cal("/1/calendars/work/", "Work"),
                            _cal("/1/calendars/hol/", "Holidays", writable=False),
                            _cal("/1/calendars/plan/", "Planned"),
                            _cal("/1/calendars/fix/", "Planned · static"),
                            _resp("/1/calendars/sub/", "<d:resourcetype><d:collection/><cs:subscribed "
                                  'xmlns:cs="http://calendarserver.org/ns/"/></d:resourcetype>'
                                  "<d:displayname>Lectures</d:displayname><cs:source "
                                  'xmlns:cs="http://calendarserver.org/ns/"><d:href>webcal://uni.example/'
                                  "feed.ics</d:href></cs:source>")), {}
        if method == "REPORT" and url.endswith("/work/"):
            return 207, _ms(_data("/1/calendars/work/a.ics", WORK_ICS)), {}
        if method == "REPORT" and url.endswith("/plan/"):
            return 207, _ms(*(_data(h, ics) for h, ics in self.planned.items())), {}
        if method == "REPORT":
            return 207, _ms(), {}
        if method == "PUT":
            self.planned[url] = body
            return 201, b"", {}
        if method == "DELETE":
            self.planned.pop(url, None)
            return 204, b"", {}
        if method == "GET" and url in self.files:
            ics, head = self.files[url]
            return 200, ics.encode(), head
        return 404, b"", {}


def at(text: str) -> datetime:
    return datetime.fromisoformat(text)


class TestCalDAV(unittest.TestCase):
    def setUp(self) -> None:
        caldav._HOMES.clear()
        self.cal = FakeICloud()
        self.addCleanup(self.cal._tmp.cleanup)

    def test_events_are_timed_busy_and_not_our_own(self) -> None:
        events = self.cal.events(at("2026-10-19T00:00+00:00"), at("2026-10-26T00:00+00:00"))
        self.assertEqual([e["title"] for e in events], ["Stand, up", "Lecture", "lunch"])
        self.assertFalse(events[1]["editable"])  # a subscription is read-only
        self.assertEqual(at(events[2]["start"]), at("2026-10-22T09:00+00:00"))  # Moscow noon
        self.assertEqual(at(events[2]["end"]), at("2026-10-22T09:30+00:00"))
        self.assertEqual(events[0]["calendar"], "Work")
        self.assertEqual(events[0]["color"], "#112233")
        self.assertTrue(events[0]["editable"])
        self.assertNotIn("REPORT", [m for m, u in self.cal.calls if u.endswith("/plan/")])

    def test_write_is_a_diff(self) -> None:
        window = (at("2026-10-19T00:00+00:00"), at("2026-10-26T00:00+00:00"))
        keep = {"title": "A", "start": "2026-10-20T12:00+00:00", "end": "2026-10-20T13:00+00:00",
                "notes": "planner task #1", "kind": "planned"}
        old = {"title": "B", "start": "2026-10-21T12:00+00:00", "end": "2026-10-21T13:00+00:00",
               "notes": "planner task #2", "kind": "planned"}
        self.cal.write(*window, [keep, old])
        self.assertEqual(len(self.cal.planned), 2)
        kept = next(h for h, ics in self.cal.planned.items() if "SUMMARY:A" in ics)

        self.cal.calls.clear()
        moved = dict(old, start="2026-10-22T12:00+00:00", end="2026-10-22T13:00+00:00")
        self.cal.write(*window, [keep, moved])
        writes = [m for m, _ in self.cal.calls if m in ("PUT", "DELETE")]
        self.assertEqual(sorted(writes), ["DELETE", "PUT"])  # A untouched, B moved
        self.assertIn(kept, self.cal.planned)
        self.assertTrue(any("DTSTART:20261022T120000Z" in ics for ics in self.cal.planned.values()))

    def test_edit_changes_a_single_event(self) -> None:
        href = HOME + "work/x.ics"
        self.cal.files[href] = (new_event("Old", "", at("2026-10-20T09:00+00:00"),
                                          at("2026-10-20T10:00+00:00")), {"ETag": '"1"'})
        self.cal.edit({"id": href, "occurrence": "2026-10-20T09:00:00Z", "title": "New",
                       "start": "2026-10-20T11:00+00:00", "end": "2026-10-20T12:00+00:00"})
        saved = self.cal.planned[href]
        self.assertIn("SUMMARY:New", saved)
        self.assertIn("DTSTART:20261020T110000Z", saved)
        self.assertNotIn("SUMMARY:Old", saved)
        self.assertEqual(saved.count("DTSTART"), 1)

    def test_edit_refuses_read_only_and_repeating(self) -> None:
        with self.assertRaisesRegex(CalendarError, "read-only"):
            self.cal.edit({"id": HOME + "hol/x.ics", "occurrence": "2026-10-20T09:00:00Z"})
        with self.assertRaisesRegex(CalendarError, "read-only"):
            self.cal.edit({"id": "https://uni.example/feed.ics", "occurrence": "2026-10-21T09:00:00Z"})
        with self.assertRaisesRegex(CalendarError, "no longer"):  # and no login sent there
            self.cal.edit({"id": "https://elsewhere.example/x.ics", "occurrence": "2026-10-21T09:00:00Z"})
        self.assertNotIn("https://elsewhere.example/x.ics", [u for _, u in self.cal.calls])
        href = HOME + "work/r.ics"
        self.cal.files[href] = (new_event("R", "", at("2026-10-20T09:00+00:00"), at("2026-10-20T10:00+00:00"))
                                .replace("SUMMARY:R", "SUMMARY:R\r\nRRULE:FREQ=WEEKLY"), {})
        with self.assertRaisesRegex(CalendarError, "repeating"):
            self.cal.edit({"id": href, "occurrence": "2026-10-20T09:00:00Z", "title": "x"})


if __name__ == "__main__":
    unittest.main()
