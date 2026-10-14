// The planner's one window onto Apple Calendar.
//
//   eventkit busy  FROM TO   -> JSON [{"start", "end", "title", "calendar", "color"}] of
//                               every timed, busy event outside the planner's own two
//                               calendars; color is the calendar's own, as #rrggbb
//   eventkit write FROM TO   <- JSON [{"title", "start", "end", "notes", "kind"}] on stdin;
//                               replaces everything in the planner's calendars between
//                               FROM and TO -- "Planned", or "Planned · static" for kind
//                               "static" -- each event with a 15-minute alert
//   eventkit edit  FROM TO   <- JSON {"id", "occurrence", "title"?, "start"?, "end"?} on
//                               stdin; changes that one occurrence (found between FROM
//                               and TO), refusing a calendar that is read-only
//
// Timestamps are ISO-8601 with an offset, both ways. Exit 1 with a sentence on
// stderr when calendar access is refused.
import CoreGraphics
import EventKit
import Foundation

/// The planner's own calendars, in the tracker's colours so iOS and the Mac show
/// them the way the viewer does: pink for a plan, amber for a static block.
let plannedName = "Planned"
let staticName = "Planned · static"
let ownCalendars = [plannedName: (0xec, 0x48, 0x99), staticName: (0xf5, 0xb0, 0x41)]
/// Every event the planner writes reminds you this long before it starts.
let reminder: TimeInterval = -15 * 60
let store = EKEventStore()
let iso = ISO8601DateFormatter()
iso.timeZone = .current

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(1)
}

let args = CommandLine.arguments
guard args.count == 4, let from = iso.date(from: args[2]), let to = iso.date(from: args[3]) else {
    fail("usage: eventkit busy|write FROM TO")
}

let gate = DispatchSemaphore(value: 0)
var granted = false
if #available(macOS 14.0, *) {
    store.requestFullAccessToEvents { ok, _ in granted = ok; gate.signal() }
} else {
    store.requestAccess(to: .event) { ok, _ in granted = ok; gate.signal() }
}
gate.wait()
guard granted else { fail("calendar access refused — allow it in System Settings → Privacy & Security → Calendars") }

/// The calendar's colour as #rrggbb, in sRGB -- what a browser means by it.
func hex(_ color: CGColor?) -> String {
    guard let srgb = CGColorSpace(name: CGColorSpace.sRGB),
          let c = color?.converted(to: srgb, intent: .defaultIntent, options: nil)?.components,
          c.count >= 3 else { return "#8e8e93" }
    return String(format: "#%02x%02x%02x", Int(c[0] * 255), Int(c[1] * 255), Int(c[2] * 255))
}

/// One of the planner's calendars, created if missing and kept in its colour --
/// recoloured if it was made before it had one, or changed by hand since.
func ownCalendar(_ name: String) throws -> EKCalendar {
    let (r, g, b) = ownCalendars[name]!
    let color = CGColor(srgbRed: CGFloat(r) / 255, green: CGFloat(g) / 255, blue: CGFloat(b) / 255, alpha: 1)
    if let found = store.calendars(for: .event).first(where: { $0.title == name }) {
        if hex(found.cgColor) != hex(color) {
            found.cgColor = color
            try store.saveCalendar(found, commit: true)
        }
        return found
    }
    let calendar = EKCalendar(for: .event, eventStore: store)
    calendar.title = name
    calendar.cgColor = color
    guard let source = store.defaultCalendarForNewEvents?.source else { fail("no calendar account to add \(name) to") }
    calendar.source = source
    try store.saveCalendar(calendar, commit: true)
    return calendar
}

switch args[1] {
case "busy":
    let others = store.calendars(for: .event).filter { ownCalendars[$0.title] == nil }
    let events = store.events(matching: store.predicateForEvents(withStart: from, end: to, calendars: others))
    let busy = events
        .filter { !$0.isAllDay && $0.availability != .free && $0.status != .canceled }
        .map { [
            "id": $0.eventIdentifier ?? "",
            "editable": $0.calendar.allowsContentModifications,
            "start": iso.string(from: $0.startDate),
            "end": iso.string(from: $0.endDate),
            "title": $0.title ?? "",
            "calendar": $0.calendar.title,
            "color": hex($0.calendar.cgColor),
        ] as [String: Any] }
    print(String(data: try JSONSerialization.data(withJSONObject: busy), encoding: .utf8)!)

case "write":
    let input = FileHandle.standardInput.readDataToEndOfFile()
    guard let blocks = try JSONSerialization.jsonObject(with: input) as? [[String: String]] else {
        fail("stdin must be a JSON array of blocks")
    }
    let planned = try ownCalendar(plannedName)
    let fixed = try ownCalendar(staticName)
    for old in store.events(matching: store.predicateForEvents(withStart: from, end: to, calendars: [planned, fixed])) {
        try store.remove(old, span: .thisEvent, commit: false)
    }
    for block in blocks {
        guard let start = block["start"].flatMap(iso.date), let end = block["end"].flatMap(iso.date) else { continue }
        let event = EKEvent(eventStore: store)
        event.calendar = block["kind"] == "static" ? fixed : planned
        event.addAlarm(EKAlarm(relativeOffset: reminder))
        event.title = block["title"] ?? "Planned"
        event.notes = block["notes"]
        event.startDate = start
        event.endDate = end
        try store.save(event, span: .thisEvent, commit: false)
    }
    try store.commit()

case "edit":
    let input = FileHandle.standardInput.readDataToEndOfFile()
    guard let change = try JSONSerialization.jsonObject(with: input) as? [String: String],
          let id = change["id"], let occurrence = change["occurrence"].flatMap(iso.date) else {
        fail("stdin must be {id, occurrence, title?, start?, end?}")
    }
    let found = store.events(matching: store.predicateForEvents(withStart: from, end: to, calendars: nil))
        .first { $0.eventIdentifier == id && abs($0.startDate.timeIntervalSince(occurrence)) < 1 }
    guard let event = found else { fail("that event is no longer in the calendar") }
    guard event.calendar.allowsContentModifications else {
        fail("the \(event.calendar.title) calendar is read-only — change it in the Calendar app")
    }
    if let title = change["title"] { event.title = title }
    if let start = change["start"].flatMap(iso.date) { event.startDate = start }
    if let end = change["end"].flatMap(iso.date) { event.endDate = end }
    try store.save(event, span: .thisEvent, commit: true)

default:
    fail("usage: eventkit busy|write|edit FROM TO")
}
