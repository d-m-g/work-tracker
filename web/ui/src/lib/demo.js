/**
 * The demo: the instrument, running on a fortnight that never happened.
 *
 * A public URL that answers every visitor with a password box tells them nothing
 * about what is behind it. This is what is behind it — the whole viewer, live and
 * driveable, standing on invented data.
 *
 * **It is a fiction, and it is a fiction on purpose.** Nothing here reaches the
 * server. `useDemoTracker` is a drop-in for `useTracker` — same shape in, same
 * shape out — but where that one polls `/api/status` and posts commands, this one
 * holds a session in a `useState` and answers out of it. That is not a shortcut,
 * it is the security property: the demo route is safe to leave open (see
 * `public_path` in web/server.py) precisely because there is no request it can
 * send that would read anybody's hours. The server has no demo mode to be talked
 * into, because the demo never asks it for anything.
 *
 * **The fiction is held to the tracker's own rules.** The payloads below are the
 * shapes `web/api.py` builds, down to the field names, and `perform` refuses in
 * the tracker's own words — "the session is already paused" is the sentence
 * `tracker.py` raises, not one written for a demo. So Pause on a paused session
 * behaves here exactly as it does on the real thing, and the demo cannot flatter
 * the instrument by being more permissive than it. If the two ever drift, the
 * demo is the one that is wrong.
 *
 * **Everything is anchored to the moment you arrive**, never to a date baked in
 * at build time: the live session started three-and-three-quarter hours ago
 * whenever "ago" is, and the archive is hung off that. A demo that is always
 * mid-afternoon on the day it was written is a screenshot with extra steps.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
// The very midnight the history cuts on, so a session the demo means to land at
// 22:10 lands there on the same day the strips draw it on.
import { midnightOf } from './timeline.js'

const MINUTE = 60 * 1000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

/** The path the demo answers at. `DEMO_PATH` in web/server.py, and it must match. */
export const DEMO_PATH = '/demo'

/** True if this page load is the demo rather than the instrument. */
export function isDemo() {
  const path = window.location.pathname
  return path === DEMO_PATH || path.startsWith(`${DEMO_PATH}/`)
}

// ---------------------------------------------------------------------------
// stamps
// ---------------------------------------------------------------------------

function pad(value) {
  return String(value).padStart(2, '0')
}

/**
 * An ISO-8601 stamp carrying this browser's own UTC offset.
 *
 * `toISOString()` would be easier and would be wrong in the same way a naive
 * datetime is wrong: the tracker writes `2026-07-14T19:42:18+03:00`
 * (`utils.format_timestamp`), and the demo's job is to produce what the server
 * produces, not something else the parser happens to accept.
 */
function iso(ms) {
  const moment = new Date(ms)
  const offset = -moment.getTimezoneOffset()
  const sign = offset < 0 ? '-' : '+'
  const size = Math.abs(offset)
  return (
    `${moment.getFullYear()}-${pad(moment.getMonth() + 1)}-${pad(moment.getDate())}` +
    `T${pad(moment.getHours())}:${pad(moment.getMinutes())}:${pad(moment.getSeconds())}` +
    `${sign}${pad(Math.floor(size / 60))}:${pad(size % 60)}`
  )
}

/** A session id: the stamp the tracker names its files with (`_ID_FORMAT`). */
function idOf(ms) {
  const moment = new Date(ms)
  return (
    `${moment.getFullYear()}-${pad(moment.getMonth() + 1)}-${pad(moment.getDate())}_` +
    `${pad(moment.getHours())}-${pad(moment.getMinutes())}-${pad(moment.getSeconds())}`
  )
}

const seconds = (ms) => Math.round(ms / 1000)

// ---------------------------------------------------------------------------
// the day that never happened
// ---------------------------------------------------------------------------

/** How long ago the demo's live session began. Far enough in to have a shape. */
const LIVE_AGO = 3 * HOUR + 44 * MINUTE

/** The live session's breaks: [minutes in, minutes long]. */
const LIVE_BREAKS = [
  [71, 12],
  [158, 34],
]

const LIVE_TASK = 'Demo mode — the viewer, on a day that never happened'

