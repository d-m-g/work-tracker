"""Apple Calendar over CalDAV (iCloud), for a planner that is not on a Mac.

The same four calls as :class:`planner.eventkit.AppleCalendar` -- ``events``,
``busy``, ``write``, ``edit`` -- with the same shapes, so the planner cannot tell
which one it holds. Standard library only.

Configured by environment (see the README):

* ``PLANNER_CALDAV_USER`` -- the Apple ID. Unset means no CalDAV.
* ``PLANNER_CALDAV_PASSWORD_FILE`` -- a file holding an *app-specific* password
  (appleid.apple.com → Sign-In and Security → App-Specific Passwords).
* ``PLANNER_CALDAV_URL`` -- default ``https://caldav.icloud.com/``; any CalDAV
  server works.
"""

from __future__ import annotations

import base64
import os
import re
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple
from urllib.parse import urljoin
from xml.sax.saxutils import escape as xml_escape

from .eventkit import CalendarError

NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav", "a": "http://apple.com/ns/ical/",
      "cs": "http://calendarserver.org/ns/"}
D, C, A, CS = ("{%s}" % NS[k] for k in ("d", "c", "a", "cs"))

#: The planner's own calendars and their colours, as in eventkit.swift.
OWN = {"Planned": "#ec4899", "Planned · static": "#f5b041"}
STATIC = "Planned · static"

#: Discovery is three round trips; the planner is built per request, so remember it.
_HOMES: Dict[Tuple[str, str], str] = {}


def from_env() -> "CalDAVCalendar":
    return CalDAVCalendar(
        os.environ["PLANNER_CALDAV_USER"],
        Path(os.environ.get("PLANNER_CALDAV_PASSWORD_FILE", "")),
        os.environ.get("PLANNER_CALDAV_URL", "https://caldav.icloud.com/"),
    )


# -- iCalendar ---------------------------------------------------------------


def _utc_stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _unescape(text: str) -> str:
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), text)


def _unfold(ics: str) -> List[str]:
    return re.sub(r"\r?\n[ \t]", "", ics).splitlines()


def vevents(ics: str) -> List[Dict[str, Tuple[Dict[str, str], str]]]:
    """Each VEVENT's properties as ``{NAME: (params, value)}``; nested VALARMs skipped."""
    events: List[Dict[str, Tuple[Dict[str, str], str]]] = []
    current: Optional[Dict[str, Tuple[Dict[str, str], str]]] = None
    nested = 0
    for line in _unfold(ics):
        if line == "BEGIN:VEVENT":
            current, nested = {}, 0
        elif line == "END:VEVENT" and current is not None:
            events.append(current)
            current = None
        elif current is None:
            continue
        elif line.startswith("BEGIN:"):
            nested += 1
        elif line.startswith("END:"):
            nested -= 1
        elif nested == 0:
            head, _, value = line.partition(":")
            name, *raw = head.split(";")
            params = dict(p.split("=", 1) for p in raw if "=" in p)
            current.setdefault(name.upper(), (params, value))
    return events


def when(prop: Optional[Tuple[Dict[str, str], str]]) -> Optional[datetime]:
    """A DTSTART/DTEND as an aware datetime; None for an all-day date (or nothing)."""
    if prop is None:
        return None
    params, value = prop
    if params.get("VALUE") == "DATE" or len(value) == 8:
        return None
    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    naive = datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
    tzid = params.get("TZID", "").strip('"')
    if not tzid:
        return naive.astimezone()  # "floating": read as this server's local time
    try:
        from zoneinfo import ZoneInfo

        return naive.replace(tzinfo=ZoneInfo(tzid))
    except Exception:  # ponytail: an unknown TZID reads as UTC; iCloud's expand sends UTC anyway
        return naive.replace(tzinfo=timezone.utc)


def _duration(value: str) -> timedelta:
    match = re.fullmatch(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value)
    if not match:
        return timedelta(0)
    sign, w, d, h, m, s = match.groups()
    span = timedelta(weeks=int(w or 0), days=int(d or 0), hours=int(h or 0),
                     minutes=int(m or 0), seconds=int(s or 0))
    return -span if sign == "-" else span


def span_of(event: Dict[str, Tuple[Dict[str, str], str]]) -> Optional[Tuple[datetime, datetime]]:
    start = when(event.get("DTSTART"))
    if start is None:
        return None
    end = when(event.get("DTEND"))
    if end is None and "DURATION" in event:
        end = start + _duration(event["DURATION"][1])
    return (start, end or start)


def _text(event: Dict[str, Tuple[Dict[str, str], str]], name: str) -> str:
    return _unescape(event[name][1]) if name in event else ""


