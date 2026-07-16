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
 *
 * **The live session is cut at midnight too** (`todayOf`, `spillOf`). It used to
 * be the one exception — drawn whole, on the day it began, with the axis stretched
 * to hold it — and the exception was wrong twice over. A card headed *Today* was
 * showing yesterday's ruler with nearly all of it empty and the work jammed
 * against the right edge; and one live session past midnight stretched the axis
 * every history row shares, so the comparison the ruler exists for was squashed
 * for the whole page, worse the longer you worked. So it follows the same rule as
 * everything else now: today's card holds today, the hours before midnight go to
 * the day they were worked on, and pressing Stop changes nothing you can see.
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
 * Local midnight of the day a session is *in now* — the day its live edge falls
 * in, which for a live session is the day it is for the person watching.
 *
 * Taken from the session's own end (`start + gross`, the server's arithmetic),
 * never from `Date.now()`: the browser's clock can sit seconds from the server's,
 * and a page that disagreed with itself about which day it is — the strip cut at
 * one midnight, the digits counted to another — would be worse than either.
 */
function todayBaseOf(session) {
  return midnightOf(spanOf(session).end)
}

/**
 * What the live session amounts to **today**, in the shape the card draws.
 *
 * A session that began this morning is returned whole, which is nearly always
 * what happens: `startsEarlier` is false and every number is the session's own.
 * A session that began before the midnight it has since crossed is cut at it, and
 * what comes back is only the hours on this side — today's ink, on today's ruler,
 * starting from the left where it belongs.
 *
 * The hours on the other side are not lost, they are simply not *today*: they are
 * yesterday's, and `spillOf` hands them to the history to be drawn on the day they
 * were worked on. The two pieces still sum to the session.
 *
 * The clock restarting at midnight is the point rather than a side-effect. It is
 * what the history has always said about a session like this, and what the archive
 * will say the moment you press Stop — so this is the same tracker before and
 * after, rather than one that rearranges the day under you when you finish it.
 */
export function todayOf(session) {
  const { start, end } = spanOf(session)
  const base = todayBaseOf(session)
  const startsEarlier = start < base

  // Only cut a session that actually crossed something. Left alone, the ordinary
  // case cannot be bruised by the arithmetic here -- and the zero-width block a
  // just-started session has (see blocksOf) survives, where a `to > base` filter
  // would drop it for beginning exactly at midnight.
  const blocks = startsEarlier
    ? blocksOf(session)
        .filter((block) => block.to > base)
        .map((block) => ({ ...block, from: Math.max(block.from, base) }))
    : blocksOf(session)

  const total = (kind) =>
    blocks
      .filter((block) => block.kind === kind)
      .reduce((sum, block) => sum + (block.to - block.from), 0)

  const worked = total('work')
  const paused = total('pause')

  return {
    base,
    segments: blocks.map((block) => segment(block.kind, block.from, block.to, base)),
    edge: minutesOf(end, base),
    workedSeconds: secondsOf(worked),
    pausedSeconds: secondsOf(paused),
    grossSeconds: secondsOf(worked + paused),
    // Breaks that *finished*, and that touched today -- the open one is counted
    // separately by the card, which says "+1 open" rather than folding it in. A
    // break taken across midnight belongs to both days, exactly as the history
    // counts it (`breaks` in daysOf), because that is one break in each day's
    // account of itself.
    pauseCount: (session.pauses ?? []).filter((pause) => toMs(pause.end) > base).length,
    startsEarlier,
  }
}

/**
 * The half of a live session that is no longer today: the hours before the
 * midnight it has crossed, shaped as a session so the history can draw them.
 *
 * `null` when it has crossed nothing, which is the ordinary case and means the
 * history is exactly the archive, as it was.
 *
 * It is `live` because the session it was cut from is still running, and the
 * history needs to know two things that follow from that: the row *does* run on
 * into the next day (it is cut at that midnight, not ended at it), and a task
 * typed into it belongs to a session that is not in the archive yet — so it is
 * written through the live session's own door rather than the archive's, which
 * would answer "no such session" and be right to.
 */
export function spillOf(session) {
  const { start, end } = spanOf(session)
  const base = midnightOf(end)
  if (start >= base) return null
  return { ...session, end: new Date(base).toISOString(), live: true }
}

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
            // A live session's spill (see spillOf) was cut at today's midnight
            // rather than ended at it, so its span stops exactly where the day
            // does and the comparison alone would read "no". It does run on: it
            // is running now.
            endsLater: span.end > nextMidnightAfter(piece.day) || Boolean(session.live),
            // Still running, so its task is written through the live session's
            // door and not the archive's -- see History.
            live: Boolean(session.live),
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
 * The axis every strip on the page is drawn against: one day, 00:00 to 00:00.
 *
 * A constant, and that is the whole of its job. A day is the same length as every
 * other day, so the ruler is fixed and the comparison it exists for is exact —
 * four hours of work looks like four hours of a day, on Monday and on Friday
 * alike. Nothing can push it now: `daysOf` has cut the archive to size and
 * `todayOf` cuts the live session, so there is no strip on this page longer than
 * the day it is drawn on.
 *
 * It used to take the live session and stretch for it, which was the one way the
 * ruler could read past 24:00. That is gone, and with it the day that got shorter
 * on screen the longer you worked past a midnight — one running session was
 * enough to squash every row beneath it.
 */
export function axisFor() {
  const hours = DAY_MINUTES / 60
  const ticks = []
  for (let hour = 0; hour <= hours; hour += 3) {
    ticks.push(hour * 60)
  }
  return { start: 0, end: DAY_MINUTES, ticks }
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
