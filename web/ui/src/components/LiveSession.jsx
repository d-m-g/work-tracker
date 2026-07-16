import { formatDuration, formatTime } from '../lib/format.js'
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

/**
 * Today: what the tracker has to show for the day you are in.
 *
 * `today` is the live session cut to this day (lib/timeline.js) — nearly always
 * the whole of it, and after a midnight only this side. Every number on the card
 * comes from there rather than from `status`, so the card counts the same hours
 * its strip draws, and the same hours the history will show once the session is
 * archived. `status` is still what the card is *about*: the state, the task, the
 * buttons, and the moment it began, which is worth saying even when it was
 * yesterday.
 */
export default function LiveSession({ status, today, axis, busy, send, readOnly = false }) {
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

      <Clock seconds={today.workedSeconds} held={held} />

      {/* The caption's first word is what the clock above it means, so it says
          "today" exactly when that is not the same as "this session" — after a
          midnight, when the digits hold this side of it and the session holds
          both. The rest says where the session actually began, which on such a
          night is the thing the clock cannot tell you: 0:00:00 worked and held
          since 23:40 is a sentence about two days, and it should read like one. */}
      <p className="live__caption">
        {today.startsEarlier ? 'worked today' : 'worked'} —{' '}
        {held ? (
          <>holding since {formatTime(status.pauseStart)}, so this is not moving</>
        ) : today.startsEarlier ? (
          <>running since {formatTime(status.start)}, the night before</>
        ) : (
          <>started {formatTime(status.start)}</>
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
        {/* Today's hours on today's ruler, the same one every row below is drawn
            against — so the day you are in the middle of can be compared with the
            days behind it, which is the entire reason the axis is shared. A
            session that ran past midnight is cut at it here exactly as the history
            cuts it (see lib/timeline.js), so this strip is already the one the
            archive will draw when you press Stop. */}
        <Strip segments={today.segments} axis={axis} edge={today.edge} held={held} />
      </div>

      <dl className="figures">
        <div>
          <dt>Paused</dt>
          <dd>
            <Num>{formatDuration(today.pausedSeconds)}</Num>
          </dd>
        </div>
        <div>
          <dt>Breaks</dt>
          <dd>
            <Num>{today.pauseCount}</Num>
            {/* Prose, not a measurement, so it keeps its natural figures. */}
            {status.pauseInProgress && <span className="dim"> +1 open</span>}
          </dd>
        </div>
        <div>
          <dt>On the clock</dt>
          <dd>
            <Num>{formatDuration(today.grossSeconds)}</Num>
          </dd>
        </div>
      </dl>
    </section>
  )
}