def new_event(title: str, notes: str, start: datetime, end: datetime) -> str:
    """One planned block as an .ics resource, with a 15-minute alert."""
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//work-tracker//planner//EN",
        "BEGIN:VEVENT",
        f"UID:{uuid.uuid4()}",
        f"DTSTAMP:{_utc_stamp(datetime.now(timezone.utc))}",
        f"DTSTART:{_utc_stamp(start)}",
        f"DTEND:{_utc_stamp(end)}",
        f"SUMMARY:{_escape(title)}",
        f"DESCRIPTION:{_escape(notes)}",
        "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_escape(title)}", "TRIGGER:-PT15M",
        "END:VALARM",
        "END:VEVENT", "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"


def _edited(ics: str, change: Dict[str, str]) -> str:
    """The resource with the VEVENT's title and/or times replaced."""
    drop = set()
    add = []
    if "title" in change:
        drop.add("SUMMARY")
        add.append(f"SUMMARY:{_escape(change['title'])}")
    if "start" in change:
        drop.update({"DTSTART", "DTEND", "DURATION"})
        add += [f"DTSTART:{_utc_stamp(_iso(change['start']))}",
                f"DTEND:{_utc_stamp(_iso(change['end']))}"]
    out, inside, nested = [], False, 0
    for line in _unfold(ics):
        if line == "BEGIN:VEVENT":
            inside, nested = True, 0
            out += [line, *add]
            continue
        if line == "END:VEVENT":
            inside = False
        elif inside and line.startswith("BEGIN:"):
            nested += 1
        elif inside and line.startswith("END:"):
            nested -= 1
        elif inside and nested == 0 and re.split("[;:]", line, maxsplit=1)[0].upper() in drop:
            continue
        out.append(line)
    return "\r\n".join(out) + "\r\n"


