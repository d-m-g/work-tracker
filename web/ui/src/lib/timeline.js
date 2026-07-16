/**
 * The geometry behind the day strips. Pure functions — no React, no DOM.
 *
 * A session is a span of wall-clock time with holes punched in it. That is the
 * shape these helpers produce: an ordered list of `work` and `pause` blocks.
 *
 * **The unit on screen is a day, not a session.** You can start and stop the
 * tracker six times between breakfast and bed, and that is six sessions and one
 * day — so the history draws one strip per day, with every session that touched
 * it laid on the same hours. A row is a day of your life; how many times you
 * happened to press the button is not what you go there to read.
 *
 * **A session that runs past midnight is two days' work, and is cut in two.**
 * 21:24 Tuesday to 01:38 Wednesday means Tuesday worked until midnight and
 * Wednesday started at 00:00, because that is what happened. `daysOf` splits at
 * local midnight and each day counts only its own hours. Nothing is invented and
 * nothing is lost: the pieces still sum to the session.
 *
 * That cut is also what makes the ruler honest. The axis is **time of day**, so
 * every day is measured from its own local midnight and Monday and Friday sit on
 * one ruler — a day that started late *looks* late. Before the split, a session
 * ending at 01:38 was minute 1594 of the day it began, the axis had to stretch to
 * 27 hours to hold it, and every day's ink was squeezed into the sliver that was
 * left. Now no day can exceed 24 hours, so the ruler is a day: 00:00 to 00:00.
 */

const MINUTE = 60 * 1000

/** Minutes in a day. The axis, unless a live session has outrun one. */
export const DAY_MINUTES = 24 * 60

/** Parse an ISO-8601 timestamp to epoch milliseconds. */
export function toMs(iso) {
  return new Date(iso).getTime()
}

/** Local midnight of the day a moment falls in: the origin it is measured from. */
export function midnightOf(ms) {
  const moment = new Date(ms)
  return new Date(moment.getFullYear(), moment.getMonth(), moment.getDate()).getTime()
}

/**
 * The next local midnight after `ms`.
 *
 * Built by adding a day to the *date* rather than 24 hours to the clock, so the
 * two days a year that are 23 or 25 hours long land on midnight like every other.
 */
export function nextMidnightAfter(ms) {
  const moment = new Date(ms)
  return new Date(moment.getFullYear(), moment.getMonth(), moment.getDate() + 1).getTime()
}

/** Local midnight of the day a session began. */
export function baseOf(session) {
  return midnightOf(toMs(session.start))
}

/** Minutes from `base` to `moment`. */
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
 * Cut a session into alternating work and pause blocks, in absolute milliseconds.
 *
 * `pauseStart` is the pause that has not finished yet: it runs to the live edge,
 * which is what makes a paused session read as still open rather than stopped.
 */
function blocksOf(session) {
  const { start, end } = spanOf(session)

  const pauses = [...(session.pauses ?? [])]
    .map((pause) => ({ from: toMs(pause.start), to: toMs(pause.end) }))
    .sort((a, b) => a.from - b.from)

  if (session.pauseStart) {
    pauses.push({ from: toMs(session.pauseStart), to: end })
  }

  const blocks = []
  let cursor = start

  for (const pause of pauses) {
    // Clamp: a hand-edited file could hold a pause that pokes outside its span.
    const from = Math.max(cursor, Math.min(pause.from, end))
    const to = Math.max(from, Math.min(pause.to, end))

    if (from > cursor) blocks.push({ kind: 'work', from: cursor, to: from })
    if (to > from) blocks.push({ kind: 'pause', from, to })
    cursor = Math.max(cursor, to)
  }

  if (end > cursor) blocks.push({ kind: 'work', from: cursor, to: end })

  // A session stopped in the same second it started has no block at all, and
  // would vanish from its own day. It happened; give it a block of no width and
  // let the strip's minimum draw it as the tick mark it is.
  if (blocks.length === 0) blocks.push({ kind: 'work', from: start, to: start })

  return blocks
}

