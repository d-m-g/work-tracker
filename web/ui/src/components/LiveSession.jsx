import { formatDuration, formatTime } from '../lib/format.js'
import { baseOf, minutesOf, segmentsOf, spanOf } from '../lib/timeline.js'
import Controls, { StartForm } from './Controls.jsx'
import Num from './Num.jsx'
import Strip, { Ruler } from './Strip.jsx'
import TaskField from './TaskField.jsx'

/**
 * The hero: what the day has come to so far.
 *
 * The clock splits `H:MM` from `:SS` and dims the seconds. The minutes are the
 * number you actually read; the seconds only prove the thing is alive, and at
 * full weight they nag.
 */
function Clock({ seconds, held }) {
  const text = formatDuration(seconds)
  const cut = text.lastIndexOf(':')

  return (
    <p className={`clock ${held ? 'clock--held' : ''}`}>
      <Num>{text.slice(0, cut)}</Num>
      <span className="clock__seconds">
        <Num>{text.slice(cut)}</Num>
      </span>
    </p>
  )
}

export default function LiveSession({ status, axis, busy, send, readOnly = false }) {
  if (!status) {
    return (
      <section className="live">
        <p className="eyebrow">Today</p>
        <Clock seconds={0} held />
      </section>
    )
  }

  if (status.state === 'idle') {
    return (
      <section className="live live--idle">
        <p className="eyebrow">Today</p>
        <Clock seconds={0} held />
        <p className="live__caption">Nothing running.</p>
        {/* A read-only account is not offered the box, because starting a session
            is not theirs to do. An idle tracker is still worth showing them: that
            nothing is running is an answer to the question they came with. */}
        {!readOnly && <StartForm busy={busy} send={send} />}
      </section>
    )
  }

  const held = status.state === 'paused'

  return (
    <section className={`live ${held ? 'live--held' : 'live--running'}`}>
      <header className="live__head">
        <p className="eyebrow">Today</p>
        <p className={`state ${held ? 'state--held' : 'state--running'}`}>
          <span className="state__dot" />
          {held ? 'Paused' : 'Running'}
        </p>
      </header>

      <Clock seconds={status.workedSeconds} held={held} />

      <p className="live__caption">
        {held ? (
          <>worked — holding since {formatTime(status.pauseStart)}, so this is not moving</>
        ) : (
          <>worked — started {formatTime(status.start)}</>
        )}
      </p>

      <TaskField
        value={status.task}
        onCommit={(task) => send('task', { task })}
        placeholder="What are you working on?"
        id="live-task"
        readOnly={readOnly}
      />

      {!readOnly && <Controls status={status} busy={busy} send={send} />}

      <div className="live__timeline">
        <Ruler axis={axis} />
        {/* The live session is drawn whole, on the day it began — one session you
            are in the middle of, not two halves of two days. If it has run past
            midnight the axis stretches for it (see lib/timeline.js), which is the
            only time this page's ruler reads past 24:00. The history, being days
            rather than sessions, is cut at midnight instead. */}
        <Strip
          segments={segmentsOf(status)}
          axis={axis}
          edge={minutesOf(spanOf(status).end, baseOf(status))}
          held={held}
        />
      </div>

      <dl className="figures">
        <div>
          <dt>Paused</dt>
          <dd>
            <Num>{formatDuration(status.pausedSeconds)}</Num>
          </dd>
        </div>
        <div>
          <dt>Breaks</dt>
          <dd>
            <Num>{status.pauseCount}</Num>
            {/* Prose, not a measurement, so it keeps its natural figures. */}
            {status.pauseInProgress && <span className="dim"> +1 open</span>}
          </dd>
        </div>
        <div>
          <dt>On the clock</dt>
          <dd>
            <Num>{formatDuration(status.grossSeconds)}</Num>
          </dd>
        </div>
      </dl>
    </section>
  )
}