/**
 * The archive: `back` days ago, starting at minute `at` of that day, running for
 * `gross` minutes, with breaks at [minutes in, minutes long].
 *
 * The hours are absolute — 09:34, not "an hour before the live session" — and
 * that is a correction, not a detail. Hanging them off `now` meant the whole
 * fortnight slid up and down the clock with the hour you happened to visit: at
 * lunchtime it was a fortnight of office days, and at midnight the same data
 * became a fortnight of sessions that all fell over midnight and split in half.
 * The demo would have been showing off the cut by accident, differently every
 * hour, and a fortnight that reads differently at 23:00 than at noon is not a
 * fortnight. Only the live session moves with you now; the past is the past.
 *
 * The gaps in `back` are the weekends and the day off. A demo with a perfect
 * unbroken row of days is a demo of a spreadsheet, not of anyone's fortnight.
 *
 * Two entries are doing work beyond filling the list:
 *
 * * **back 2 holds three sessions.** A day is not a session — you start and stop
 *   as the day happens to go — and the history draws the day, so the demo had
 *   better contain a day worth drawing.
 * * **back 5 crosses midnight**, 22:10 to 02:26. One session, two days' work: the
 *   history cuts it at midnight, back 5 keeps the evening and back 4 gets the
 *   small hours on top of its own afternoon. That cut is what holds the ruler to
 *   a day, and a demo that never saw a midnight would never show it.
 */
const ARCHIVE = [
  { back: 1, at: 9 * 60 + 34, gross: 507, task: 'Widget: drive the VM over SSH, with an offline fallback', breaks: [[124, 36], [318, 21]] },

  // One day, three sessions, one strip.
  { back: 2, at: 9 * 60 + 12, gross: 148, task: 'Dress the viewer in the portfolio’s voice', breaks: [] },
  { back: 2, at: 12 * 60 + 40, gross: 212, task: 'Dress the viewer in the portfolio’s voice', breaks: [[96, 24]] },
  { back: 2, at: 17 * 60 + 5, gross: 163, task: 'Zodiak’s figures, celled in CSS', breaks: [] },

  { back: 3, at: 8 * 60 + 50, gross: 601, task: 'Password login for the public viewer', breaks: [[97, 17], [251, 48], [455, 24]] },
  { back: 4, at: 14 * 60 + 20, gross: 268, task: null, breaks: [] },

  // The long night, and the only midnight in the fortnight.
  { back: 5, at: 22 * 60 + 10, gross: 256, task: 'The night before the deadline', breaks: [[118, 22]] },

  { back: 7, at: 10 * 60 + 5, gross: 473, task: 'Size the viewer for touch; lift the hero card', breaks: [[205, 52]] },
  { back: 8, at: 11 * 60 + 30, gross: 388, task: 'Give the mini player a face so the readout can’t wash out', breaks: [[143, 27], [299, 19]] },
  { back: 9, at: 9 * 60 + 45, gross: 556, task: 'Session strips: one axis, so a late start looks late', breaks: [[176, 38]] },
  { back: 11, at: 10 * 60 + 20, gross: 419, task: 'Shortcuts: Start, Pause, Stop, and one key that toggles', breaks: [[211, 25]] },
  { back: 12, at: 8 * 60 + 40, gross: 534, task: 'Tracker core — one writer, atomic writes, no lost days', breaks: [[131, 29], [341, 44]] },
]

/** Build the demo's opening state: a session in progress, and a fortnight behind it. */
function seed() {
  const start = Date.now() - LIVE_AGO

  return {
    live: {
      id: idOf(start),
      start,
      task: LIVE_TASK,
      pauses: LIVE_BREAKS.map(([at, length]) => ({
        from: start + at * MINUTE,
        to: start + (at + length) * MINUTE,
      })),
      pauseStart: null,
    },
    archive: ARCHIVE.map((entry) => {
      // Midnight `back` days ago, plus the hour it actually began at. Anchored to
      // the day rather than to the live session, so the fortnight sits still.
      const began = midnightOf(start - entry.back * DAY) + entry.at * MINUTE
      const ended = began + entry.gross * MINUTE
      const pauses = entry.breaks.map(([at, length]) => ({
        from: began + at * MINUTE,
        to: began + (at + length) * MINUTE,
      }))
      return completedOf({ id: idOf(began), start: began, task: entry.task, pauses }, ended)
    }),
  }
}

// ---------------------------------------------------------------------------
// the payloads web/api.py would have sent
// ---------------------------------------------------------------------------

