/**
 * The geometry behind the day strips. Pure functions — no React, no DOM.
 *
 * A session is a span of wall-clock time with holes punched in it. That is the
 * shape these helpers produce: an ordered list of `work` and `pause` segments
 * that tile the span end to end with no gaps and no overlaps.
 *
 * The axis is **time of day**, not absolute time. Every session is measured from
 * its own local midnight, so Monday and Friday sit on the same ruler and a day
 * that started late *looks* late. An absolute axis would instead stretch across
 * the whole week and squash every session into a sliver.
 */

const MINUTE = 60 * 1000

/** Parse an ISO-8601 timestamp to epoch milliseconds. */
export function toMs(iso) {
  return new Date(iso).getTime()
}

/** Local midnight of the day a session began: the origin it is measured from. */
export function baseOf(session) {
  const start = new Date(session.start)
  return new Date(start.getFullYear(), start.getMonth(), start.getDate()).getTime()
}

/**
 * Minutes from `base` to `moment`.
 *
 * A session that runs past midnight simply keeps counting past 1440 rather than
 * wrapping, so its strip stays one continuous band instead of being torn in two.
 */
export function minutesOf(moment, base) {
  return (moment - base) / MINUTE
}

/**
 * The clock span a session occupies.
 *
 * For a live session the end is derived from the server's own numbers
 * (`start + gross`), never from `Date.now()`. The browser's clock can sit
 * seconds away from the server's, and the strip must agree with the digits
 * printed above it.
 */
export function spanOf(session) {
  const start = toMs(session.start)
  if (session.end) return { start, end: toMs(session.end) }
  return { start, end: start + (session.grossSeconds ?? 0) * 1000 }
}

/**
 * Cut a span into alternating work and pause segments, in minutes of the day.
 *
 * `pauseStart` is the pause that has not finished yet: it runs to the live edge,
 * which is what makes a paused session read as still open rather than stopped.
 */
export function segmentsOf(session) {
  const base = baseOf(session)
  const { start, end } = spanOf(session)

  const pauses = [...(session.pauses ?? [])]
    .map((pause) => ({ from: toMs(pause.start), to: toMs(pause.end) }))
    .sort((a, b) => a.from - b.from)

  if (session.pauseStart) {
    pauses.push({ from: toMs(session.pauseStart), to: end })
  }

  const segments = []
  let cursor = start

  for (const pause of pauses) {
    // Clamp: a hand-edited file could hold a pause that pokes outside its span.
    const from = Math.max(cursor, Math.min(pause.from, end))
    const to = Math.max(from, Math.min(pause.to, end))

    if (from > cursor) segments.push(segment('work', cursor, from, base))
    if (to > from) segments.push(segment('pause', from, to, base))
    cursor = Math.max(cursor, to)
  }

  if (end > cursor) segments.push(segment('work', cursor, end, base))
  return segments
}

function segment(kind, from, to, base) {
  return {
    kind,
    from: minutesOf(from, base),
    to: minutesOf(to, base),
    seconds: Math.round((to - from) / 1000),
    at: from, // kept for the hover label, which wants real clock times
    until: to,
  }
}

/**
 * The shared time-of-day axis every strip is drawn against.
 *
 * It spans only the hours that were actually worked — a 09:00–18:00 day does not
 * drag a midnight-to-midnight ruler behind it — and snaps outward to whole hours
 * so the ticks land on round numbers.
 */
export function axisFor(sessions) {
  if (sessions.length === 0) {
    return { start: 9 * 60, end: 18 * 60, ticks: [] }
  }

  const bounds = sessions.map((session) => {
    const base = baseOf(session)
    const { start, end } = spanOf(session)
    return { start: minutesOf(start, base), end: minutesOf(end, base) }
  })

  const earliest = Math.min(...bounds.map((bound) => bound.start))
  const latest = Math.max(...bounds.map((bound) => bound.end))

  const start = Math.floor(earliest / 60) * 60
  const end = Math.max(Math.ceil(latest / 60) * 60, start + 60)

  // Thin the ticks as the span grows, so the labels never collide.
  const hours = Math.round((end - start) / 60)
  const step = hours <= 10 ? 1 : hours <= 16 ? 2 : 3

  const ticks = []
  for (let hour = 0; hour <= hours; hour += step) {
    ticks.push(start + hour * 60)
  }

  return { start, end, ticks }
}

/** Where a minute-of-day sits on the axis, as a percentage from the left. */
export function positionOf(minute, axis) {
  const span = axis.end - axis.start
  if (span <= 0) return 0
  return ((minute - axis.start) / span) * 100
}

/** A tick's label: the hour, wrapped past midnight (25:00 reads as 01). */
export function hourLabel(minute) {
  return String(Math.floor(minute / 60) % 24).padStart(2, '0')
}
