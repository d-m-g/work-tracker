import { useMemo } from 'react'
import History from './components/History.jsx'
import LiveSession from './components/LiveSession.jsx'
import WidgetPortrait from './components/WidgetPortrait.jsx'
import { isDemo, useDemoTracker } from './lib/demo.js'
import { axisFor } from './lib/timeline.js'
import { useTracker } from './lib/useTracker.js'

/**
 * Which of the two trackers this page load is holding.
 *
 * Read once, at module scope, and never again: the answer is the URL the browser
 * arrived at, which cannot change without a fresh load. That is what makes the
 * branch in `App` safe — it is a constant, so the tree below it is decided before
 * the first render and the two hooks are never swapped for one another.
 */
const DEMO = isDemo()

/**
 * The viewer.
 *
 * It writes now, and it does so through the same door as everything else: each
 * button is one call into the same tracker the CLI and the Shortcuts drive. There
 * is still exactly one writer of the JSON files — the browser is another caller of
 * it, not a second one. Nothing is rendered optimistically: what you see after a
 * click is the state the server read back afterwards.
 *
 * The demo is the one exception, and it is not an exception to any of that. This
 * component cannot tell the difference: it is handed `status`, `sessions` and a
 * `send`, and it draws them. Where they came from — a socket, or a fortnight
 * invented in lib/demo.js — is the caller's business and nothing it needs to know.
 * That is deliberate. A demo built as a second, simpler page would be a demo of a
 * second, simpler page; this one can only show you the instrument, because it is
 * the instrument.
 */
function Viewer({ tracker, demo = false }) {
  const { status, sessions, error, refusal, busy, send, signOut } = tracker

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
      {demo && <DemoNotice />}

      <header className="masthead">
        <div className="masthead__titles">
          <h1>Work Tracker</h1>
          <p className="dim">Start, pause and stop from here, the shortcuts, or the CLI.</p>
        </div>

        {/* Only when there is a session to end. On a loopback server with no
            password configured there is no login, so there is nothing to sign out
            of and no endpoint to ask — the server says which it is (`login`), and
            a button that would 404 is never drawn. The demo says no too: its
            fortnight was never signed into. */}
        {status?.login && (
          <button type="button" className="btn btn--stop masthead__out" onClick={signOut} disabled={busy}>
            Sign out
          </button>
        )}
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

      {/* Only in the demo, and only here: on the real page the widget is three
          inches away on your own screen, and a picture of it would be a picture of
          something you are already looking at. */}
      {demo && <WidgetPortrait status={status} />}

      <History sessions={sessions} axis={axis} send={send} />
    </main>
  )
}

/**
 * The demo says what it is, once, at the top, and then gets out of the way.
 *
 * It has to be said plainly — every number below it is invented, and a visitor who
 * mistakes this for someone's real fortnight has been misled by us, not by their
 * own carelessness. It also has to be said *briefly*: the demo's argument is the
 * instrument working, and a wall of disclaimer standing in front of it is a worse
 * lie than the one it prevents.
 */
function DemoNotice() {
  return (
    <aside className="demo-notice">
      <p className="eyebrow">Demo</p>
      <p>
        A fortnight that never happened, played out in your browser. Nothing here was recorded
        and nothing you press is saved — but the buttons all work, so press them.
      </p>
      <a className="btn btn--stop demo-notice__out" href="/">
        Sign in
      </a>
    </aside>
  )
}

/** The instrument: a viewer over the tracker on the other end of the socket. */
function Instrument() {
  return <Viewer tracker={useTracker()} />
}

/** The demo: the same viewer, over a tracker that lives in this tab. */
function Demonstration() {
  return <Viewer tracker={useDemoTracker()} demo />
}

export default function App() {
  return DEMO ? <Demonstration /> : <Instrument />
}
