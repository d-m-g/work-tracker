import { useState } from 'react'
import { formatCompact, formatDate, formatDuration, formatTime } from '../lib/format.js'
import Num from './Num.jsx'
import Strip, { Ruler } from './Strip.jsx'
import TaskField from './TaskField.jsx'

/**
 * One archived day. Click it to read its pauses and to say what it was spent on.
 *
 * Every row opens, including one with no breaks in it — an unbroken day is
 * exactly the kind you might want to name, and a row you cannot open is a row you
 * cannot correct.
 */
function Day({ session, axis, index, send }) {
  const [open, setOpen] = useState(false)
  const breaks = session.pauses.length

  return (
    <li className="day" style={{ '--row': index }}>
      <button
        type="button"
        className="day__row"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <span className="day__date">
          {formatDate(session.start)}
          {/* The task rides under the date, quietly, and is dimmed when absent —
              an unlabelled day says so rather than leaving a gap you have to
              interpret. A long one is clipped here and readable in full on hover,
              or by opening the row. */}
          <span className={`day__task ${session.task ? '' : 'dim'}`} title={session.task ?? ''}>
            {session.task || 'no task'}
          </span>
        </span>
        <span className="day__worked">
          <Num>{formatDuration(session.workedSeconds)}</Num>
        </span>
        <span className="day__strip">
          <Strip session={session} axis={axis} />
        </span>
        <span className="day__breaks dim">
          {breaks === 0 ? 'unbroken' : `${breaks} break${breaks === 1 ? '' : 's'}`}
        </span>
      </button>

      {open && (
        <div className="day__open">
          <TaskField
            value={session.task}
            onCommit={(task) => send('task', { id: session.id, task })}
            placeholder="What was this day spent on?"
            id={`task-${session.id}`}
          />

          {breaks > 0 && (
            <ol className="breaks">
              {session.pauses.map((pause, position) => (
                <li key={`${pause.start}-${position}`}>
                  <span className="dim">
                    <Num>{formatTime(pause.start)}</Num> – <Num>{formatTime(pause.end)}</Num>
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

export default function History({ sessions, axis, send }) {
  if (!sessions) return null

  const { totals, unreadable } = sessions

  return (
    <section className="history">
      <header className="history__head">
        <p className="eyebrow">Previous sessions</p>
        {totals.count > 0 && (
          <p className="dim">
            {totals.count} session{totals.count === 1 ? '' : 's'} ·{' '}
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

      {sessions.sessions.length === 0 ? (
        <p className="empty dim">Stop a session and it lands here.</p>
      ) : (
        <>
          <div className="history__ruler">
            <Ruler axis={axis} />
          </div>
          <ul className="days">
            {sessions.sessions.map((session, index) => (
              <Day key={session.id} session={session} axis={axis} index={index} send={send} />
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
