import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { formatCompact, formatDate, formatTime } from '../lib/format.js'
import { blocksOf, midnightOf, nextMidnightAfter, toMs } from '../lib/timeline.js'
import { getJSON, postJSON } from '../lib/useTracker.js'
import Num from './Num.jsx'
import { DoneButton, TaskEditor, TypeTag } from './Planner.jsx'

/** One hour of the grid, in pixels. */
const HOUR = 44
/** Where the day opens when the page loads: the small hours are a scroll away. */
const FIRST_HOUR_SHOWN = 9
/** Dragging moves a block in steps of this many minutes. */
const SNAP = 15
const MINUTE = 60000

/** Monday's midnight, `weeks` weeks from this one. */
function mondayOf(weeks) {
  const today = new Date()
  return new Date(
    today.getFullYear(),
    today.getMonth(),
    today.getDate() - ((today.getDay() + 6) % 7) + weeks * 7,
  ).getTime()
}

/** The views, by how many days they show. */
const SPANS = [
  [1, 'Day'],
  [3, '3 days'],
  [7, 'Week'],
]

/** The view to open in: the last one chosen, else three days on a phone and the week elsewhere. */
function initialSpan() {
  try {
    const saved = Number(window.localStorage.getItem('calendar.span'))
    if (SPANS.some(([span]) => span === saved)) return saved
  } catch {
    // storage can be refused; the default below is fine
  }
  return window.matchMedia('(max-width: 45rem)').matches ? 3 : 7
}

/** The first midnight shown: Monday for the week, else today, moved `offset` views along. */
function startOf(span, offset) {
  if (span === 7) return mondayOf(offset)
  const today = new Date()
  return new Date(today.getFullYear(), today.getMonth(), today.getDate() + offset * span).getTime()
}

/** The `span` + 1 midnights from `start` -- by date, so a DST day is still one day. */
function daysFrom(start, span) {
  const first = new Date(start)
  return Array.from({ length: span + 1 }, (_, i) =>
    new Date(first.getFullYear(), first.getMonth(), first.getDate() + i).getTime(),
  )
}

/**
 * Follow the pointer until it is released. The listeners are attached right here,
 * inside the press handler -- not in an effect a render later, which a quick
 * click-and-drag can finish before, losing the release.
 */
function follow(onMove, onUp) {
  const move = (event) => onMove(event)
  const up = (event) => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
    onUp(event)
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
}

/** Minutes past midnight, snapped to the grid. */
const snap = (minutes) => Math.round(minutes / SNAP) * SNAP

/** Cut an item at every midnight it crosses, so each day holds only its own part. */
function* piecesOf(item) {
  let from = item.from
  while (from < item.to) {
    const to = Math.min(item.to, nextMidnightAfter(from))
    yield { ...item, from, to, day: midnightOf(from), whole: { from: item.from, to: item.to } }
    from = to
  }
}

/** An ms timestamp as an ISO string Python 3.9 reads (no "Z"). */
const iso = (ms) => new Date(ms).toISOString().replace('Z', '+00:00')

/** Everything the week is made of: worked, live, planned, static, and the calendar's own. */
function itemsOf({ sessions, status, plan, events }) {
  const items = []
  // Work on a task that is now done is shown done: green, dimmed, crossed out.
  const finished = plan?.done_work ?? { sessions: {}, entered: [] }
  for (const session of sessions) {
    const done = finished.sessions[session.id]
    for (const block of blocksOf(session)) {
      if (block.kind === 'work') {
        items.push(
          done
            ? { kind: 'done', lane: 'did', from: block.from, to: block.to, title: done.title }
            : { kind: 'work', lane: 'did', from: block.from, to: block.to, title: session.task, sessionId: session.id },
        )
      }
    }
  }
  for (const entry of finished.entered) {
    items.push({ kind: 'done', lane: 'did', from: toMs(entry.start), to: toMs(entry.end), title: entry.title, workId: entry.id })
  }
  if (status && status.state !== 'idle') {
    const blocks = blocksOf(status).filter((block) => block.kind === 'work')
    blocks.forEach((block, i) => {
      const live = i === blocks.length - 1 && status.state === 'running'
      items.push({ kind: live ? 'live' : 'work', lane: 'did', from: block.from, to: block.to, title: status.task, live: true })
    })
  }
  for (const task of plan?.tasks ?? []) {
    if (task.status === 'done') continue
    // A block that ends after its task is due is time booked too late: it is
    // marked, with how far past the deadline it runs.
    const due = task.deadline ? toMs(task.deadline) : null
    const past = (end) => (due !== null && end > due ? { overdue: Math.round((end - due) / MINUTE), due: task.deadline } : {})
    for (const block of task.blocks) {
      const to = toMs(block.end)
      items.push({ kind: 'plan', lane: 'plan', from: toMs(block.start), to, title: task.title, taskId: task.id, type: task.type, ...past(to) })
    }
    for (const pin of task.pins ?? []) {
      const to = toMs(pin.end)
      items.push({ kind: 'pin', lane: 'plan', from: toMs(pin.start), to, title: task.title, taskId: task.id, pinId: pin.id, type: task.type, ...past(to) })
    }
  }
  for (const event of events) {
    items.push({
      kind: 'event',
      lane: 'plan',
      from: toMs(event.start),
      to: toMs(event.end),
      title: event.title,
      note: event.calendar,
      color: event.color,
      eventId: event.id,
      editable: event.editable,
      occurrence: event.start,
    })
  }
  return items.filter((item) => item.to > item.from).flatMap((item) => [...piecesOf(item)])
}

