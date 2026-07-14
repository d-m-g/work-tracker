import { useMemo } from 'react'
import History from './components/History.jsx'
import LiveSession from './components/LiveSession.jsx'
import { axisFor } from './lib/timeline.js'
import { useTracker } from './lib/useTracker.js'

/**
 * The viewer.
 *
 * It writes now, and it does so through the same door as everything else: each
 * button is one call into the same tracker the CLI and the Shortcuts drive. There
 * is still exactly one writer of the JSON files — the browser is another caller of
 * it, not a second one. Nothing is rendered optimistically: what you see after a
 * click is the state the server read back afterwards.
 */
export default function App() {
  const { status, sessions, error, refusal, busy, send } = useTracker()

  // One axis for every strip on the page, live and archived alike. Sharing it is
  // what makes the days comparable: a late start *looks* late.
  const axis = useMemo(() => {
    const spans = []
    if (status && status.state !== 'idle') spans.push(status)
    if (sessions) spans.push(...sessions.sessions)
    return axisFor(spans)
  }, [status, sessions])

  return (
    <main className="page">
      <header className="masthead">
        <h1>Work Tracker</h1>
        <p className="dim">Start, pause and stop from here, the shortcuts, or the CLI.</p>
      </header>

      {/* Two different things, and they are never conflated. The tracker being
          unreachable is a fault. The tracker saying no is an answer. */}
      {error && (
        <p className="fault" role="alert">
          Can’t reach the tracker — {error}. Is <code>python3 web/server.py</code> still
          running?
        </p>
      )}

      {refusal && (
        <p className="refusal" role="alert">
          {refusal}
        </p>
      )}

      <LiveSession status={status} axis={axis} busy={busy} send={send} />
      <History sessions={sessions} axis={axis} send={send} />
    </main>
  )
}
