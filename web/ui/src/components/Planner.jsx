import { useCallback, useEffect, useMemo, useState } from 'react'
import { formatCompact, formatDate, formatTime } from '../lib/format.js'
import { midnightOf, minutesOf, toMs } from '../lib/timeline.js'
import { getJSON, postJSON } from '../lib/useTracker.js'
import Num from './Num.jsx'
import Strip, { Ruler } from './Strip.jsx'

/** How often the plan is re-read: often while Claude is parsing, rarely otherwise. */
const PARSING_INTERVAL = 2000
const IDLE_INTERVAL = 60000

/**
 * The planner's half of the page: tasks, the plan, and what is on now.
 *
 * Like the tracker's buttons, nothing here is optimistic. Every command answers
 * with the plan the server read back afterwards, and that is what is drawn.
 */
function usePlan() {
  const [plan, setPlan] = useState(null)
  const [refusal, setRefusal] = useState(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      setPlan(await getJSON('/api/plan'))
    } catch (err) {
      setRefusal(err.message)
    }
  }, [])

  const parsing = plan?.tasks.some((task) => task.parsed === 'pending') ?? false

  useEffect(() => {
    load()
    const timer = setInterval(load, parsing ? PARSING_INTERVAL : IDLE_INTERVAL)
    return () => clearInterval(timer)
  }, [load, parsing])

  const send = useCallback(async (command, body) => {
    setBusy(true)
    setRefusal(null)
    try {
      setPlan((await postJSON(`/api/plan/${command}`, body)).plan)
      return true
    } catch (err) {
      setRefusal(err.message)
      return false
    } finally {
      setBusy(false)
    }
  }, [])

  return { plan, refusal, busy, send }
}

/** What each kind of work is, said where the tag is pointed at. */
const TYPES = {
  deep: 'Deep: focused work that needs an unbroken stretch. Planned into the deep-work hours first.',
  shallow: 'Shallow: small admin you can do half-distracted. Goes into the first free slot.',
  errand: 'Errand: away from your desk. Goes into the first free slot.',
}

/** The kind of work, as a small rounded tag. */
export function TypeTag({ type }) {
  return (
    <span className={`tag tag--${type}`} title={TYPES[type]}>
      {type}
    </span>
  )
}

/** Every block a task has on the calendar, planned or static, in time order. */
const timeOf = (task) =>
  [...task.blocks, ...(task.pins ?? []).map((pin) => ({ ...pin, static: true }))].sort(
    (a, b) => toMs(a.start) - toMs(b.start),
  )

/** `1h 30m` from minutes. */
const minutes = (value) => (value ? formatCompact(value * 60) : '0m')

