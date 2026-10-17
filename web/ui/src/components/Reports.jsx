import { useEffect, useMemo, useRef, useState } from 'react'
import { formatCompact, formatDate } from '../lib/format.js'
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

/** `2026-07`: the key `planner.py summarize` stores a month's summary under. */
function monthKey(at) {
  const day = new Date(at)
  return `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, '0')}`
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

function Periods({ title, rows, label, onReport, picked }) {
  return (
    <div className="report">
      <p className="eyebrow report__title">{title}</p>
      <ol className={`report__rows ${onReport ? 'report__rows--action' : ''}`}>
        {rows.map((row) => (
          <li key={row.at}>
            <span className="report__label">{label(row.at)}</span>
            {onReport && (
              <button
                type="button"
                className="report__make"
                aria-pressed={picked === row.at}
                aria-label={`${PROJECT} report for ${label(row.at)}`}
                onClick={() => onReport(picked === row.at ? null : row.at)}
              >
                Report
              </button>
            )}
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

/** The client's project. Unassigned sessions count as theirs, as in web/api.py. */
const PROJECT = 'Pecto'
const isProject = (session) =>
  session.project == null || String(session.project).toLowerCase() === PROJECT.toLowerCase()

/** `12.50 h`: decimal hours, the unit an invoice is written in. */
const hours = (seconds) => `${(seconds / 3600).toFixed(2)} h`

/**
 * One month of Pecto's hours, day by day: what was tracked, what counts after
 * halving, and the difference. Archived days only — the session running now
 * joins once it is stopped.
 */
function MonthReport({ days, month, summary: done, onClose }) {
  // It opens below both lists, and the weeks run far longer than the months:
  // without this the button appears to do nothing.
  const panel = useRef(null)
  useEffect(() => {
    panel.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [month])

  const rows = (days ?? [])
    .filter((day) => monthOf(day.at) === month)
    .map((day) => {
      const tracked = day.parts
        .filter((part) => isProject(part.session))
        .reduce((sum, part) => sum + part.workedSeconds, 0)
      return { at: day.at, tracked, halved: !!day.halved, counted: day.halved ? Math.round(tracked / 2) : tracked }
    })
    .filter((row) => row.tracked > 0)
    .sort((a, b) => a.at - b.at)

  const sum = (list, key) => list.reduce((total, row) => total + row[key], 0)
  const halvedRows = rows.filter((row) => row.halved)
  const tracked = sum(rows, 'tracked')
  const counted = sum(rows, 'counted')
  const halvedTracked = sum(halvedRows, 'tracked')
  const halvedCounted = sum(halvedRows, 'counted')

  const summary = [
    ['Days worked', String(rows.length)],
    ['Hours tracked', hours(tracked)],
    [`Halved days (${halvedRows.length})`, `${hours(halvedTracked)} → ${hours(halvedCounted)}`],
    ['Hours removed by halving', hours(tracked - counted)],
    ['Hours counted', hours(counted)],
  ]

  const title = `${PROJECT} — ${monthLabel(month)}`
  const text = [
    title,
    '',
    ...summary.map(([name, value]) => `${name}: ${value}`),
    '',
    ...(done ? [done, ''] : []),
    ...rows.map(
      (row) =>
        `${formatDate(row.at)}\t${hours(row.tracked)}\t${hours(row.counted)}${row.halved ? '\t(halved)' : ''}`,
    ),
  ].join('\n')

  return (
    <div className="month-report" ref={panel}>
      <header className="month-report__head">
        <p className="eyebrow">{title}</p>
        <span>
          <button type="button" className="btn" onClick={() => navigator.clipboard?.writeText(text)}>
            Copy
          </button>{' '}
          <button type="button" className="btn" onClick={onClose}>
            Close
          </button>
        </span>
      </header>

      {rows.length === 0 ? (
        <p className="dim">No {PROJECT} hours this month.</p>
      ) : (
        <>
          <dl className="month-report__summary">
            {summary.map(([name, value]) => (
              <div key={name}>
                <dt className="dim">{name}</dt>
                <dd>
                  <Num>{value}</Num>
                </dd>
              </div>
            ))}
          </dl>
          {done && <p className="month-report__done">{done}</p>}
          <table className="month-report__days">
            <thead>
              <tr>
                <th>Day</th>
                <th>Tracked</th>
                <th>Counted</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.at}>
                  <td>{formatDate(row.at)}</td>
                  <td>
                    <Num>{hours(row.tracked)}</Num>
                  </td>
                  <td>
                    <Num>{hours(row.counted)}</Num>
                  </td>
                  <td className="dim">{row.halved ? '½' : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
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
export default function Reports({ days, live, today, summaries }) {
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
  const [picked, setPicked] = useState(null)

  if (dayTotals.length === 0) return null

  return (
    <section className="reports">
      <header className="history__head">
        <p className="eyebrow">Reports</p>
      </header>
      <div className="reports__cols">
        <Periods title="Weeks" rows={weeks} label={weekLabel} />
        <Periods title="Months" rows={months} label={monthLabel} onReport={setPicked} picked={picked} />
      </div>
      {picked !== null && (
        <MonthReport days={days} month={picked} summary={summaries?.[monthKey(picked)]} onClose={() => setPicked(null)} />
      )}
    </section>
  )
}