/**
 * There is no session to sign out of, and nothing here is withheld.
 *
 * The server staples both of these onto every status it sends (see `_serve_json`),
 * so the demo owes an answer to the same two questions.
 *
 * `login: false` is the honest one: nobody signed in to see a fortnight that never
 * happened. It is what keeps the Sign out button off this page — the way back to
 * the login form is the banner's, and it is a link, because there is no session
 * here to end.
 *
 * `role: 'owner'` is the honest one too, though it reads oddly next to it. The
 * demo's whole argument is the instrument working, so every button has to be
 * there and has to do what it says — and the account that has every button is the
 * owner. There is nothing to protect: the fortnight is invented, this page never
 * calls the server, and pressing Stop here ends a session that does not exist.
 * The demo cannot escalate to anything, because there is nothing on the other
 * side of it to escalate to.
 */
const NO_LOGIN = { login: false, role: 'owner' }

const IDLE = {
  state: 'idle',
  id: null,
  start: null,
  task: null,
  grossSeconds: 0,
  workedSeconds: 0,
  pausedSeconds: 0,
  pauseCount: 0,
  pauseInProgress: false,
  pauseStart: null,
  pauses: [],
  ...NO_LOGIN,
}

/** One closed break, in the shape `models.Pause.to_dict` writes. */
function pauseOf(pause) {
  return { start: iso(pause.from), end: iso(pause.to), seconds: seconds(pause.to - pause.from) }
}

const heldFor = (pauses) => pauses.reduce((total, pause) => total + (pause.to - pause.from), 0)

/**
 * `GET /api/status`, computed at `now` — the moving half of the demo.
 *
 * The arithmetic is `build_status_payload`'s, and so is the consequence: worked
 * time is gross minus paused, so while a pause is open the clock genuinely stands
 * still rather than being frozen for show.
 */
function statusOf(live, now) {
  if (!live) return IDLE

  const end = Math.max(now, live.start)
  const gross = end - live.start
  const paused = heldFor(live.pauses) + (live.pauseStart ? end - live.pauseStart : 0)

  return {
    state: live.pauseStart ? 'paused' : 'running',
    id: live.id,
    start: iso(live.start),
    task: live.task,
    grossSeconds: seconds(gross),
    workedSeconds: seconds(gross - paused),
    pausedSeconds: seconds(paused),
    pauseCount: live.pauses.length,
    pauseInProgress: Boolean(live.pauseStart),
    pauseStart: live.pauseStart ? iso(live.pauseStart) : null,
    pauses: live.pauses.map(pauseOf),
    ...NO_LOGIN,
  }
}

/** One archived day, in the shape `models.CompletedSession.to_dict` writes. */
function completedOf(live, end) {
  const gross = end - live.start
  const paused = heldFor(live.pauses)
  return {
    id: live.id,
    start: iso(live.start),
    end: iso(end),
    status: 'completed',
    task: live.task,
    grossSeconds: seconds(gross),
    pausedSeconds: seconds(paused),
    workedSeconds: seconds(gross - paused),
    pauses: live.pauses.map(pauseOf),
  }
}

/** `GET /api/sessions`, totals and all. The archive is held newest-first. */
function sessionsOf(archive) {
  return {
    sessions: archive,
    // A demo has no corrupt files. The real viewer names them here, and that it
    // has nothing to name is itself true of this fortnight.
    unreadable: [],
    totals: {
      count: archive.length,
      workedSeconds: archive.reduce((total, day) => total + day.workedSeconds, 0),
      pausedSeconds: archive.reduce((total, day) => total + day.pausedSeconds, 0),
    },
  }
}

// ---------------------------------------------------------------------------
// the commands
// ---------------------------------------------------------------------------

/**
 * Refuse, in the tracker's voice.
 *
 * These sentences are lifted from tracker/tracker.py rather than written afresh,
 * because a refusal is a thing the demo is *demonstrating*. The message the real
 * instrument would have shown is the message this one shows.
 */
function refuse(message) {
  throw new Error(message)
}

/** `models.normalise_task`, and `tracker.checked_task`'s cap, in the browser. */
function checkedTask(raw) {
  if (raw === null || raw === undefined) return null
  if (typeof raw !== 'string') refuse(`a task must be text, got ${typeof raw}`)
  const task = raw.split(/\s+/).filter(Boolean).join(' ')
  if (task.length > 200) refuse(`a task may be at most 200 characters -- this one is ${task.length}`)
  return task || null
}