/** An ISO stamp as what a datetime-local input holds: local, to the minute. */
function toLocalInput(iso) {
  if (!iso) return ''
  const at = new Date(iso)
  return new Date(at.getTime() - at.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
}

/** And back: the input's local time, as an ISO stamp with an offset Python 3.9 reads. */
function fromLocalInput(value) {
  return value ? new Date(value).toISOString().replace('Z', '+00:00') : null
}

/** `Mon 5 Oct 09:00`. */
const when = (iso) => `${formatDate(iso)} ${formatTime(iso)}`

/** What the clock is on, or the block the plan says it should be on. */
function NowLine({ plan, status, busy, send }) {
  const onClock = status?.taskId ? plan.tasks.find((task) => String(task.id) === status.taskId) : null

  if (onClock) {
    return (
      <div className="plan-now">
        <p className="eyebrow">On the clock</p>
        <p className="plan-now__title">{onClock.title}</p>
        <p className="live__caption">
          {minutes(onClock.worked_min)} of {minutes(onClock.estimate_min)} so far. Stop it as:
        </p>
        {(
          <div className="controls">
            <button type="button" className="btn btn--go" disabled={busy} onClick={() => send('stop', { outcome: 'done' })}>
              Done
            </button>
            <button type="button" className="btn btn--stop" disabled={busy} onClick={() => send('stop', { outcome: 'more' })}>
              More left
            </button>
            <button type="button" className="btn btn--stop" disabled={busy} onClick={() => send('stop', { outcome: 'blocked' })}>
              Blocked
            </button>
          </div>
        )}
      </div>
    )
  }

  const block = plan.now ?? plan.next
  if (!block) return null
  const isNow = block === plan.now

  return (
    <div className="plan-now">
      <p className="eyebrow">{isNow ? 'Now' : 'Next'}</p>
      <p className="plan-now__title">{block.title}</p>
      <p className="live__caption">
        {isNow ? <>until {formatTime(block.end)}</> : <>{when(block.start)} – {formatTime(block.end)}</>}
      </p>
      {(
        <div className="controls">
          <button type="button" className="btn btn--go" disabled={busy} onClick={() => send('start', { id: block.task_id })}>
            Start it
          </button>
        </div>
      )}
    </div>
  )
}

/** Seconds covered by a set of [from, to] intervals, overlaps counted once. */
function covered(intervals) {
  let total = 0
  let reach = -Infinity
  for (const [from, to] of [...intervals].sort((a, b) => a[0] - b[0])) {
    const start = Math.max(from, reach)
    if (to > start) total += to - start
    reach = Math.max(reach, to)
  }
  return total / 1000
}

/**
 * The days ahead, one strip per day on the history's own ruler: everything that
 * already has a claim on the time -- what the planner booked, what you fixed by
 * hand, and what your calendars hold, each event in its calendar's colour.
 */
function Upcoming({ tasks, events, axis }) {
  // Closed until asked for: the summary in the caption is usually all you need.
  const [open, setOpen] = useState(false)
  const days = useMemo(() => {
    const byDay = new Map()
    const today = midnightOf(Date.now())
    const add = (base, segment, name, kind) => {
      const day = byDay.get(base) ?? { at: base, segments: [], names: [], tasks: new Set(), events: 0 }
      day.segments.push(segment)
      if (!day.names.includes(name)) day.names.push(name)
      if (kind === 'event') day.events += 1
      else day.tasks.add(name)
      byDay.set(base, day)
    }
    const segment = (kind, title, from, until, base, color) => ({
      kind,
      title,
      color,
      from: minutesOf(from, base),
      to: minutesOf(until, base),
      seconds: Math.round((until - from) / 1000),
      at: from,
      until,
    })
    for (const task of tasks) {
      for (const block of timeOf(task)) {
        const from = toMs(block.start)
        const until = toMs(block.end)
        const base = midnightOf(from)
        const kind = block.static ? 'pin' : 'plan'
        add(base, segment(kind, block.static ? `Static: ${task.title}` : task.title, from, until, base), task.title, kind)
      }
    }
    for (const event of events) {
      const from = toMs(event.start)
      const until = toMs(event.end)
      const base = midnightOf(from)
      if (base < today) continue
      add(base, segment('event', `${event.calendar}: ${event.title}`, from, Math.min(until, base + 86400000), base, event.color), event.title, 'event')
    }
    return [...byDay.values()]
      .map((day) => ({ ...day, seconds: covered(day.segments.map((s) => [s.at, s.until])) }))
      .sort((a, b) => a.at - b.at)
  }, [tasks, events])

  if (days.length === 0) return null
  const total = days.reduce((sum, day) => sum + day.seconds, 0)

  return (
    <>
      <button type="button" className="eyebrow plan-sub plan-toggle" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <span className="plan-toggle__arrow" aria-hidden="true">
          ▸
        </span>
        Booked time, by day
        <span className="plan-toggle__summary">
          {days.length} day{days.length === 1 ? '' : 's'} · <Num>{formatCompact(total)}</Num>
        </span>
      </button>
      {open && (
        <>
      <div className="history__ruler">
        <Ruler axis={axis} />
      </div>
      <ul className="days">
        {days.map((day, index) => {
          const counts = [
            day.tasks.size && `${day.tasks.size} task${day.tasks.size === 1 ? '' : 's'}`,
            day.events && `${day.events} event${day.events === 1 ? '' : 's'}`,
          ].filter(Boolean)
          return (
            <li className="day" key={day.at} style={{ '--row': index }}>
              <div className="day__row plan-day">
                <span className="day__date">
                  {formatDate(new Date(day.at).toISOString())}
                  <span className="day__task" title={day.names.join(' · ')}>
                    {day.names.join(' · ')}
                  </span>
                </span>
                <span className="day__worked">
                  <Num>{formatCompact(day.seconds)}</Num>
                </span>
                <span className="day__strip">
                  <Strip segments={day.segments} axis={axis} label={`Booked: ${day.names.join(', ')}`} />
                </span>
                <span className="day__breaks dim">{counts.join(' · ')}</span>
              </div>
            </li>
          )
        })}
      </ul>
        </>
      )}
    </>
  )
}

/** Every field the parser guessed, there to be corrected. Saves only what changed. */
export function TaskEditor({ task, busy, send, underRow = false }) {
  // Keyed on the task's content, not its identity: every poll hands over a new
  // object, and a draft must only be reset when the task itself has changed.
  const { blocks, worked_min, late, ...fields } = task
  const key = JSON.stringify(fields)
  const initial = useMemo(
    () => ({
      title: task.title,
      estimate_min: task.estimate_min,
      deadline: toLocalInput(task.deadline),
      earliest_start: toLocalInput(task.earliest_start),
      priority: task.priority,
      type: task.type,
      project: task.project ?? '',
      splittable: task.splittable,
      recurrence: task.recurrence ?? '',
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key],
  )
  const [draft, setDraft] = useState(initial)
  useEffect(() => setDraft(initial), [initial])

  const set = (field) => (event) => {
    const { type, checked, value } = event.target
    setDraft((old) => ({
      ...old,
      [field]: type === 'checkbox' ? checked : type === 'number' || field === 'priority' ? Number(value) : value,
    }))
  }

  const changes = {}
  for (const field of Object.keys(draft)) {
    if (draft[field] === initial[field]) continue
    changes[field] =
      field === 'deadline' || field === 'earliest_start' ? fromLocalInput(draft[field]) : draft[field]
  }
  const dirty = Object.keys(changes).length > 0

  return (
    <div className="day__open plan-edit">
      {task.raw !== task.title && <p className="plan-edit__raw dim">“{task.raw}”</p>}
      <div className="plan-edit__fields">
        <label className="plan-field plan-field--wide">
          <span>Title</span>
          <input value={draft.title} onChange={set('title')} maxLength={200} />
        </label>
        {/* Under a task row these are the row's own controls already; the editor
            holds only what the row does not. The calendar's card has no row. */}
        {!underRow && (
          <>
            <label className="plan-field">
              <span>Estimate, min</span>
              <input type="number" min="5" step="5" value={draft.estimate_min} onChange={set('estimate_min')} />
            </label>
            <label className="plan-field">
              <span>Priority</span>
              <select value={draft.priority} onChange={set('priority')}>
                <option value={1}>High</option>
                <option value={2}>Normal</option>
                <option value={3}>Low</option>
              </select>
            </label>
          </>
        )}
        <label className="plan-field">
          <span>Deadline</span>
          <input type="datetime-local" value={draft.deadline} onChange={set('deadline')} />
        </label>
        <label className="plan-field">
          <span>Not before</span>
          <input type="datetime-local" value={draft.earliest_start} onChange={set('earliest_start')} />
        </label>
        <label className="plan-field">
          <span>Kind</span>
          <select value={draft.type} onChange={set('type')}>
            <option value="deep">Deep</option>
            <option value="shallow">Shallow</option>
            <option value="errand">Errand</option>
          </select>
        </label>
        <label className="plan-field">
          <span>Project</span>
          <input value={draft.project} onChange={set('project')} maxLength={80} />
        </label>
        <label className="plan-field">
          <span>Repeats</span>
          <select value={draft.recurrence} onChange={set('recurrence')}>
            <option value="">Never</option>
            <option value="daily">Daily</option>
            <option value="weekdays">Weekdays</option>
            <option value="weekly">Weekly</option>
            <option value="monthly">Monthly</option>
          </select>
        </label>
        {!underRow && (
          <label className="plan-field plan-field--check">
            <input type="checkbox" checked={draft.splittable} onChange={set('splittable')} />
            <span>Can be split</span>
          </label>
        )}
      </div>

      <div className="controls">
        {dirty ? (
          <>
            <button type="button" className="btn btn--go" disabled={busy} onClick={() => send('edit', { id: task.id, changes })}>
              Save
            </button>
            <button type="button" className="btn btn--stop" disabled={busy} onClick={() => setDraft(initial)}>
              Revert
            </button>
          </>
        ) : (
          task.status === 'blocked' && (
            <button type="button" className="btn btn--stop" disabled={busy} onClick={() => send('edit', { id: task.id, changes: { status: 'open' } })}>
              Unblock
            </button>
          )
        )}
      </div>
    </div>
  )
}

/** 09:00 local, `days` days from today -- or next Monday, for "next week". */
function morning(days) {
  const now = new Date()
  const ahead = days === 'monday' ? (8 - now.getDay()) % 7 || 7 : days
  const at = new Date(now.getFullYear(), now.getMonth(), now.getDate() + ahead, 9)
  return at.toISOString().replace('Z', '+00:00')
}

/** An ISO stamp moved on by whole days, as one Python 3.9 reads. */
const laterBy = (iso, days) => new Date(toMs(iso) + days * 86400000).toISOString().replace('Z', '+00:00')

/**
 * The changes you make to a task without opening it: how much it matters, how long
 * it takes, when it may start, when it is due, and whether it is still a task at all.
 * Each is one edit, answered with the replanned plan -- nothing here guesses.
 */
function QuickControls({ task, busy, send, onClock }) {
  const [confirming, setConfirming] = useState(false)
  const edit = (changes) => send('edit', { id: task.id, changes })

  return (
    <div className="plan-quick" role="group" aria-label={`Change ${task.title}`}>
      <span className="plan-quick__group">
        <span className="plan-quick__label">Priority</span>
        {[
          [1, 'High'],
          [2, 'Normal'],
          [3, 'Low'],
        ].map(([level, label]) => (
          <button
            key={level}
            type="button"
            className={`chip ${task.priority === level ? 'chip--on' : ''}`}
            aria-pressed={task.priority === level}
            disabled={busy || task.priority === level}
            onClick={() => edit({ priority: level })}
          >
            {label}
          </button>
        ))}
      </span>

      <span className="plan-quick__group">
        <span className="plan-quick__label">Estimate</span>
        <button
          type="button"
          className="chip"
          aria-label="Estimate 15 minutes less"
          disabled={busy || task.estimate_min <= 5}
          onClick={() => edit({ estimate_min: Math.max(5, task.estimate_min - 15) })}
        >
          −15m
        </button>
        <span className="plan-quick__value">
          <Num>{minutes(task.estimate_min)}</Num>
        </span>
        <button
          type="button"
          className="chip"
          aria-label="Estimate 15 minutes more"
          disabled={busy}
          onClick={() => edit({ estimate_min: task.estimate_min + 15 })}
        >
          +15m
        </button>
      </span>

      <span className="plan-quick__group">
        <button
          type="button"
          className={`chip ${task.splittable ? 'chip--on' : ''}`}
          aria-pressed={task.splittable}
          title="Split into sessions between the shortest and longest session (Hours). Anything over the longest is split regardless."
          disabled={busy}
          onClick={() => edit({ splittable: !task.splittable })}
        >
          {task.splittable ? '✓ ' : ''}Can split
        </button>
      </span>

      <span className="plan-quick__group">
        <span className="plan-quick__label" title="Don't plan it before then">Not before</span>
        <button type="button" className="chip" disabled={busy} onClick={() => edit({ earliest_start: morning(1) })}>
          Tomorrow
        </button>
        <button type="button" className="chip" disabled={busy} onClick={() => edit({ earliest_start: morning('monday') })}>
          Next week
        </button>
        {task.earliest_start && (
          <button type="button" className="chip" disabled={busy} onClick={() => edit({ earliest_start: null })}>
            Any time
          </button>
        )}
      </span>

      {task.deadline && (
        <span className="plan-quick__group">
          <span className="plan-quick__label">Deadline</span>
          <button type="button" className="chip" disabled={busy} onClick={() => edit({ deadline: laterBy(task.deadline, 1) })}>
            +1 day
          </button>
          <button type="button" className="chip" disabled={busy} onClick={() => edit({ deadline: laterBy(task.deadline, 7) })}>
            +1 week
          </button>
        </span>
      )}

      <span className="plan-quick__group plan-quick__end">
        {!onClock && (
          <button type="button" className="chip chip--go" disabled={busy} onClick={() => send('start', { id: task.id })}>
            Start
          </button>
        )}
        <DoneButton task={task} busy={busy} send={send} />
        <button
          type="button"
          className={`chip ${confirming ? 'chip--warn' : ''}`}
          disabled={busy}
          onClick={() => (confirming ? send('delete', { id: task.id }) : setConfirming(true))}
          onBlur={() => setConfirming(false)}
        >
          {confirming ? 'Really delete?' : 'Delete'}
        </button>
      </span>
    </div>
  )
}

/**
 * Done. A task with tracked time is simply closed. One with none asks how long it
 * took (its estimate, to start with) and records that much, ending now.
 */
export function DoneButton({ task, busy, send, className = 'chip' }) {
  const [asking, setAsking] = useState(false)
  const [spent, setSpent] = useState(task.estimate_min)
  if (task.worked_min > 0 || !asking) {
    return (
      <button
        type="button"
        className={className}
        disabled={busy}
        onClick={() => (task.worked_min > 0 ? send('done', { id: task.id }) : (setSpent(task.estimate_min), setAsking(true)))}
      >
        Done
      </button>
    )
  }
  const minutesSpent = Number(spent)
  const valid = Number.isInteger(minutesSpent) && minutesSpent >= 1 && minutesSpent <= 1440
  return (
    <span className="plan-done">
      <label className="plan-done__ask">
        How long did it take?
        <input
          type="number"
          min="1"
          max="1440"
          step="5"
          value={spent}
          autoFocus
          onChange={(event) => setSpent(event.target.value)}
          onKeyDown={(event) => event.key === 'Enter' && valid && send('done', { id: task.id, minutes: minutesSpent })}
        />
        min
      </label>
      <button type="button" className="chip chip--go" disabled={busy || !valid} onClick={() => send('done', { id: task.id, minutes: minutesSpent })}>
        Done
      </button>
      <button type="button" className="chip" disabled={busy} onClick={() => send('done', { id: task.id })}>
        Without time
      </button>
      <button type="button" className="chip" onClick={() => setAsking(false)}>
        Cancel
      </button>
    </span>
  )
}

/** One task: what it is, how far along, when it is next planned and when it will be done. */
function Task({ task, busy, send, onClock }) {
  const [open, setOpen] = useState(false)

  const booked = timeOf(task)
  let next = booked[0] ? when(booked[0].start) : 'not planned'
  if (task.status === 'blocked') next = 'blocked'
  if (task.parsed === 'pending') next = 'parsing…'

  const meta = [task.project, task.priority === 1 ? 'high' : task.priority === 3 ? 'low' : null]
    .filter(Boolean)
    .join(' · ')
  // When the plan has it finished: the end of its last block.
  const eta = booked.length ? booked[booked.length - 1].end : null

  return (
    <li className="day">
      <button
        type="button"
        className="day__row plan-task"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <span className="day__date">
          <span className="plan-task__title">
            {task.title} <TypeTag type={task.type} />
          </span>
          <span className="day__task dim">
            {/* Only the parts a task has, joined -- so a task with no project does
                not open on a stray separator. */}
            {[
              meta,
              task.earliest_start && `not before ${when(task.earliest_start)}`,
              task.deadline && `due ${when(task.deadline)}`,
              eta && `done by ${when(eta)}`,
              task.pad && task.pad !== 1 && `planned ×${task.pad.toFixed(1)}`,
              task.parsed === 'failed' && 'not understood — open to fill in',
            ]
              .filter(Boolean)
              .join(' · ')}
          </span>
        </span>
        <span className="day__worked plan-task__figure">
          <span>
            <Num>{minutes(task.worked_min)}</Num>
            <span className="dim"> of </span>
            <Num>{minutes(task.estimate_min)}</Num>
          </span>
          <span className="plan-task__caption">worked</span>
        </span>
        <span className={`plan-task__next plan-task__figure ${task.late ? 'plan-task__next--late' : 'dim'}`}>
          <span>{onClock ? 'on the clock' : task.late ? `late · ${next}` : next}</span>
          <span className="plan-task__caption">{onClock ? '' : 'next block'}</span>
        </span>
      </button>
      {/* The controls wait for the click: a list of tasks should read as a list,
          not as a wall of buttons. */}
      {open && (
        <>
          <QuickControls task={task} busy={busy} send={send} onClock={onClock} />
          <TaskEditor task={task} busy={busy} send={send} underRow />
        </>
      )}
    </li>
  )
}

const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

/**
 * When the planner may put work: per weekday, any number of windows (a lunch is
 * the gap between two), and the window deep work is tried in first. A day with no
 * window is a day off. Saving replans at once.
 */
function HoursEditor({ hours, busy, send, onClose }) {
  const [draft, setDraft] = useState(hours)
  const dirty = JSON.stringify(draft) !== JSON.stringify(hours)

  const setDay = (day, windows) => setDraft((old) => ({ ...old, days: { ...old.days, [day]: windows } }))
  const setTime = (day, index, side, value) =>
    setDay(
      day,
      draft.days[day].map((window, i) => (i === index ? (side ? [window[0], value] : [value, window[1]]) : window)),
    )

  return (
    <div className="plan-now hours">
      <p className="eyebrow">When and how to plan</p>
      <ol className="hours__days">
        {WEEKDAYS.map((name, index) => {
          const day = String(index)
          const windows = draft.days[day] ?? []
          return (
            <li key={day} className="hours__day">
              <span className="hours__name">{name}</span>
              <span className="hours__windows">
                {windows.length === 0 && <span className="dim hours__off">off</span>}
                {windows.map((window, i) => (
                  <span key={i} className="hours__window">
                    <input type="time" step="900" value={window[0]} aria-label={`${name} window ${i + 1} opens`} onChange={(e) => setTime(day, i, 0, e.target.value)} />
                    <span className="dim">–</span>
                    <input type="time" step="900" value={window[1]} aria-label={`${name} window ${i + 1} closes`} onChange={(e) => setTime(day, i, 1, e.target.value)} />
                    <button type="button" className="hours__remove" aria-label={`Remove ${name} window ${i + 1}`} onClick={() => setDay(day, windows.filter((_, j) => j !== i))}>
                      ×
                    </button>
                  </span>
                ))}
                <button
                  type="button"
                  className="chip"
                  onClick={() => setDay(day, [...windows, windows.length ? [windows[windows.length - 1][1], '18:00'] : ['09:00', '18:00']])}
                >
                  + window
                </button>
              </span>
            </li>
          )
        })}
        {[
          ['buffer', 'Buffer between blocks', 0, 60, 'Kept free around meetings and between planned blocks.', 'min', 5],
          ['min_session', 'Shortest session', 5, 240, 'A split task is never booked in less than this.', 'min', 5],
          ['max_session', 'Longest session', 15, 720, 'Nothing is booked for longer; a longer task is always split.', 'min', 5],
          ['lead_days', 'Start before deadline', 0, 60, 'A task with a deadline is not planned earlier than this, unless you give it its own "not before". 0: as soon as possible.', 'days', 1],
        ].map(([key, name, low, high, help, unit, step], i) => (
          <li key={key} className={`hours__day ${i === 0 ? 'hours__day--deep' : ''}`}>
            <span className="hours__name">{name}</span>
            <span className="hours__windows">
              <span className="hours__window">
                <input
                  type="number"
                  min={low}
                  max={high}
                  step={step}
                  value={draft[key]}
                  aria-label={`${name}, minutes`}
                  onChange={(e) => setDraft((old) => ({ ...old, [key]: Number(e.target.value) }))}
                />
                <span className="dim">{unit}</span>
              </span>
              <span className="dim hours__help">{help}</span>
            </span>
          </li>
        ))}
        <li className="hours__day">
          <span className="hours__name">Deep work first</span>
          <span className="hours__windows">
            {draft.deep ? (
              <span className="hours__window">
                <input type="time" step="900" value={draft.deep[0]} aria-label="Deep work opens" onChange={(e) => setDraft((old) => ({ ...old, deep: [e.target.value, old.deep[1]] }))} />
                <span className="dim">–</span>
                <input type="time" step="900" value={draft.deep[1]} aria-label="Deep work closes" onChange={(e) => setDraft((old) => ({ ...old, deep: [old.deep[0], e.target.value] }))} />
                <button type="button" className="hours__remove" aria-label="No deep-work window" onClick={() => setDraft((old) => ({ ...old, deep: null }))}>
                  ×
                </button>
              </span>
            ) : (
              <button type="button" className="chip" onClick={() => setDraft((old) => ({ ...old, deep: ['09:00', '12:00'] }))}>
                + window
              </button>
            )}
          </span>
        </li>
      </ol>
      <div className="controls">
        <button
          type="button"
          className="btn btn--go"
          disabled={busy || !dirty}
          onClick={async () => (await send('hours', { hours: draft })) && onClose()}
        >
          Save and replan
        </button>
        <button type="button" className="btn btn--stop" disabled={busy} onClick={dirty ? () => setDraft(hours) : onClose}>
          {dirty ? 'Revert' : 'Close'}
        </button>
      </div>
    </div>
  )
}

/**
 * What does not fit, said up front: each late task, by how much, and why -- with
 * the changes most likely to make it fit one press away.
 */
function Shortfalls({ tasks, busy, send, onHours }) {
  if (tasks.length === 0) return null
  return (
    <div className="refusal shortfalls" role="alert">
      <p className="eyebrow shortfalls__title">
        {tasks.length === 1 ? 'One task does not fit' : `${tasks.length} tasks do not fit`}
      </p>
      <ul className="shortfalls__list">
        {tasks.map((task) => {
          const short = task.shortfall ?? { unplaced: 0, past_deadline: 0 }
          const why = [
            short.unplaced > 0 &&
              `${minutes(short.unplaced)} has no free time ${task.deadline ? 'before the deadline' : 'in the next two weeks'}`,
            short.past_deadline > 0 && `${minutes(short.past_deadline)} lands after the deadline`,
          ]
            .filter(Boolean)
            .join('; ')
          return (
            <li key={task.id}>
              <span>
                <strong>{task.title}</strong>
                {task.deadline && <> — due {when(task.deadline)}</>}
                {why && <>: {why}.</>}
              </span>
              <span className="shortfalls__fixes">
                {task.deadline && (
                  <button type="button" className="chip" disabled={busy} onClick={() => send('edit', { id: task.id, changes: { deadline: laterBy(task.deadline, 1) } })}>
                    Deadline +1 day
                  </button>
                )}
                {!task.splittable && (
                  <button type="button" className="chip" disabled={busy} onClick={() => send('edit', { id: task.id, changes: { splittable: true } })}>
                    Allow splitting
                  </button>
                )}
                <button type="button" className="chip" onClick={onHours}>
                  More hours…
                </button>
              </span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

/**
 * Sessions you started by hand, and which task each was for. One the matcher was
 * sure of is linked already and only says so; the rest offer their best guesses as
 * one-tap choices. Every choice you make is remembered: the next session with the
 * same name links itself.
 */
function Inbox({ inbox, tasks, busy, send }) {
  const [open, setOpen] = useState(false)
  const [choosing, setChoosing] = useState(null)
  if (!inbox?.length) return null
  const unsorted = inbox.filter((entry) => !entry.link).length
  const auto = inbox.length - unsorted
  const link = (entry, task) => {
    setChoosing(null)
    send('link', { session: entry.session, task })
  }

  return (
    <>
      <button type="button" className="eyebrow plan-sub plan-toggle" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <span className="plan-toggle__arrow" aria-hidden="true">
          ▸
        </span>
        Sessions to sort
        <span className="plan-toggle__summary">
          {[unsorted && `${unsorted} to sort`, auto && `${auto} linked automatically`].filter(Boolean).join(' · ')}
        </span>
      </button>
      {open && (
        <ul className="inbox">
          {inbox.map((entry) => (
            <li key={entry.session} className="inbox__item">
              <span className="inbox__when dim">
                {formatDate(entry.start)} <Num>{formatTime(entry.start)}</Num>–<Num>{formatTime(entry.end)}</Num> ·{' '}
                <Num>{minutes(entry.worked_min)}</Num>
                {entry.live && ' · running'}
              </span>
              <span className={`inbox__label ${entry.label ? '' : 'dim'}`}>{entry.label || 'no name'}</span>
              <span className="inbox__choices">
                {entry.link && choosing !== entry.session ? (
                  <>
                    <span className="dim inbox__linked">
                      → <strong>{entry.link.title}</strong> (automatic)
                    </span>
                    <button type="button" className="chip" disabled={busy} onClick={() => link(entry, entry.link.task_id)}>
                      Right
                    </button>
                    <button type="button" className="chip" onClick={() => setChoosing(entry.session)}>
                      Change
                    </button>
                  </>
                ) : (
                  <>
                    {entry.suggestions.map((s) => (
                      <button
                        key={s.task_id}
                        type="button"
                        className="chip"
                        disabled={busy}
                        title={`${Math.round(s.score * 100)}% · ${s.reason}`}
                        onClick={() => link(entry, s.task_id)}
                      >
                        {s.title}
                      </button>
                    ))}
                    <select
                      className="inbox__other"
                      value=""
                      disabled={busy}
                      aria-label="Link to another task"
                      onChange={(e) => e.target.value && link(entry, Number(e.target.value))}
                    >
                      <option value="">Other task…</option>
                      {tasks.map((task) => (
                        <option key={task.id} value={task.id}>
                          {task.title}
                        </option>
                      ))}
                    </select>
                    <button type="button" className="chip" disabled={busy} onClick={() => link(entry, null)}>
                      Not a task
                    </button>
                  </>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </>
  )
}

/** The box: one line of free text, and Claude fills in the rest. */
function AddForm({ busy, send }) {
  const [text, setText] = useState('')
  return (
    <form
      className="start"
      onSubmit={async (event) => {
        event.preventDefault()
        if (text.trim() && (await send('add', { text: text.trim() }))) setText('')
      }}
    >
      <input
        className="start__task"
        type="text"
        value={text}
        onChange={(event) => setText(event.target.value)}
        placeholder="finish onboarding copy, ~3h, before Thursday, deep work"
        aria-label="Add a task"
        maxLength={500}
        autoComplete="off"
      />
      <button type="submit" className="btn btn--go" disabled={busy || !text.trim()}>
        Add
      </button>
    </form>
  )
}

/** The plan, for the owner -- the server refuses it to anyone else. */
export default function Planner({ status, axis }) {
  const { plan, refusal, busy, send } = usePlan()
  const [editingHours, setEditingHours] = useState(false)
  const [events, setEvents] = useState([])

  // The calendars' own events for the fortnight the planner looks at, refetched
  // whenever it replans -- which is also when the calendars were last read.
  const replanned = plan?.replanned
  useEffect(() => {
    let cancelled = false
    const from = midnightOf(Date.now())
    const iso = (ms) => new Date(ms).toISOString().replace('Z', '+00:00')
    getJSON(`/api/calendar?from=${encodeURIComponent(iso(from))}&to=${encodeURIComponent(iso(from + 14 * 86400000))}`)
      .then((answer) => !cancelled && setEvents(answer.events ?? []))
      .catch(() => {}) // the plan still draws without them
    return () => {
      cancelled = true
    }
  }, [replanned])
  if (!plan) return null

  const open = plan.tasks.filter((task) => task.status !== 'done')
  const done = plan.tasks.filter((task) => task.status === 'done')
  const late = open.filter((task) => task.late).length

  return (
    <section className="planner">
      <header className="history__head">
        <p className="eyebrow">Plan</p>
        <p className="dim plan-head">
          {open.length} task{open.length === 1 ? '' : 's'}
          {late > 0 && <span className="plan-task__next--late"> · {late} late</span>}
          {plan.replanned && <> · planned {formatTime(plan.replanned)}</>}
          <button type="button" className="plan-head__replan" aria-expanded={editingHours} onClick={() => setEditingHours((v) => !v)}>
            Hours
          </button>
          <button type="button" className="plan-head__replan" disabled={busy} onClick={() => send('replan')}>
            Replan
          </button>
        </p>
      </header>

      {refusal && (
        <p className="refusal" role="alert">
          {refusal}
        </p>
      )}
      {plan.calendar?.error && (
        <p className="fault" role="alert">
          Calendar — {plan.calendar.error}.{' '}
          {plan.calendar.cached
            ? `Planned around your calendar as last read, ${when(plan.calendar.cached)}.`
            : 'Planned without it.'}
        </p>
      )}

      {editingHours && (
        <HoursEditor hours={plan.hours} busy={busy} send={send} onClose={() => setEditingHours(false)} />
      )}

      <Shortfalls tasks={open.filter((task) => task.late)} busy={busy} send={send} onHours={() => setEditingHours(true)} />

      <NowLine plan={plan} status={status} busy={busy} send={send} />

      <AddForm busy={busy} send={send} />

      <Upcoming tasks={open} events={events} axis={axis} />

      <Inbox inbox={plan.inbox} tasks={plan.tasks} busy={busy} send={send} />

      {open.length === 0 ? (
        <p className="empty dim">Nothing to do. Type one line above and it gets a time.</p>
      ) : (
        <>
        <p className="eyebrow plan-sub">Tasks</p>
        <ul className="days plan-tasks">
          {open.map((task) => (
            <Task
              key={task.id}
              task={task}
              busy={busy}
              send={send}
              onClock={status?.taskId === String(task.id)}
            />
          ))}
        </ul>
        </>
      )}

      {done.length > 0 && (
        <p className="plan-done dim">
          Done this week: {done.map((task) => task.title).join(' · ')}
        </p>
      )}
    </section>
  )
}
