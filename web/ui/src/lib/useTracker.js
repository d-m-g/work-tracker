import { useCallback, useEffect, useRef, useState } from 'react'

/** How often the live session is polled, in milliseconds. */
const STATUS_INTERVAL = 1000

async function getJSON(url) {
  const response = await fetch(url, { cache: 'no-store' })
  const payload = await response.json().catch(() => null)

  if (!response.ok) {
    // The API reports a corrupt current.json as a JSON body, not a bare 500 —
    // surface that message rather than a generic "request failed".
    throw new Error(payload?.error ?? `${response.status} ${response.statusText}`)
  }
  return payload
}

/**
 * Polls the tracker and keeps the session list in step with it.
 *
 * The status endpoint is polled once a second; the durations are computed
 * server-side, so the browser never has to reason about clocks or timezones and
 * cannot drift away from what the CLI would report.
 *
 * The (much heavier) session list is *not* polled. It is fetched once, then
 * refetched only when a session id disappears — that is precisely the moment a
 * `stop` happened and a new archive appeared. Polling it every second would
 * re-read every file on disk to redraw a list that changes a few times a day.
 */
export function useTracker() {
  const [status, setStatus] = useState(null)
  const [sessions, setSessions] = useState(null)
  const [error, setError] = useState(null)

  const previousId = useRef(null)

  const loadSessions = useCallback(async () => {
    try {
      setSessions(await getJSON('/api/sessions'))
    } catch (err) {
      setError(err.message)
    }
  }, [])

  useEffect(() => {
    let cancelled = false

    async function poll() {
      try {
        const next = await getJSON('/api/status')
        if (cancelled) return

        setStatus(next)
        setError(null)

        // A session that was there and is now gone means `stop` ran: the archive
        // it just wrote is what we need to pull in.
        if (previousId.current && previousId.current !== next.id) {
          loadSessions()
        }
        previousId.current = next.id
      } catch (err) {
        if (!cancelled) setError(err.message)
      }
    }

    poll()
    loadSessions()

    const timer = setInterval(poll, STATUS_INTERVAL)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [loadSessions])

  return { status, sessions, error, reload: loadSessions }
}
