import { useEffect, useRef, useState } from 'react'

/**
 * One editable line: what a session is being spent on.
 *
 * The same component serves the live session and any day in the archive, because
 * it is the same act — writing down what you were doing. Doing it late is not a
 * lesser version of doing it on time; it is the normal case, and the archive
 * accepts it. Only the label is ever rewritten: every number in that file still
 * comes from what the clock actually recorded.
 *
 * It commits on Enter or on blur, reverts on Escape, and — if the server refuses
 * the edit — puts back what is true rather than leaving your rejected text on
 * screen looking saved.
 */
export default function TaskField({ value, onCommit, placeholder, id }) {
  const [draft, setDraft] = useState(value ?? '')
  const [saved, setSaved] = useState(false)
  const editing = useRef(false)

  // Adopt the server's value whenever it changes underneath us — a Shortcut, the
  // CLI, another tab — but never while you are mid-sentence: pulling the text out
  // from under a cursor is not synchronisation, it is vandalism.
  useEffect(() => {
    if (!editing.current) setDraft(value ?? '')
  }, [value])

  const commit = async () => {
    editing.current = false
    const next = draft.trim()

    if (next === (value ?? '')) return
    const ok = await onCommit(next || null)

    if (ok) {
      setSaved(true)
      window.setTimeout(() => setSaved(false), 1400)
    } else {
      setDraft(value ?? '') // refused: what is on screen must be what is on disk
    }
  }

  return (
    <p className="task">
      <input
        id={id}
        className="task__input"
        type="text"
        value={draft}
        onChange={(event) => {
          editing.current = true
          setDraft(event.target.value)
        }}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === 'Enter') {
            event.preventDefault()
            event.currentTarget.blur() // blur commits; one path, not two
          } else if (event.key === 'Escape') {
            editing.current = false
            setDraft(value ?? '')
            event.currentTarget.blur()
          }
        }}
        placeholder={placeholder}
        aria-label={placeholder}
        maxLength={200}
        autoComplete="off"
      />
      <span className={`task__saved ${saved ? 'task__saved--on' : ''}`} aria-live="polite">
        {saved ? 'saved' : ''}
      </span>
    </p>
  )
}
