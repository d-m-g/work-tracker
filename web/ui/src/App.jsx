import { useMemo } from 'react'
import History from './components/History.jsx'
import LiveSession from './components/LiveSession.jsx'
import { axisFor } from './lib/timeline.js'
import { useTracker } from './lib/useTracker.js'

/**
 * The viewer.
 *
 * Read-only by design: no start/stop buttons. The CLI and the Shortcuts remain
 * the single writer of the JSON files, so the browser can never race a Shortcut
 * and corrupt a session. This is a window onto the data, not a second way in.
 */
export default function App() {
  const { status, sessions, error } = useTracker()

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
        <p className="dim">Read-only. Start, pause and stop from the CLI or the shortcuts.</p>
      </header>

      {error && (
        <p className="fault" role="alert">
          Can’t reach the tracker — {error}. Is <code>python3 web/server.py</code> still
          running?
        </p>
      )}

      <LiveSession status={status} axis={axis} />
      <History sessions={sessions} axis={axis} />
    </main>
  )
}
