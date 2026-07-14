import { useState } from 'react'
import { formatCompact, formatDate, formatDuration, formatTime } from '../lib/format.js'
import Strip, { Ruler } from './Strip.jsx'

/** One archived day. Click it to read its pauses. */
function Day({ session, axis, index }) {
  const [open, setOpen] = useState(false)
  const breaks = session.pauses.length

  return (
    <li className="day" style={{ '--row': index }}>
      <button
        type="button"
        className="day__row"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        disabled={breaks === 0}
      >
        <span className="day__date">{formatDate(session.start)}</span>
        <span className="day__worked">{formatDuration(session.workedSeconds)}</span>
        <span className="day__strip">
          <Strip session={session} axis={axis} />
        </span>
        <span className="day__breaks dim">
          {breaks === 0 ? 'unbroken' : `${breaks} break${breaks === 1 ? '' : 's'}`}
        </span>
      </button>

      {open && breaks > 0 && (
        <ol className="breaks">
          {session.pauses.map((pause, position) => (
            <li key={`${pause.start}-${position}`}>
              <span className="dim">
                {formatTime(pause.start)} – {formatTime(pause.end)}
              </span>
              <span>{formatDuration(pause.seconds)}</span>
            </li>
          ))}
        </ol>
      )}
    </li>
  )
}

export default function History({ sessions, axis }) {
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
              <Day key={session.id} session={session} axis={axis} index={index} />
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