/** Cut a session into work and pause segments, in minutes of its own day. */
export function segmentsOf(session) {
  const base = baseOf(session)
  return blocksOf(session).map((block) => segment(block.kind, block.from, block.to, base))
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
 * Cut an interval at every local midnight it crosses.
 *
 * Returns one piece per day touched, in order. An interval inside one day comes
 * back whole and untouched, which is almost always what happens — this loop runs
 * more than once only for the sessions that actually saw a midnight.
 */
function byDay(from, to) {
  const pieces = []
  let cursor = from

  while (cursor < to) {
    const midnight = nextMidnightAfter(cursor)
    const end = Math.min(to, midnight)
    pieces.push({ day: midnightOf(cursor), from: cursor, to: end })
    cursor = end
  }

  // A zero-length interval never enters the loop, but it still has a day.
  if (pieces.length === 0) pieces.push({ day: midnightOf(from), from, to })

  return pieces
}

const secondsOf = (ms) => Math.round(ms / 1000)

/**
 * Group archived sessions into the days they were worked on, newest first.
 *
 * Each day comes back with everything the history needs to draw it and nothing
 * it has to recompute:
 *
 * * `segments` — every work and pause block that fell on this day, clipped to it
 *   and measured in minutes from *its* midnight, ready for the shared axis;
 * * `parts` — one per session that touched the day, carrying what it did *here*:
 *   the clipped clock range, the worked time, the task, and whether it began
 *   before this midnight or ran past the next one;
 * * the day's own `workedSeconds`, `pausedSeconds` and break count.
 *
 * The gaps *between* sessions are not pauses and are not drawn as pauses: they
 * are simply not worked, and the track shows through. A pause is a break you took
 * inside a session you had not finished; an evening is neither.
 */
export function daysOf(sessions) {
  const days = new Map()

  const dayAt = (base) => {
    let day = days.get(base)
    if (!day) {
      day = { base, blocks: [], parts: [] }
      days.set(base, day)
    }
    return day
  }

  for (const session of sessions) {
    const span = spanOf(session)
    const parts = new Map()

    for (const block of blocksOf(session)) {
      for (const piece of byDay(block.from, block.to)) {
        const day = dayAt(piece.day)
        day.blocks.push({ kind: block.kind, from: piece.from, to: piece.to })

        let part = parts.get(piece.day)
        if (!part) {
          part = {
            session,
            id: session.id,
            task: session.task,
            from: piece.from,
            to: piece.to,
            workedMs: 0,
            pausedMs: 0,
            // The session was already running when this day began, or was still
            // running when it ended. Either way the row is showing a slice of
            // something larger, and should say so rather than imply a day that
            // started at midnight sharp.
            startsEarlier: span.start < piece.day,
            endsLater: span.end > nextMidnightAfter(piece.day),
          }
          parts.set(piece.day, part)
          day.parts.push(part)
        }

        part.from = Math.min(part.from, piece.from)
        part.to = Math.max(part.to, piece.to)
        if (block.kind === 'work') part.workedMs += piece.to - piece.from
        else part.pausedMs += piece.to - piece.from
      }
    }
  }

  return [...days.values()]
    .map((day) => {
      day.blocks.sort((a, b) => a.from - b.from)
      day.parts.sort((a, b) => a.from - b.from)

      const segments = day.blocks.map((block) => segment(block.kind, block.from, block.to, day.base))
      const worked = day.blocks
        .filter((block) => block.kind === 'work')
        .reduce((total, block) => total + (block.to - block.from), 0)
      const paused = day.blocks
        .filter((block) => block.kind === 'pause')
        .reduce((total, block) => total + (block.to - block.from), 0)

      return {
        // Stable across a refetch and unique per day: React's key, and the row's.
        id: String(day.base),
        at: day.base,
        segments,
        parts: day.parts.map((part) => ({
          ...part,
          workedSeconds: secondsOf(part.workedMs),
          pausedSeconds: secondsOf(part.pausedMs),
        })),
        workedSeconds: secondsOf(worked),
        pausedSeconds: secondsOf(paused),
        breaks: segments.filter((s) => s.kind === 'pause').length,
      }
    })
    .sort((a, b) => b.at - a.at)
}

/**
 * The shared axis every strip is drawn against: one day, 00:00 to 00:00.
 *
 * A day is the same length as every other day, so the ruler is fixed and the
 * comparison it exists for is exact — four hours of work looks like four hours of
 * a day, on Monday and on Friday alike. Nothing in the history can push it, since
 * `daysOf` has already cut the days to size.
 *
 * The one thing that can is a live session still running past midnight: it is
 * drawn whole, on the day it began, because it is one session and you are in the
 * middle of it. So the axis stretches to hold it rather than let it run off the
 * end — and it is the only reason the ruler ever reads past 24:00, for as long as
 * it takes you to press Stop.
 */
export function axisFor(live = []) {
  let end = DAY_MINUTES

  for (const session of live) {
    const past = minutesOf(spanOf(session).end, baseOf(session))
    if (past > end) end = Math.ceil(past / 60) * 60
  }

  // Thin the ticks as the span grows, so the labels never collide.
  const hours = Math.round(end / 60)
  const step = hours <= 10 ? 1 : hours <= 16 ? 2 : 3

  const ticks = []
  for (let hour = 0; hour <= hours; hour += step) {
    ticks.push(hour * 60)
  }

  return { start: 0, end, ticks }
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