def _iso(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


# -- the calendar ------------------------------------------------------------


class CalDAVCalendar:
    """Busy time from every calendar, and the plan written to one called "Planned"."""

    def __init__(self, user: str, password_file: Path, url: str, timeout: float = 20) -> None:
        self._user = user
        self._password_file = password_file
        self._url = url
        self._timeout = timeout
        self._calendars: Optional[List[Dict[str, Any]]] = None

    # -- HTTP --------------------------------------------------------------

    def _request(self, method: str, url: str, body: str = "", headers: Optional[Dict[str, str]] = None
                 ) -> Tuple[int, bytes, Dict[str, str]]:
        try:
            password = self._password_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise CalendarError(f"cannot read the CalDAV password file: {exc}") from exc
        token = base64.b64encode(f"{self._user}:{password}".encode()).decode()
        request = urllib.request.Request(urljoin(self._url, url), data=body.encode() or None, method=method)
        request.add_header("Authorization", f"Basic {token}")
        request.add_header("Content-Type", "application/xml; charset=utf-8")
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise CalendarError("iCloud refused the login — check the Apple ID and the "
                                    "app-specific password") from exc
            return exc.code, exc.read(), dict(exc.headers or {})
        except OSError as exc:
            raise CalendarError(f"cannot reach the calendar server: {exc}") from exc

    def _multistatus(self, method: str, url: str, body: str, depth: str
                     ) -> Iterator[Tuple[str, Dict[str, ET.Element]]]:
        status, data, _ = self._request(method, url, body, {"Depth": depth})
        if status != 207:
            raise CalendarError(f"the calendar server answered {method} with HTTP {status}")
        for response in ET.fromstring(data).findall("d:response", NS):
            props: Dict[str, ET.Element] = {}
            for propstat in response.findall("d:propstat", NS):
                if " 200 " in f"{propstat.findtext('d:status', '', NS)} ":
                    found = propstat.find("d:prop", NS)
                    for prop in [] if found is None else found:
                        props[prop.tag] = prop
            yield response.findtext("d:href", "", NS), props

    def _propfind(self, url: str, props: str, depth: str) -> Iterator[Tuple[str, Dict[str, ET.Element]]]:
        body = (f'<?xml version="1.0" encoding="utf-8"?><d:propfind xmlns:d="DAV:" '
                f'xmlns:c="{NS["c"]}" xmlns:a="{NS["a"]}" xmlns:cs="{NS["cs"]}">'
                f"<d:prop>{props}</d:prop></d:propfind>")
        return self._multistatus("PROPFIND", url, body, depth)

    # -- discovery ---------------------------------------------------------

    def _home(self) -> str:
        key = (self._url, self._user)
        if key not in _HOMES:
            principal = home = None
            for _, props in self._propfind(self._url, "<d:current-user-principal/>", "0"):
                principal = props.get(D + "current-user-principal")
            if principal is None or principal.findtext("d:href", None, NS) is None:
                raise CalendarError("the calendar server did not say whose calendars these are")
            principal_url = urljoin(self._url, principal.findtext("d:href", "", NS))
            for _, props in self._propfind(principal_url, "<c:calendar-home-set/>", "0"):
                home = props.get(C + "calendar-home-set")
            if home is None or home.findtext("d:href", None, NS) is None:
                raise CalendarError("the calendar server did not say where the calendars are")
            _HOMES[key] = urljoin(principal_url, home.findtext("d:href", "", NS))
        return _HOMES[key]

    def calendars(self) -> List[Dict[str, Any]]:
        """Every calendar that holds events: ``{"url", "name", "color", "editable", "source"}``.

        ``source`` is set for a subscribed calendar (a university timetable, the
        holidays): iCloud only stores its address, so its events are read from there.
        """
        if self._calendars is None:
            home = self._home()
            found = []
            for href, props in self._propfind(
                home,
                "<d:resourcetype/><d:displayname/><a:calendar-color/><cs:source/>"
                "<c:supported-calendar-component-set/><d:current-user-privilege-set/>",
                "1",
            ):
                kind = props.get(D + "resourcetype")
                if kind is None:
                    continue
                source = None
                if kind.find("cs:subscribed", NS) is not None:
                    source = props[CS + "source"].findtext("d:href", None, NS) if CS + "source" in props else None
                    if not source:
                        continue
                    source = re.sub(r"^webcals?://", "https://", source)
                elif kind.find("c:calendar", NS) is None:
                    continue
                comps = props.get(C + "supported-calendar-component-set")
                if comps is not None and not any(c.get("name") == "VEVENT" for c in comps):
                    continue
                privileges = props.get(D + "current-user-privilege-set")
                editable = privileges is None or any(
                    privileges.find(f".//d:{p}", NS) is not None for p in ("write", "write-content", "all"))
                color = (props[A + "calendar-color"].text or "") if A + "calendar-color" in props else ""
                found.append({
                    "url": urljoin(home, href),
                    "name": (props[D + "displayname"].text or "") if D + "displayname" in props else "",
                    "color": color[:7] if color.startswith("#") else "#8e8e93",
                    "editable": editable and source is None,
                    "source": source,
                })
            self._calendars = found
        return self._calendars

    def _own(self, name: str) -> str:
        for calendar in self.calendars():
            if calendar["name"] == name:
                return calendar["url"]
        url = urljoin(self._home(), f"{uuid.uuid4()}/")
        body = (f'<?xml version="1.0" encoding="utf-8"?><c:mkcalendar xmlns:d="DAV:" '
                f'xmlns:c="{NS["c"]}" xmlns:a="{NS["a"]}"><d:set><d:prop>'
                f"<d:displayname>{xml_escape(name)}</d:displayname>"
                f"<a:calendar-color>{OWN[name]}</a:calendar-color>"
                '<c:supported-calendar-component-set><c:comp name="VEVENT"/>'
                "</c:supported-calendar-component-set></d:prop></d:set></c:mkcalendar>")
        status, _, _ = self._request("MKCALENDAR", url, body)
        if status not in (200, 201):
            raise CalendarError(f"could not create the {name} calendar (HTTP {status})")
        self._calendars = None
        return url

    def _query(self, url: str, start: datetime, end: datetime, expand: bool
               ) -> List[Tuple[str, str, str]]:
        """``(href, etag, ics)`` of every event in ``url`` overlapping the window.

        ``expand`` has the server unroll repeating events into their occurrences,
        in UTC -- so a weekly meeting is busy every week, not just the first.
        """
        window = f'start="{_utc_stamp(start)}" end="{_utc_stamp(end)}"'
        data = f"<c:expand {window}/>" if expand else ""
        body = (f'<?xml version="1.0" encoding="utf-8"?><c:calendar-query xmlns:d="DAV:" '
                f'xmlns:c="{NS["c"]}"><d:prop><d:getetag/><c:calendar-data>{data}</c:calendar-data>'
                '</d:prop><c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">'
                f"<c:time-range {window}/></c:comp-filter></c:comp-filter></c:filter>"
                "</c:calendar-query>")
        return [
            (urljoin(url, href), props[D + "getetag"].text or "" if D + "getetag" in props else "",
             props[C + "calendar-data"].text or "")
            for href, props in self._multistatus("REPORT", url, body, "1")
            if C + "calendar-data" in props
        ]

    def _feed(self, url: str) -> str:
        """A subscribed calendar's .ics, fetched from its own server -- with no
        Apple credentials, which are iCloud's and nobody else's.

        ponytail: repeating events in a feed count only their first occurrence;
        expand RRULEs here if a feed ever relies on them.
        """
        try:
            with urllib.request.urlopen(url, timeout=self._timeout) as response:
                return response.read().decode("utf-8", "replace")
        except OSError as exc:
            raise CalendarError(f"cannot read the subscribed calendar at {url.split('?')[0]}: {exc}") from exc

    # -- the four calls ----------------------------------------------------

    def events(self, start: datetime, end: datetime) -> List[Dict[str, Any]]:
        """Busy events: ``{"id", "editable", "start", "end", "title", "calendar", "color"}``."""
        out = []
        for calendar in self.calendars():
            if calendar["name"] in OWN:
                continue
            found = ([(calendar["source"], "", self._feed(calendar["source"]))] if calendar["source"]
                     else self._query(calendar["url"], start, end, expand=True))
            for href, _, ics in found:
                for event in vevents(ics):
                    span = span_of(event)
                    if (span is None or span[1] <= start or span[0] >= end
                            or _text(event, "TRANSP").upper() == "TRANSPARENT"
                            or _text(event, "STATUS").upper() == "CANCELLED"):
                        continue
                    out.append({
                        "id": href,
                        "editable": calendar["editable"],
                        "start": span[0].astimezone().isoformat(),
                        "end": span[1].astimezone().isoformat(),
                        "title": _text(event, "SUMMARY"),
                        "calendar": calendar["name"],
                        "color": calendar["color"],
                    })
        return sorted(out, key=lambda e: e["start"])

    def busy(self, start: datetime, end: datetime) -> List[Tuple[datetime, datetime]]:
        return [(_iso(e["start"]), _iso(e["end"])) for e in self.events(start, end)]

    def write(self, start: datetime, end: datetime, events: List[Dict[str, str]]) -> None:
        """Make the planner's calendars hold exactly ``events`` between start and end.

        A diff, not a wipe: an unchanged block keeps its resource, so a replan that
        moves one block costs two requests rather than re-uploading the week.
        """
        urls = {name: self._own(name) for name in OWN}
        existing: Dict[Tuple[str, ...], List[str]] = {}
        for name, url in urls.items():
            for href, _, ics in self._query(url, start, end, expand=False):
                for event in vevents(ics):
                    span = span_of(event)
                    if span is not None:
                        key = (name, _text(event, "SUMMARY"), _utc_stamp(span[0]), _utc_stamp(span[1]),
                               _text(event, "DESCRIPTION"))
                        existing.setdefault(key, []).append(href)
        for block in events:
            name = STATIC if block.get("kind") == "static" else "Planned"
            begins, ends = _iso(block["start"]), _iso(block["end"])
            title, notes = block.get("title") or "Planned", block.get("notes") or ""
            key = (name, title, _utc_stamp(begins), _utc_stamp(ends), notes)
            if existing.get(key):
                existing[key].pop()
                continue
            status, _, _ = self._request(
                "PUT", urljoin(urls[name], f"{uuid.uuid4()}.ics"), new_event(title, notes, begins, ends),
                {"Content-Type": "text/calendar; charset=utf-8", "If-None-Match": "*"})
            if status not in (200, 201, 204):
                raise CalendarError(f"could not write a planned block (HTTP {status})")
        for stale in (href for hrefs in existing.values() for href in hrefs):
            status, _, _ = self._request("DELETE", stale)
            if status not in (200, 204, 404):
                raise CalendarError(f"could not remove an old planned block (HTTP {status})")

    def edit(self, change: Dict[str, str]) -> None:
        """Change one event: ``{"id", "occurrence", "title"?, "start"?, "end"?}``."""
        href = change["id"]
        calendar = next((c for c in self.calendars()
                         if href == c["source"] or (not c["source"] and href.startswith(c["url"]))), None)
        if calendar is None:  # never send the Apple login anywhere but iCloud's own calendars
            raise CalendarError("that event is no longer in the calendar")
        if not calendar["editable"]:
            raise CalendarError(f"the {calendar['name']} calendar is read-only — change it in the Calendar app")
        status, data, headers = self._request("GET", href)
        if status != 200:
            raise CalendarError("that event is no longer in the calendar")
        ics = data.decode("utf-8")
        if any("RRULE" in event or "RECURRENCE-ID" in event for event in vevents(ics)):
            # ponytail: one occurrence of a repeating event needs an override VEVENT; not built.
            raise CalendarError("that is a repeating event — change it in the Calendar app")
        etag = next((v for k, v in headers.items() if k.lower() == "etag"), None)
        status, _, _ = self._request(
            "PUT", href, _edited(ics, change),
            {"Content-Type": "text/calendar; charset=utf-8", **({"If-Match": etag} if etag else {})})
        if status == 412:
            raise CalendarError("the event changed in the meantime — reload and try again")
        if status not in (200, 201, 204):
            raise CalendarError(f"could not save the event (HTTP {status})")
