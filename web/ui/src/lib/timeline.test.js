/**
 * Tests for the geometry behind the strips — and above all for the midnight.
 *
 * Run them with `npm test` in web/ui. They need no runner and no dependency:
 * `node --test` and `node:assert` ship with Node, which this project already
 * needs to build the app at all. The same bargain the Python suite strikes —
 * `python3 -m unittest`, and nothing to install.
 *
 * These are here because a midnight is the one thing this file gets asked about
 * that a person cannot check by looking. Every other bug in a strip is visible:
 * the ink is in the wrong place and you can see that it is. A day boundary is
 * arithmetic, it is wrong for a few hours a night, and by morning it has fixed
 * itself and left no trace — which is exactly how the live session came to be
 * drawn on the day it began, under a heading that said Today, for as long as
 * this file had no tests in it.
 *
 * Nothing here reads the clock. `todayOf` takes the day it is *in* from the
 * session's own end (`start + gross`, the server's arithmetic), so a session is
 * a fixture like any other and these assertions mean the same thing at any hour
 * and in any timezone.
 */

import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { axisFor, daysOf, spillOf, todayOf } from './timeline.js'

const MINUTE = 60 * 1000
const HOUR = 60 * MINUTE

/** A local wall-clock moment in July 2026, as the ISO string the API sends. */
const at = (day, hour, minute = 0) => new Date(2026, 6, day, hour, minute).toISOString()

/** The live status payload web/api.py builds, for a session that is still running. */
function live({ start, gross, pauses = [], pauseStart = null, task = 'Something' }) {
  return {
    state: pauseStart ? 'paused' : 'running',
    id: 'a-session',
    start,
    task,
    grossSeconds: gross / 1000,
    pauses: pauses.map(([from, to]) => ({ start: from, end: to, seconds: (new Date(to) - new Date(from)) / 1000 })),
    pauseStart,
  }
}

describe('todayOf — a session that never left its day', () => {
  const session = live({
    start: at(16, 9),
    gross: 3 * HOUR,
    pauses: [[at(16, 10), at(16, 10, 30)]],
  })

  it('is not cut: today holds all of it', () => {
    const today = todayOf(session)
    assert.equal(today.startsEarlier, false)
    assert.equal(today.workedSeconds, 2.5 * 3600)
  })

  it('is drawn from the hour it began, not from midnight', () => {
    const [first] = todayOf(session).segments
    assert.equal(first.kind, 'work')
    assert.equal(first.from, 9 * 60)
  })

  it('has nothing to spill', () => {
    assert.equal(spillOf(session), null)
  })
})

describe('todayOf — a session that ran past midnight', () => {
  // 22:00 on the 16th until 01:30 on the 17th. Ninety minutes of it are today.
  const session = live({ start: at(16, 22), gross: 3.5 * HOUR })

  it('keeps only the hours on this side of the midnight', () => {
    const today = todayOf(session)
    assert.equal(today.startsEarlier, true)
    assert.equal(today.workedSeconds, 90 * 60)
  })

  it('draws them from midnight, at the left of the day', () => {
    const today = todayOf(session)
    assert.deepEqual(
      today.segments.map((s) => [s.kind, s.from, s.to]),
      [['work', 0, 90]],
    )
    // The live edge is 01:30 into *today*, not 25:30 into the day it began --
    // which is the bug this whole file exists to have caught.
    assert.equal(today.edge, 90)
  })

  it('gives the hours before midnight to the day they were worked on', () => {
    const spill = spillOf(session)
    const [yesterday] = daysOf([spill])
    assert.equal(yesterday.at, new Date(2026, 6, 16).getTime())
    assert.equal(yesterday.workedSeconds, 2 * 3600)
  })

  it('loses nothing and invents nothing: the halves are the session', () => {
    const worked = todayOf(session).workedSeconds
    const spilled = daysOf([spillOf(session)]).reduce((sum, day) => sum + day.workedSeconds, 0)
    assert.equal(worked + spilled, 3.5 * 3600)
  })
})