/** What a block is called, in the card that opens when you click it. */
const KIND = {
  plan: 'Planned — the scheduler may move it',
  pin: 'Static — stays where you put it',
  event: 'Calendar event',
  work: 'Worked',
  live: 'Working now',
  done: 'Done',
}

const clockOf = (from, to) => `${formatTime(iso(from))}–${formatTime(iso(to))}`

function Block({ item, split, offset, onGrab, onOpen, selected }) {
  const top = ((item.from - item.day) / 3600000) * HOUR
  const height = Math.max(((item.to - item.from) / 3600000) * HOUR, 3)
  const movable = item.kind === 'plan' || item.kind === 'pin' || item.workId != null
  const label = `${item.title || 'no task'} · ${clockOf(item.from, item.to)}${item.note ? ` · ${item.note}` : ''}${
    item.overdue ? ` · ends ${overdueBy(item.overdue)} after its deadline` : ''
  }`
  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`${KIND[item.kind]}: ${label}`}
      className={[
        'cal__block',
        `cal__block--${item.kind}`,
        `cal__lane--${split ? item.lane : 'all'}`,
        movable ? 'cal__block--movable' : '',
        offset ? 'cal__block--dragging' : '',
        selected ? 'cal__block--selected' : '',
        item.overdue ? 'cal__block--overdue' : '',
      ].join(' ')}
      style={{
        top: `${top}px`,
        height: `${height}px`,
        ...(item.color && { '--cal': item.color }),
        ...(offset && { transform: `translate(${offset.x}px, ${offset.y}px)` }),
      }}
      title={label}
      onPointerDown={movable ? (event) => onGrab(item, event) : undefined}
      onClick={movable ? undefined : () => onOpen(item)}
      onKeyDown={(event) => (event.key === 'Enter' || event.key === ' ') && (event.preventDefault(), onOpen(item))}
    >
      {height >= 22 && (
        <span className="cal__head-line">
          <span className="cal__title">{item.title || 'no task'}</span>
          {item.type && <TypeTag type={item.type} />}
        </span>
      )}
      {height >= 38 && (
        <span className="cal__clock">
          <Num>{offset?.clock ?? clockOf(item.from, item.to)}</Num>
          {item.overdue && <span className="cal__overdue"> · past deadline</span>}
        </span>
      )}
    </div>
  )
}

/** "45m" or "2h 10m". */
const overdueBy = (minutes) => (minutes < 60 ? `${minutes}m` : `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m`)

/** "YYYY-MM-DD" and "HH:MM" of a moment, in local time, for the date and time inputs. */
function localParts(ms) {
  const at = new Date(ms - new Date(ms).getTimezoneOffset() * MINUTE).toISOString()
  return { date: at.slice(0, 10), time: at.slice(11, 16) }
}

/**
 * The card a click opens: the block, editable in place.
 *
 * A planned block edits its *task* -- priority, estimate, deadline, the things the
 * scheduler plans by -- and leaves the time to the scheduler; dragging it, or
 * "Make static", is how you take the time over. A static block edits its title and
 * its time, since the time is yours now. A calendar event edits its own
 * title and time, written back to Apple Calendar (that one occurrence). A worked
 * block edits only what it was spent on: the tracker never rewrites a recorded time.
 */
