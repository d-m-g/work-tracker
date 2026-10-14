import { useMemo } from 'react'
import { formatCompact } from '../lib/format.js'
import { midnightOf, spanOf } from '../lib/timeline.js'
import Num from './Num.jsx'

/** Monday's midnight of the week `at` falls in — the key a week groups under. */
function weekOf(at) {
  const day = new Date(at)
  return new Date(
    day.getFullYear(),
    day.getMonth(),
    day.getDate() - ((day.getDay() + 6) % 7),
  ).getTime()
}

/** The first midnight of the month `at` falls in. */
function monthOf(at) {
  const day = new Date(at)
  return new Date(day.getFullYear(), day.getMonth(), 1).getTime()
}

/** `27 Jul – 2 Aug`: the Monday and the Sunday of one week. */
function weekLabel(at) {
  const start = new Date(at)
  const end = new Date(start.getFullYear(), start.getMonth(), start.getDate() + 6)
  const opts = { day: 'numeric', month: 'short' }
  return `${start.toLocaleDateString([], opts)} – ${end.toLocaleDateString([], opts)}`
}

/** `July 2026`. */
function monthLabel(at) {
  return new Date(at).toLocaleDateString([], { month: 'long', year: 'numeric' })
}

/** Fold day totals into periods, newest first. */
function group(dayTotals, keyOf) {
  const periods = new Map()
  for (const day of dayTotals) {
    const key = keyOf(day.at)
    const period = periods.get(key) ?? { at: key, workedSeconds: 0, days: 0 }
    period.workedSeconds += day.workedSeconds
    period.days += 1
    periods.set(key, period)
  }
  return [...periods.values()].sort((a, b) => b.at - a.at)
}

function Periods({ title, rows, label }) {
  return (
    <div className="report">
      <p className="eyebrow report__title">{title}</p>
      <ol className="report__rows">
        {rows.map((row) => (
          <li key={row.at}>
            <span className="report__label">{label(row.at)}</span>
            <span className="report__worked">
              <Num>{formatCompact(row.workedSeconds)}</Num>
            </span>
            <span className="report__days dim">
              {row.days} day{row.days === 1 ? '' : 's'}
            </span>
          </li>
        ))}
      </ol>
    </div>
  )
}

/**
 * Weekly and monthly totals, side by side.
 *
 * Computed here from the same days the history draws — no second source of
 * truth, and the same midnight cuts: a session that crossed midnight counts its
 * hours on the days they were worked, so a week's figure is the sum of its
 * days as the rows below show them. The live session's hours count too, on the
 * day (and so the week, and the month) they are landing in right now.
 */
export default function Reports({ days, live, today }) {
  const dayTotals = useMemo(() => {
    const totals = new Map()
    for (const day of days ?? []) {
      totals.set(day.at, (totals.get(day.at) ?? 0) + day.workedSeconds)
    }
    // The live session's today: not in the history yet, but already worked.
    if (live && today) {
      const base = midnightOf(spanOf(live).end)
      totals.set(base, (totals.get(base) ?? 0) + today.workedSeconds)
    }
    return (
      [...totals.entries()]
        .map(([at, workedSeconds]) => ({ at, workedSeconds }))
        // A day a pause spilled into but no work did (0:00:00 in the history)
        // is not a day worked, and must not pad the period's day count.
        .filter((day) => day.workedSeconds > 0)
    )
  }, [days, live, today])

  const weeks = useMemo(() => group(dayTotals, weekOf), [dayTotals])
  const months = useMemo(() => group(dayTotals, monthOf), [dayTotals])

  if (dayTotals.length === 0) return null

  return (
    <section className="reports">
      <header className="history__head">
        <p className="eyebrow">Reports</p>
      </header>
      <div className="reports__cols">
        <Periods title="Weeks" rows={weeks} label={weekLabel} />
        <Periods title="Months" rows={months} label={monthLabel} />
      </div>
    </section>
  )
}
