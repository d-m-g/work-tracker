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
 * The session on the clock, and where it sits in the day.
 *
 * The card counts the *session* — every figure on it comes from `status`, which
 * is the server's own arithmetic over the whole of it, across whatever midnights
 * it has seen. That is what the clock is for: "how long have I been at this" does
 * not become 0:00:00 because a date turned over while you were working.
 *
 * The strip is the one thing here that is about the *day*, and it has to be: it
 * is drawn against the ruler every history row shares, and that ruler is a day.
 * So `today` (lib/timeline.js) is the session cut to the day it is now in —
 * nearly always the whole of it, and after a midnight only this side, the rest
 * having gone to the history to be drawn on the night it was worked.
 *
 * Which leaves the clock and the strip measuring two different things on the same
 * card, and the caption is where that is said out loud rather than left to be
 * noticed: after a midnight it reads "worked this session", so the digits are not
 * mistaken for the day the strip beneath them is drawing.
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

      <Clock seconds={status.workedSeconds} held={held} />

      {/* The caption says which of the two this card is measuring, and it only
          has to once a session has crossed a midnight — before that the session
          *is* today and the distinction has nothing to mark. After it, the digits
          are the session's and the strip below is the day's, so the first words
          say "this session" and the last say where it began. A reader who takes
          1:36:19 for a figure about today would be reading it wrong, and would be
          right to: nothing else on the card says otherwise. */}
      <p className="live__caption">
        {today.startsEarlier ? 'worked this session' : 'worked'} —{' '}
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

      {/* The session's, like the clock — every one of these is a figure the server
          computed over the whole of it. They have to keep the clock's company and
          not the strip's: paused and worked sum to "on the clock", and three
          numbers that only add up when two are the session's and one is the day's
          would be a sum that lies. */}
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