function Detail({ item, tasks, busy, act, send, onClose }) {
  const span = item.whole ?? item
  const planner = item.kind === 'plan' || item.kind === 'pin'
  const event = item.kind === 'event'
  const worked = item.kind === 'work' || item.kind === 'live'
  // Done work entered by hand is the planner's own record, so its time can move.
  const entered = item.workId != null
  const timed = planner || (event && item.editable) || entered
  const readOnly = item.kind === 'done' || (event && !item.editable)
  const task = planner ? tasks.find((candidate) => candidate.id === item.taskId) : null

  const initial = useMemo(() => {
    const from = localParts(span.from)
    return { title: item.title ?? '', date: from.date, start: from.time, end: localParts(span.to).time }
  }, [item, span.from, span.to])
  const [draft, setDraft] = useState(initial)
  const [problem, setProblem] = useState(null)
  useEffect(() => setDraft(initial), [initial])

  const set = (field) => (e) => setDraft((old) => ({ ...old, [field]: e.target.value }))
  const titleChanged = draft.title.trim() !== (item.title ?? '').trim()
  const timeChanged = draft.date !== initial.date || draft.start !== initial.start || draft.end !== initial.end
  const dirty = titleChanged || (timed && timeChanged)

  const save = async () => {
    setProblem(null)
    const from = new Date(`${draft.date}T${draft.start}`).getTime()
    const to = new Date(`${draft.date}T${draft.end}`).getTime()
    if (timed && timeChanged && !(to > from)) {
      setProblem('It has to end after it starts.')
      return
    }
    if (planner) {
      if (titleChanged && !(await act('edit', { id: item.taskId, changes: { title: draft.title } }, false))) return
      if (timeChanged) await act('pin', { id: item.taskId, start: iso(from), end: iso(to), pin: item.pinId ?? null })
      else onClose()
    } else if (event) {
      await act('event', {
        id: item.eventId,
        occurrence: item.occurrence,
        ...(titleChanged && { title: draft.title }),
        ...(timeChanged && { start: iso(from), end: iso(to) }),
      })
    } else if (entered) {
      await act('work', { work: item.workId, start: iso(from), end: iso(to) })
    } else if (worked) {
      // The live session is named without an id: it is *the* one in progress.
      if (await send('task', item.live ? { task: draft.title || null } : { id: item.sessionId, task: draft.title || null })) onClose()
    }
  }

  if (item.kind === 'plan') {
    return (
      <div className="cal__detail cal__detail--wide" role="dialog" aria-label={item.title || 'Planned block'}>
        <p className="eyebrow">
          {KIND.plan} {item.type && <TypeTag type={item.type} />}
        </p>
        <p className="live__caption">
          {formatDate(iso(span.from))} <Num>{clockOf(span.from, span.to)}</Num> · drag it, or make it static, to fix
          the time yourself
        </p>
        {item.overdue && (
          <p className="plan-task__next--late cal__problem">
            Ends {overdueBy(item.overdue)} after its deadline ({formatDate(item.due)} {formatTime(item.due)}) — there is
            no free time before it. Move the deadline, shorten the estimate, or add hours.
          </p>
        )}
        {task && <TaskEditor task={task} busy={busy} send={act} />}
        <div className="controls">
          <button
            type="button"
            className="btn btn--go"
            disabled={busy}
            onClick={() => act('pin', { id: item.taskId, start: iso(span.from), end: iso(span.to) })}
          >
            Make static
          </button>
          <button type="button" className="btn btn--stop" disabled={busy} onClick={() => act('start', { id: item.taskId })}>
            Start
          </button>
          {task && <DoneButton task={task} busy={busy} send={act} className="btn btn--stop" />}
          <button type="button" className="btn btn--stop cal__close" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    )
  }

  const kindLabel = event ? item.note : KIND[item.kind]
  return (
    <div className="cal__detail" role="dialog" aria-label={item.title || 'Block'}>
      <p className="eyebrow">
        {event && <span className="cal__dot" style={{ '--cal': item.color }} />}
        {kindLabel}
        {event && !item.editable && <> · read-only calendar</>}
      </p>

      {readOnly ? (
        <p className="plan-now__title">{item.title}</p>
      ) : (
        <label className="plan-field plan-field--wide cal__field">
          <span>{worked ? 'What was this spent on?' : planner ? 'Task' : 'Title'}</span>
          <input value={draft.title} onChange={set('title')} maxLength={200} />
        </label>
      )}

      {timed ? (
        <div className="cal__when">
          <label className="plan-field">
            <span>Day</span>
            <input type="date" value={draft.date} onChange={set('date')} />
          </label>
          <label className="plan-field">
            <span>From</span>
            <input type="time" step="300" value={draft.start} onChange={set('start')} />
          </label>
          <label className="plan-field">
            <span>To</span>
            <input type="time" step="300" value={draft.end} onChange={set('end')} />
          </label>
        </div>
      ) : (
        <p className="live__caption">
          {formatDate(iso(span.from))} <Num>{clockOf(span.from, span.to)}</Num>
          {worked && <> · recorded times are never rewritten</>}
        </p>
      )}

      {problem && <p className="plan-task__next--late cal__problem">{problem}</p>}
      {item.overdue && !problem && (
        <p className="plan-task__next--late cal__problem">
          Ends {overdueBy(item.overdue)} after its deadline ({formatDate(item.due)} {formatTime(item.due)}).
        </p>
      )}
      {planner && !problem && <p className="live__caption">Drag it, or give it a new time here.</p>}

      <div className="controls">
        {dirty && (
          <button type="button" className="btn btn--go" disabled={busy} onClick={save}>
            Save
          </button>
        )}
        {!dirty && item.kind === 'pin' && (
          <button type="button" className="btn btn--stop" disabled={busy} onClick={() => act('unpin', { pin: item.pinId })}>
            Give back to scheduler
          </button>
        )}
        {!dirty && planner && (
          <button type="button" className="btn btn--stop" disabled={busy} onClick={() => act('start', { id: item.taskId })}>
            Start
          </button>
        )}
        {!dirty && task && <DoneButton task={task} busy={busy} send={act} className="btn btn--stop" />}
        <button type="button" className="btn btn--stop cal__close" onClick={dirty ? () => setDraft(initial) : onClose}>
          {dirty ? 'Revert' : 'Close'}
        </button>
      </div>
    </div>
  )
}