/**
 * Run one command against the demo's state and return the state it leaves.
 *
 * Pure: it takes the state and gives back the next one, throwing a refusal rather
 * than mutating anything. That is what lets it be called from a `useState` setter
 * under StrictMode without doing a day's work twice.
 */
function perform(state, command, body, at) {
  const live = state.live

  switch (command) {
    case 'start':
      if (live) refuse("a session is already in progress -- run 'stop' before starting a new one")
      return {
        ...state,
        live: { id: idOf(at), start: at, task: checkedTask(body?.task), pauses: [], pauseStart: null },
      }

    case 'pause':
      if (!live) refuse("no session is in progress -- run 'start' first")
      if (live.pauseStart) refuse('the session is already paused')
      return { ...state, live: { ...live, pauseStart: at } }

    case 'resume':
      if (!live) refuse("no session is in progress -- run 'start' first")
      if (!live.pauseStart) refuse('the session is not paused')
      return { ...state, live: closePause(live, at) }

    case 'toggle':
      // Whichever of the three the state calls for — exactly as `tracker.toggle`
      // decides it, by asking the same question of the same state.
      if (!live) return perform(state, 'start', body, at)
      return perform(state, live.pauseStart ? 'resume' : 'pause', body, at)

    case 'stop': {
      if (!live) refuse("no session is in progress -- run 'start' first")
      // A session stopped while paused has its open break closed at the stop
      // time, so no paused second goes unaccounted for. `tracker.stop` does this.
      const ending = live.pauseStart ? closePause(live, at) : live
      return { live: null, archive: [completedOf(ending, at), ...state.archive] }
    }

    case 'task': {
      const task = checkedTask(body?.task)
      if (body?.id === null || body?.id === undefined) {
        if (!live) refuse("no session is in progress -- run 'start' first")
        return { ...state, live: { ...live, task } }
      }
      if (typeof body.id !== 'string') refuse(`a session id must be text, got ${typeof body.id}`)
      const index = state.archive.findIndex((day) => day.id === body.id)
      if (index < 0) refuse(`no such session: ${body.id}`)
      const archive = state.archive.slice()
      archive[index] = { ...archive[index], task }
      return { ...state, archive }
    }

    default:
      refuse(`no such command: ${command}`)
      return state
  }
}

/** End the open break at `at`, which is what both `resume` and `stop` do to one. */
function closePause(live, at) {
  return {
    ...live,
    pauseStart: null,
    pauses: [...live.pauses, { from: live.pauseStart, to: at }],
  }
}

// ---------------------------------------------------------------------------
// the hook
// ---------------------------------------------------------------------------

/**
 * `useTracker`'s double, with the socket taken out.
 *
 * It returns the same fields, so App.jsx renders one viewer and never learns
 * which of the two it is holding — the demo is not a second implementation of the
 * page, it is a second source for it.
 *
 * Three of those fields are constants here, and each says something true:
 *
 * * `error` is always null. The tracker cannot be unreachable when it is in the
 *   same tab as the page. There is nothing to fail to reach.
 * * `busy` is always false. `busy` exists to disable a button for the length of a
 *   round trip, and there is no round trip: the state is already here.
 * * `refusal` is real, and is the point. The demo says no exactly where the
 *   instrument would.
 */
export function useDemoTracker() {
  const [state, setState] = useState(seed)
  const [now, setNow] = useState(() => Date.now())
  const [refusal, setRefusal] = useState(null)

  // The real viewer polls once a second; this ticks once a second. Same cadence,
  // same reason — the clock has to move — and no request either way.
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])

  const status = useMemo(() => statusOf(state.live, now), [state.live, now])
  const sessions = useMemo(() => sessionsOf(state.archive), [state.archive])

  const send = useCallback(
    async (command, body) => {
      const at = Date.now()
      setRefusal(null)
      try {
        // Computed from this render's state, which is what the click was looking
        // at when it was made. The real viewer cannot assume that — a Shortcut or
        // the widget may have moved underneath it, which is why it renders what
        // the server reports back rather than what the click assumed. Here you
        // are the only writer there is, so the click and the state agree.
        const next = perform(state, command, body, at)
        setState(next)
        setNow(at)
        return true
      } catch (err) {
        setRefusal(err.message)
        return false
      }
    },
    [state],
  )

  return { status, sessions, error: null, refusal, busy: false, send, reload: () => {} }
}
