import { formatDuration, formatTime } from '../lib/format.js'
import Controls, { StartForm } from './Controls.jsx'
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
      {text.slice(0, cut)}
      <span className="clock__seconds">{text.slice(cut)}</span>
    </p>
  )
}

export default function LiveSession({ status, axis, busy, send }) {
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
        <StartForm busy={busy} send={send} />
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
      />

      <Controls status={status} busy={busy} send={send} />

      <div className="live__timeline">
        <Ruler axis={axis} />
        <Strip session={status} axis={axis} live />
      </div>

      <dl className="figures">
        <div>
          <dt>Paused</dt>
          <dd>{formatDuration(status.pausedSeconds)}</dd>
        </div>
        <div>
          <dt>Breaks</dt>
          <dd>
            {status.pauseCount}
            {status.pauseInProgress && <span className="dim"> +1 open</span>}
          </dd>
        </div>
        <div>
          <dt>On the clock</dt>
          <dd>{formatDuration(status.grossSeconds)}</dd>
        </div>
      </dl>
    </section>
  )
}