/**
 * A new task, at a time picked in the grid. Fixed there as a static block: picking
 * the time was the point. The length picked becomes its estimate (said in the text,
 * where the parser takes a stated duration over its own guess).
 */
function Composer({ slot, busy, onAdd, onClose }) {
  const [text, setText] = useState('')
  const from = slot.day + slot.from * MINUTE
  const to = slot.day + slot.to * MINUTE
  const past = to <= Date.now()
  return (
    <form
      className="cal__detail"
      role="dialog"
      aria-label="New task"
      onSubmit={(event) => {
        event.preventDefault()
        if (text.trim()) onAdd(text.trim(), from, to)
      }}
    >
      <p className="eyebrow">New task · fixed at this time</p>
      <p className="live__caption">
        {formatDate(iso(from))} <Num>{clockOf(from, to)}</Num> · drag in the grid to pick another time
      </p>
      {past && (
        <p className="plan-task__next--late cal__problem">
          That time has already passed — pick a time from now on.
        </p>
      )}
      <input
        className="start__task cal__compose"
        autoFocus
        value={text}
        onChange={(event) => setText(event.target.value)}
        placeholder="What is it? e.g. call the bank about the invoice"
        aria-label="What is it?"
        maxLength={400}
        autoComplete="off"
      />
      <div className="controls">
        <button type="submit" className="btn btn--go" disabled={busy || past || !text.trim()}>
          Add
        </button>
        <button type="button" className="btn btn--stop cal__close" onClick={onClose}>
          Cancel
        </button>
      </div>
    </form>
  )
}

/** "09:30" -> hours since midnight (9.5). */
const hoursOf = (hhmm) => {
  const [h, m] = hhmm.split(':').map(Number)
  return h + m / 60
}

/** The windows the planner may fill on the day starting at `midnight`. */
const windowsOf = (plan, midnight) => plan?.hours?.days?.[String((new Date(midnight).getDay() + 6) % 7)] ?? []

/** Where a dragged block lands: whole days across, SNAP-minute steps down, on the grid. */
function landing(item, drag) {
  const days = Math.round(drag.dx / drag.colWidth)
  const minutes = Math.round(((drag.dy / HOUR) * 60) / SNAP) * SNAP
  const start = new Date(item.whole.from)
  start.setDate(start.getDate() + days) // by date, so a DST night is still one day
  const from = Math.round((start.getTime() + minutes * MINUTE) / (SNAP * MINUTE)) * SNAP * MINUTE
  return { days, from, to: from + (item.whole.to - item.whole.from) }
}

/**
 * The week, as a calendar: what was planned and what the calendar holds on the
 * left of each day, what was actually worked on the right — so a day's plan and
 * its reality are read side by side, against the same hours.
 *
 * Every block opens a card when clicked. A planned or static block can be dragged:
 * wherever it is dropped it stays, as a static block the scheduler plans around.
 */
