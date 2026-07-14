import { useState } from 'react'

/**
 * The buttons.
 *
 * Each one sends the *precise* command it is labelled with — `pause`, never
 * `toggle`. The distinction only shows itself in a race, and then it matters: if
 * a Shortcut paused the session in the second before you clicked **Pause**, a
 * `toggle` would helpfully *resume* it, which is the exact opposite of what the
 * button said it would do. `pause` instead refuses, the refusal is shown, and the
 * state on screen was true all along. A key bound to ⌘F8 has no label to keep
 * faith with and can toggle; a button does, and cannot.
 *
 * Stop stands apart, and is never reachable by the play/pause key: ending a day
 * stays a deliberate act.
 */
export default function Controls({ status, busy, send }) {
  const held = status.state === 'paused'

  return (
    <div className="controls">
      <button
        type="button"
        className="btn btn--go"
        onClick={() => send(held ? 'resume' : 'pause')}
        disabled={busy}
      >
        {held ? 'Resume' : 'Pause'}
      </button>

      <button
        type="button"
        className="btn btn--stop"
        onClick={() => send('stop')}
        disabled={busy}
      >
        Stop
      </button>
    </div>
  )
}

/**
 * What an idle tracker offers instead: a box to say what you are about to do, and
 * the button that begins.
 *
 * The task is asked for *here*, at the one moment you actually know the answer —
 * the same question the Work Start shortcut asks. It is never required: Start with
 * an empty box begins an unlabelled session, and you can write the label later, or
 * after the day is already in the archive.
 */
export function StartForm({ busy, send }) {
  const [task, setTask] = useState('')

  const start = async () => {
    const ok = await send('start', { task: task.trim() || null })
    if (ok) setTask('')
  }

  return (
    <form
      className="start"
      onSubmit={(event) => {
        event.preventDefault()
        start()
      }}
    >
      <input
        className="start__task"
        type="text"
        value={task}
        onChange={(event) => setTask(event.target.value)}
        placeholder="What are you working on?"
        aria-label="What are you working on?"
        maxLength={200}
        autoComplete="off"
      />
      <button type="submit" className="btn btn--go" disabled={busy}>
        Start
      </button>
    </form>
  )
}
