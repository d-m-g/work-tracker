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
 *
 * `readOnly` is the view-only account, and it is a different element rather than
 * a disabled one. A greyed-out input is a promise that this could have been yours
 * to write if only something were otherwise — a session, a permission, a moment's
 * patience — and for a viewer none of that is true, ever. So the line is what it
 * has always looked like anyway (see `.task` in styles.css: no box, no chrome,
 * just the sentence) and simply cannot be typed into. Nothing is dangled.
 */
export default function TaskField({ value, onCommit, placeholder, id, readOnly = false }) {
  const [draft, setDraft] = useState(value ?? '')
  const [saved, setSaved] = useState(false)
  const editing = useRef(false)

  // Adopt the server's value whenever it changes underneath us — a Shortcut, the
  // CLI, another tab — but never while you are mid-sentence: pulling the text out
  // from under a cursor is not synchronisation, it is vandalism.
  useEffect(() => {
    if (!editing.current) setDraft(value ?? '')
  }, [value])

  // Below every hook, never above one: the account cannot change without a fresh
  // status from the server, but React counts hook calls per render regardless,
  // and a return that skipped some would be a bug waiting for the day this flag
  // first flips under a mounted component.
  if (readOnly) {
    return (
      <p className="task">
        <span className={`task__text ${value ? '' : 'dim'}`}>{value || 'No task named.'}</span>
      </p>
    )
  }

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