/** The hours column, in pixels (2.25rem). */
const HOURS_W = 36
/** No day is drawn narrower than this; past it the strip just scrolls further. */
const MIN_COL = 96
/** Days held at once. The calendar endpoint serves six weeks, so the strip slides. */
const WINDOW = 42
/** How many days are added at an edge as you scroll toward it. */
const STEP = 7

/** `ms` moved by `n` whole days -- by date, so a DST day is still one day. */
function addDays(ms, n) {
  const at = new Date(ms)
  return new Date(at.getFullYear(), at.getMonth(), at.getDate() + n).getTime()
}

/** The days held around the view's own start (Monday for the week, else today). */
function around(span) {
  const anchor = startOf(span, 0)
  return { first: addDays(anchor, -14), count: 35, anchor }
}

export default function Calendar({ status, sessions, send }) {
  const [span, setSpan] = useState(initialSpan)
  const [range, setRange] = useState(() => around(span))
  const [visible, setVisible] = useState(null)
  const [colW, setColW] = useState(MIN_COL)
  const [plan, setPlan] = useState(null)
  const [calendar, setCalendar] = useState({ events: [], available: true, error: null })
  const [fault, setFault] = useState(null)
  const [busy, setBusy] = useState(false)
  const [selected, setSelected] = useState(null)
  const [drag, setDrag] = useState(null)
  const [reload, setReload] = useState(0)
  // A time being picked in the grid (pointer down), then the slot the composer is for.
  const [picking, setPicking] = useState(null)
  const [slot, setSlot] = useState(null)
  const body = useRef(null)
  // Where the strip should land once laid out (a day's midnight); and, while days
  // are added or dropped at an edge, the day at the left edge and how far into it
  // the view was -- so it lands back on exactly that, set absolutely. (A relative
  // correction would be applied twice: the browser re-snaps to the same day too.)
  const target = useRef(range.anchor)
  const keep = useRef(null)
  const sliding = useRef(false)

  const days = useMemo(() => daysFrom(range.first, range.count), [range.first, range.count])
  const columns = days.slice(0, range.count)
  const indexOf = (day) => columns.indexOf(day)

  // Columns are sized so a view's worth of days fills the width.
  useLayoutEffect(() => {
    const el = body.current
    if (!el) return undefined
    const measure = () => setColW(Math.max(MIN_COL, (el.clientWidth - HOURS_W) / span))
    measure()
    const watch = new ResizeObserver(measure)
    watch.observe(el)
    return () => watch.disconnect()
  }, [span])

  // After a layout: land on the target day, or hold still while the range slid.
  useLayoutEffect(() => {
    const el = body.current
    if (!el) return
    if (target.current !== null && indexOf(target.current) >= 0) {
      el.scrollLeft = indexOf(target.current) * colW
      const hour = new Date().getHours() + new Date().getMinutes() / 60
      el.scrollTop = Math.max(FIRST_HOUR_SHOWN, hour - 1) * HOUR
      target.current = null
    } else if (keep.current && indexOf(keep.current.day) >= 0) {
      el.scrollLeft = indexOf(keep.current.day) * colW + keep.current.into
      keep.current = null
    }
    sliding.current = false
    setVisible(Math.round(el.scrollLeft / colW))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range.first, range.count, colW])

  /** Add a week at an edge; past WINDOW days, drop one from the far side. */
  const extend = (direction) => {
    if (sliding.current || !body.current) return
    sliding.current = true
    const at = body.current.scrollLeft
    const index = Math.floor(at / colW)
    keep.current = { day: columns[index], into: at - index * colW }
    if (direction < 0) {
      setRange((r) => ({ ...r, first: addDays(r.first, -STEP), count: Math.min(r.count + STEP, WINDOW) }))
    } else {
      const over = Math.max(0, range.count + STEP - WINDOW)
      setRange((r) => ({ ...r, first: addDays(r.first, over), count: r.count + STEP - over }))
    }
  }

  const onScroll = () => {
    const el = body.current
    if (!el) return
    setVisible(Math.round(el.scrollLeft / colW))
    if (el.scrollLeft < colW * 2) extend(-1)
    else if (el.scrollLeft + el.clientWidth > el.scrollWidth - colW * 2) extend(1)
  }

  /** Glide to a day; if it is outside the days held, re-centre on it first. */
  const goTo = (day) => {
    const el = body.current
    if (indexOf(day) >= 0 && el) {
      el.scrollTo({ left: indexOf(day) * colW, behavior: 'smooth' })
      return
    }
    target.current = day
    setRange({ first: addDays(day, -14), count: 35, anchor: day })
  }
  const step = (direction) => body.current?.scrollBy({ left: direction * span * colW, behavior: 'smooth' })
  const toToday = () => goTo(startOf(span, 0))

  const choose = (next) => {
    setSpan(next)
    const fresh = around(next)
    target.current = fresh.anchor
    setRange(fresh)
    try {
      window.localStorage.setItem('calendar.span', String(next))
    } catch {
      // remembered or not, the view still changes
    }
  }

  const closeAll = () => {
    setSelected(null)
    setSlot(null)
  }

  // The keyboard: ← → move a view along, T comes back to today, Esc closes the card.
  const keyed = useRef({})
  keyed.current = { step, toToday }
  useEffect(() => {
    const keys = (event) => {
      if (event.target.closest?.('input, select, textarea') || event.metaKey || event.ctrlKey) return
      if (event.key === 'ArrowLeft') keyed.current.step(-1)
      else if (event.key === 'ArrowRight') keyed.current.step(1)
      else if (event.key === 't' || event.key === 'T') keyed.current.toToday()
      else if (event.key === 'Escape') closeAll()
      else return
      event.preventDefault()
    }
    window.addEventListener('keydown', keys)
    return () => window.removeEventListener('keydown', keys)
  }, [])

  // A press anywhere but the card or a block closes the card.
  useEffect(() => {
    if (!selected && !slot) return undefined
    const away = (event) => {
      if (!event.target.closest?.('.cal__detail, .cal__block, .cal__col')) closeAll()
    }
    window.addEventListener('pointerdown', away)
    return () => window.removeEventListener('pointerdown', away)
  }, [selected, slot])

  // Picking a time: press on an empty part of a day, drag to the length you want.
  const pick = (day, event) => {
    if (busy || event.button !== 0 || event.target.closest('.cal__block')) return
    event.preventDefault()
    setSelected(null)
    setSlot(null)
    const top = event.currentTarget.getBoundingClientRect().top
    const at = (e) => snap(((e.clientY - top) / HOUR) * 60)
    const anchor = at(event)
    setPicking({ day, anchor, cursor: anchor })
    follow(
      (e) => setPicking((old) => old && { ...old, cursor: at(e) }),
      (e) => {
        const cursor = at(e)
        const moved = Math.abs(cursor - anchor) >= SNAP
        const from = Math.max(0, moved ? Math.min(anchor, cursor) : anchor)
        const to = Math.min(24 * 60, moved ? Math.max(anchor, cursor) : anchor + 60)
        setPicking(null)
        setSlot({ day, from, to: Math.max(to, from + SNAP) })
      },
    )
  }

  /** Add a task from the composer, fixed at the slot it was written for. */
  const create = async (text, from, to) => {
    setBusy(true)
    setFault(null)
    try {
      const minutes = Math.round((to - from) / MINUTE)
      const added = await postJSON('/api/plan/add', { text: `${text}, ${minutes} min` })
      const id = Math.max(...added.plan.tasks.map((task) => task.id))
      setSlot(null)
      setBusy(false)
      await act('pin', { id, start: iso(from), end: iso(to) })
    } catch (err) {
      setFault(err.message)
      setBusy(false)
    }
  }

  // The plan, and the calendars' events for every day held.
  useEffect(() => {
    let cancelled = false
    Promise.all([
      getJSON('/api/plan'),
      getJSON(`/api/calendar?from=${encodeURIComponent(iso(days[0]))}&to=${encodeURIComponent(iso(days[range.count]))}`),
    ])
      .then(([nextPlan, nextCalendar]) => {
        if (cancelled) return
        setPlan(nextPlan)
        setCalendar(nextCalendar)
        setFault(null)
      })
      .catch((err) => !cancelled && setFault(err.message))
    return () => {
      cancelled = true
    }
  }, [days, range.count, reload])

  /** Send a planner command; adopt the plan it answers with. True if it went through. */
  const act = async (command, payload, close = true) => {
    setBusy(true)
    setFault(null)
    try {
      const result = await postJSON(`/api/plan/${command}`, payload)
      setPlan(result.plan)
      if (command === 'event') setReload((n) => n + 1) // the calendar itself changed
      if (close) setSelected(null)
      return true
    } catch (err) {
      setFault(err.message)
      return false
    } finally {
      setBusy(false)
    }
  }

  // Moving a block: followed on the window from the press itself, so neither a
  // fast pointer nor a quick click can slip past it.
  const grab = (item, event) => {
    if (busy || event.button !== 0) return
    event.preventDefault()
    setSelected(null)
    setSlot(null)
    const start = {
      item,
      x: event.clientX,
      y: event.clientY,
      colWidth: event.currentTarget.parentElement.getBoundingClientRect().width,
    }
    setDrag({ ...start, dx: 0, dy: 0 })
    follow(
      (e) => setDrag((old) => old && { ...old, dx: e.clientX - start.x, dy: e.clientY - start.y }),
      (e) => {
        const done = { ...start, dx: e.clientX - start.x, dy: e.clientY - start.y }
        setDrag(null)
        if (Math.abs(done.dx) < 4 && Math.abs(done.dy) < 4) {
          setSelected(item) // a click, not a drag
          return
        }
        const { from, to } = landing(item, done)
        if (item.workId != null) act('work', { work: item.workId, start: iso(from), end: iso(to) })
        else act('pin', { id: item.taskId, start: iso(from), end: iso(to), pin: item.pinId ?? null })
      },
    )
  }

  const items = useMemo(
    () =>
      itemsOf({ sessions: sessions?.sessions ?? [], status, plan, events: calendar.events }).filter(
        (item) => item.day >= days[0] && item.day < days[range.count],
      ),
    [sessions, status, plan, calendar, days, range.count],
  )

  // Every calendar that has an event in the days held, with its own colour, for the key.
  const calendars = useMemo(() => {
    const seen = new Map()
    for (const event of calendar.events) seen.set(event.calendar, event.color)
    return [...seen.entries()]
  }, [calendar])

  const now = Date.now()
  const today = midnightOf(now)
  const labels = Array.from({ length: 24 }, (_, hour) => hour)

  const worked = (day) =>
    items
      .filter((item) => item.day === day && item.lane === 'did')
      .reduce((sum, item) => sum + (item.to - item.from) / 1000, 0)

  // What is on screen now: the header names it, and counts its hours.
  const first = Math.max(0, Math.min(visible ?? indexOf(range.anchor), columns.length - 1))
  const shown = columns.slice(first, first + span)
  const viewWorked = shown.reduce((sum, day) => sum + worked(day), 0)
  const atToday = shown[0] === startOf(span, 0)

  const range_ =
    span === 1
      ? new Date(shown[0]).toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })
      : `${new Date(shown[0]).toLocaleDateString([], { day: 'numeric', month: 'short' })} – ${new Date(
          shown[shown.length - 1],
        ).toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' })}`
  const unit = span === 1 ? 'day' : span === 3 ? '3 days' : 'week'

  /** The ghost of the slot being picked, or picked, on this day. */
  const ghostOn = (day) => {
    const range = picking?.day === day
      ? [Math.min(picking.anchor, picking.cursor), Math.max(picking.anchor + SNAP, picking.cursor)]
      : slot?.day === day
        ? [slot.from, slot.to]
        : null
    if (!range) return null
    return (
      <div className="cal__block cal__block--new cal__lane--all" style={{ top: `${(range[0] / 60) * HOUR}px`, height: `${((range[1] - range[0]) / 60) * HOUR}px` }}>
        <span className="cal__clock">
          <Num>{clockOf(day + range[0] * MINUTE, day + range[1] * MINUTE)}</Num>
        </span>
      </div>
    )
  }

  /** Where the block being dragged is drawn right now, if it is that one. */
  const offsetOf = (item) => {
    if (
      !drag ||
      drag.item.kind !== item.kind ||
      drag.item.taskId !== item.taskId ||
      drag.item.pinId !== item.pinId ||
      drag.item.workId !== item.workId ||
      drag.item.whole.from !== item.whole.from
    )
      return null
    const land = landing(drag.item, drag)
    return {
      x: land.days * drag.colWidth,
      y: ((land.from - item.whole.from) / 3600000 - land.days * 24) * HOUR,
      clock: clockOf(land.from, land.to),
    }
  }

  return (
    <section className="calendar">
      <header className="cal__bar">
        <div className="cal__where">
          <button type="button" className="cal__arrow" onClick={() => step(-1)} aria-label={`Previous ${unit}`} title="Previous (←) — or scroll sideways">
            ‹
          </button>
          <button type="button" className="cal__arrow" onClick={() => step(1)} aria-label={`Next ${unit}`} title="Next (→) — or scroll sideways">
            ›
          </button>
          <h2 className="cal__range">{range_}</h2>
          <button type="button" className="btn btn--stop cal__today" onClick={toToday} disabled={atToday} title="Back to today (T)">
            Today
          </button>
        </div>
        <div className="cal__views" role="group" aria-label="How much to show">
          {SPANS.map(([value, label]) => (
            <button
              key={value}
              type="button"
              className={`chip ${span === value ? 'chip--on' : ''}`}
              aria-pressed={span === value}
              onClick={() => choose(value)}
            >
              {label}
            </button>
          ))}
          <span className="dim cal__total">
            <Num>{formatCompact(viewWorked)}</Num> worked
          </span>
        </div>
      </header>

      {fault && (
        <p className="refusal" role="alert">
          {fault}
        </p>
      )}
      {calendar.error && (
        <p className="fault" role="alert">
          Calendar — {calendar.error}
        </p>
      )}

      <p className="cal__legend dim">
        <span className="cal__key cal__key--open" /> plannable
        <span className="cal__key cal__key--plan" /> planned
        <span className="cal__key cal__key--pin" /> static
        <span className="cal__key cal__key--overdue" /> past deadline
        <span className="cal__key cal__key--work" /> worked
        <span className="cal__key cal__key--done" /> done
        {calendars.map(([name, color]) => (
          <span key={name} className="cal__legend-item">
            <span className="cal__key cal__key--event" style={{ '--cal': color }} /> {name}
          </span>
        ))}
        {!calendar.available && <> · Apple Calendar is off on this server</>}
        <span className="cal__hint">Scroll sideways to move through time · click or drag an empty time to add a task · drag a planned block to fix it</span>
      </p>

      <div className="cal__scroll">
        <div
          className={`cal ${drag ? 'cal--dragging' : ''}`}
          ref={body}
          onScroll={onScroll}
          style={{ '--days': range.count, '--col': `${colW}px`, '--hour': `${HOUR}px` }}
        >
          <div className="cal__canvas">
            <div className="cal__row cal__row--head">
              <div className="cal__corner" />
              {columns.map((day) => (
                <div key={day} className={`cal__day ${day === today ? 'cal__day--today' : ''}`}>
                  <span className="eyebrow">{new Date(day).toLocaleDateString([], { weekday: 'short' })}</span>
                  <span className="cal__date">
                    <Num>{new Date(day).getDate()}</Num>
                    {new Date(day).getDate() === 1 && (
                      <span className="cal__month"> {new Date(day).toLocaleDateString([], { month: 'short' })}</span>
                    )}
                  </span>
                  <span className="cal__worked dim">
                    {worked(day) > 0 ? <Num>{formatCompact(worked(day))}</Num> : '—'}
                  </span>
                </div>
              ))}
            </div>

            <div className="cal__row cal__grid">
              <div className="cal__hours">
                {labels.map((hour) => (
                  <span key={hour} className="cal__hour" style={{ top: `${hour * HOUR}px` }}>
                    <Num>{String(hour).padStart(2, '0')}</Num>
                  </span>
                ))}
              </div>
              {columns.map((day) => {
                const mine = items.filter((item) => item.day === day)
                // Two lanes only when the day has both a plan and a record to
                // compare; a day of only one gets the whole width for it.
                const split = new Set(mine.map((item) => item.lane)).size > 1
                return (
                  <div
                    key={day}
                    className={`cal__col ${day === today ? 'cal__col--today' : ''}`}
                    onPointerDown={(event) => pick(day, event)}
                  >
                    {windowsOf(plan, day).map(([opens, closes]) => (
                      <div
                        key={opens}
                        className="cal__open"
                        style={{ top: `${hoursOf(opens) * HOUR}px`, height: `${(hoursOf(closes) - hoursOf(opens)) * HOUR}px` }}
                        title={`The planner may use ${opens}–${closes}`}
                      />
                    ))}
                    {mine.map((item) => (
                      <Block
                        key={`${item.kind}-${item.pinId ?? ''}-${item.from}-${item.title}`}
                        item={item}
                        split={split}
                        offset={offsetOf(item)}
                        onGrab={grab}
                        onOpen={setSelected}
                        selected={selected && selected.kind === item.kind && selected.from === item.from && selected.title === item.title}
                      />
                    ))}
                    {ghostOn(day)}
                    {day === today && <div className="cal__now" style={{ top: `${((now - day) / 3600000) * HOUR}px` }} />}
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      </div>

      {slot && (
        <Composer key={`${slot.day}-${slot.from}`} slot={slot} busy={busy} onAdd={create} onClose={() => setSlot(null)} />
      )}

      {selected && (
        <Detail
          item={selected}
          tasks={plan?.tasks ?? []}
          busy={busy}
          act={act}
          send={send}
          onClose={() => setSelected(null)}
        />
      )}
    </section>
  )
}
