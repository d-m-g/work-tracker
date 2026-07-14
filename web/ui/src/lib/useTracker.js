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

async function postJSON(url, body) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  })
  const payload = await response.json().catch(() => null)

  if (!response.ok) {
    // A refusal ("the session is already paused") arrives as a 409 with a
    // sentence in it. That sentence was written to be read, so it is what the
    // button shows — not "409", and not a shrug.
    throw new Error(payload?.error ?? `${response.status} ${response.statusText}`)
  }
  return payload
}

/**
 * Polls the tracker, sends it commands, and keeps the session list in step.
 *
 * The status endpoint is polled once a second; the durations are computed
 * server-side, so the browser never has to reason about clocks or timezones and
 * cannot drift away from what the CLI would report.
 *
 * The (much heavier) session list is *not* polled. It is fetched once, then
 * refetched only when a session id disappears — that is precisely the moment a
 * `stop` happened and a new archive appeared. Polling it every second would
 * re-read every file on disk to redraw a list that changes a few times a day.
 *
 * Nothing here is optimistic. A command's response carries the status the server
 * read back *after* performing it, and that is what gets rendered — so a click
 * that raced a Shortcut, or a widget, shows what actually happened rather than
 * what the click assumed would happen.
 */
export function useTracker() {
  const [status, setStatus] = useState(null)
  const [sessions, setSessions] = useState(null)
  const [error, setError] = useState(null)
  const [refusal, setRefusal] = useState(null)
  const [busy, setBusy] = useState(false)

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
        // it just wrote is what we need to pull in. It fires for a stop from
        // anywhere — a Shortcut, ⌘F8, the widget — not only for our own button.
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

  /**
   * Send one write command, and adopt the state it reports back.
   *
   * Returns true if it went through. A refusal is not an exception the caller has
   * to handle — it is an answer, and it is held in `refusal` until the next
   * command supersedes it.
   */
  const send = useCallback(
    async (command, body) => {
      setBusy(true)
      setRefusal(null)
      try {
        const result = await postJSON(`/api/${command}`, body)
        setStatus(result.status)
        previousId.current = result.status.id
        // A stop archives a day; a task edit can rewrite one that is already
        // archived. Either way the history on screen is now out of date.
        if (result.session) loadSessions()
        return true
      } catch (err) {
        setRefusal(err.message)
        return false
      } finally {
        setBusy(false)
      }
    },
    [loadSessions],
  )

  return { status, sessions, error, refusal, busy, send, reload: loadSessions }
}
