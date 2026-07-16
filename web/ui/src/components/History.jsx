import { useState } from 'react'
import { formatCompact, formatDate, formatDuration, formatTime } from '../lib/format.js'
import Num from './Num.jsx'
import Strip, { Ruler } from './Strip.jsx'
import TaskField from './TaskField.jsx'

/**
 * One day. Click it to read what it was made of and to say what it was spent on.
 *
 * The row is a day, not a session: six starts and stops between breakfast and bed
 * are one day and one strip. What you go here to read is how you spent Tuesday —
 * how many times you happened to press the button is bookkeeping, and it belongs
 * inside the row, not as the row.
 *
 * Every row opens, including one with no breaks in it — an unbroken day is exactly
 * the kind you might want to name, and a row you cannot open is a row you cannot
 * correct.
 */
function Day({ day, axis, index, send }) {
  const [open, setOpen] = useState(false)

  return (
    <li className="day" style={{ '--row': index }}>
      <button
        type="button"
        className="day__row"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <span className="day__date">
          {formatDate(day.at)}
          {/* What the day was spent on, in the words you gave it. Every distinct
              task on the day, because a day is often more than one thing — and
              dimmed to "no task" when it was never named, which says so rather
              than leaving a gap you have to interpret. */}
          <span className={`day__task ${tasksOf(day) ? '' : 'dim'}`} title={tasksOf(day) ?? ''}>
            {tasksOf(day) || 'no task'}
          </span>
        </span>
        <span className="day__worked">
          <Num>{formatDuration(day.workedSeconds)}</Num>
        </span>
        <span className="day__strip">
          <Strip segments={day.segments} axis={axis} label={describe(day)} />
        </span>
        <span className="day__breaks dim">
          {day.breaks === 0 ? 'unbroken' : `${day.breaks} break${day.breaks === 1 ? '' : 's'}`}
        </span>
      </button>

      {open && (
        <div className="day__open">
          <ol className="parts">
            {day.parts.map((part) => (
              <li className="part" key={`${part.id}-${part.from}`}>
                <span className="part__when dim">
                  <Num>{formatTime(new Date(part.from).toISOString())}</Num> –{' '}
                  <Num>{formatTime(new Date(part.to).toISOString())}</Num>
                  {/* A session that was already running at midnight, or that ran
                      on past the next one, is showing a slice of itself here. Say
                      which, rather than let 00:00 read as a morning that began
                      sharp on the hour. */}
                  {part.startsEarlier && <span className="part__spill"> from the night before</span>}
                  {part.endsLater && <span className="part__spill"> on into the next day</span>}
                </span>
                <span className="part__worked">
                  <Num>{formatDuration(part.workedSeconds)}</Num>
                </span>
                {/* The task belongs to the whole session, not to the slice of it
                    that landed on this day — so editing it from either side of a
                    midnight edits the one session, which is the truth. */}
                <TaskField
                  value={part.task}
                  onCommit={(task) => send('task', { id: part.id, task })}
                  placeholder="What was this spent on?"
                  id={`task-${part.id}`}
                />
              </li>
            ))}
          </ol>

          {day.breaks > 0 && (
            <ol className="breaks">
              {day.segments
                .filter((segment) => segment.kind === 'pause')
                .map((pause) => (
                  <li key={pause.at}>
                    <span className="dim">
                      <Num>{formatTime(new Date(pause.at).toISOString())}</Num> –{' '}
                      <Num>{formatTime(new Date(pause.until).toISOString())}</Num>
                    </span>
                    <span>
                      <Num>{formatDuration(pause.seconds)}</Num>
                    </span>
                  </li>
                ))}
            </ol>
          )}
        </div>
      )}
    </li>
  )
}

/** Every distinct task the day was given, in the order they were worked. */
function tasksOf(day) {
  const tasks = []
  for (const part of day.parts) {
    if (part.task && !tasks.includes(part.task)) tasks.push(part.task)
  }
  return tasks.join(' · ')
}

/** What a screen reader hears instead of the day's picture. */
function describe(day) {
  const count = day.parts.length
  return `Timeline: ${formatDuration(day.workedSeconds)} worked across ${count} ${
    count === 1 ? 'session' : 'sessions'
  }, ${day.breaks} ${day.breaks === 1 ? 'break' : 'breaks'}.`
}

export default function History({ days, totals, unreadable, axis, send }) {
  if (!days) return null

  return (
    <section className="history">
      <header className="history__head">
        <p className="eyebrow">Previous days</p>
        {days.length > 0 && (
          <p className="dim">
            {days.length} day{days.length === 1 ? '' : 's'} ·{' '}
            {/* The server's total, not a sum of the rows: splitting a session at
                midnight moves seconds between days but cannot create or lose one,
                so this is the same number either way — and this is the one the CLI
                would print. */}
            {formatCompact(totals.workedSeconds)} worked
          </p>
        )}
      </header>

      {/* A file that will not parse is named, never quietly dropped: a missing
          day is exactly the thing you want to be told about. */}
      {unreadable.length > 0 && (
        <ul className="faults">
          {unreadable.map((fault) => (
            <li key={fault.file}>
              <strong>{fault.file}</strong> will not parse — {fault.error}
            </li>
          ))}
        </ul>
      )}

      {days.length === 0 ? (
        <p className="empty dim">Stop a session and it lands here.</p>
      ) : (
        <>
          <div className="history__ruler">
            <Ruler axis={axis} />
          </div>
          <ul className="days">
            {days.map((day, index) => (
              <Day key={day.id} day={day} axis={axis} index={index} send={send} />
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