describe('todayOf — the breaks fall on the day they were taken', () => {
  it('draws last night\'s breaks on last night, not on this morning', () => {
    // One break at 22:30 yesterday, one at 00:30 today. Only the second is a hole
    // in today's strip; the first is a hole in the strip the history draws.
    const session = live({
      start: at(16, 22),
      gross: 3 * HOUR,
      pauses: [
        [at(16, 22, 30), at(16, 23)],
        [at(17, 0, 30), at(17, 0, 45)],
      ],
    })
    assert.deepEqual(
      todayOf(session).segments.map((s) => [s.kind, s.from, s.to]),
      [
        ['work', 0, 30],
        ['pause', 30, 45],
        ['work', 45, 60],
      ],
    )
    assert.equal(daysOf([spillOf(session)])[0].breaks, 1)
  })

  it('splits a break taken across the midnight, and gives each day its half', () => {
    // 23:50 to 00:10: one break, ten real minutes on each side of the date.
    const session = live({
      start: at(16, 22),
      gross: 3 * HOUR,
      pauses: [[at(16, 23, 50), at(17, 0, 10)]],
    })
    const [first] = todayOf(session).segments
    assert.deepEqual([first.kind, first.from, first.to], ['pause', 0, 10])
    assert.equal(daysOf([spillOf(session)])[0].breaks, 1)
  })

  it('holds the strip still while the session is held', () => {
    // Held since 23:40 and still held: not one minute has been worked today, so
    // there is no ink on today at all -- only the track showing through.
    const session = live({ start: at(16, 21), gross: 4 * HOUR, pauseStart: at(16, 23, 40) })
    const today = todayOf(session)
    assert.equal(today.workedSeconds, 0)
    assert.deepEqual(
      today.segments.map((s) => [s.kind, s.from, s.to]),
      [['pause', 0, 60]], // midnight to the 01:00 edge
    )
  })
})

describe('todayOf — the strip is the day, the clock is the session', () => {
  // The card counts the session and draws the day, which is only a contradiction
  // if you expect todayOf to be about the digits. It is not: it is about the ink.
  const session = live({ start: at(16, 22), gross: 3.5 * HOUR })

  it('leaves the session\'s own totals to the server, and reports only its own', () => {
    // Everything the clock and the figures need is already on the status payload,
    // computed once, server-side. The only number here is the one nothing else
    // can supply: what the live session has put on *today*, which the history's
    // total needs in order to count a row the archive has not got yet.
    assert.deepEqual(Object.keys(todayOf(session)).sort(), [
      'edge',
      'segments',
      'startsEarlier',
      'workedSeconds',
    ])
  })
})

describe('spillOf — the half that is history already', () => {
  const session = live({ start: at(16, 22), gross: 3.5 * HOUR, task: 'The long night' })

  it('says it runs on, because it does', () => {
    // It was cut at midnight, not ended at it: comparing its span to the day
    // would say otherwise, and the row would claim the night simply stopped.
    const [part] = daysOf([spillOf(session)])[0].parts
    assert.equal(part.endsLater, true)
  })

  it('is marked live, so its task is written through the live door', () => {
    // Naming it by id would send the tracker looking through sessions/ for a
    // session that is still in current.json -- see History.jsx.
    const [part] = daysOf([spillOf(session)])[0].parts
    assert.equal(part.live, true)
    assert.equal(part.task, 'The long night')
  })

  it('merges into a day that already had a session on it', () => {
    // An evening session archived at 20:00, and the live one that began at 22:00:
    // one night, one row -- not two rows for one date.
    const archived = {
      id: 'earlier',
      start: at(16, 19),
      end: at(16, 20),
      task: 'Earlier',
      pauses: [],
    }
    const days = daysOf([archived, spillOf(session)])
    assert.equal(days.length, 1)
    assert.equal(days[0].parts.length, 2)
    assert.equal(days[0].workedSeconds, 3 * 3600) // one hour, then two
  })

  it('leaves an archived session alone', () => {
    const archived = { id: 'x', start: at(16, 9), end: at(16, 10), task: null, pauses: [] }
    const [part] = daysOf([archived])[0].parts
    assert.equal(part.live, false)
    assert.equal(part.endsLater, false)
  })
})

describe('axisFor — a day, and always a day', () => {
  it('is 24 hours', () => {
    assert.equal(axisFor().end, 24 * 60)
    assert.equal(axisFor().start, 0)
  })

  it('ticks every three hours, ending where it began', () => {
    assert.deepEqual(axisFor().ticks, [0, 180, 360, 540, 720, 900, 1080, 1260, 1440])
  })

  it('cannot be stretched by a session running past midnight', () => {
    // It used to take the live session and grow for it, which squashed every
    // history row beneath it -- worse the longer you worked. Nothing can push it
    // now, because nothing on the page is longer than the day it is drawn on.
    assert.equal(axisFor(live({ start: at(16, 22), gross: 9 * HOUR })).end, 24 * 60)
  })
})
