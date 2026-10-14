import { useCallback, useEffect, useState } from 'react'
import { formatCompact, formatDate, formatTime } from '../lib/format.js'
import { getJSON, postJSON } from '../lib/useTracker.js'
import Num from './Num.jsx'

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const PARTS = ['morning', 'afternoon', 'evening']
const GROUPS = [
  ['project', 'By project'],
  ['type', 'By kind'],
  ['time', 'By time of day'],
]

/** "×1.8" with a word for which way it went. */
function Ratio({ value }) {
  const verdict = value > 1.2 ? 'runs long' : value < 0.8 ? 'quick' : 'on target'
  return (
    <span className={`ratio ${value > 1.2 ? 'ratio--long' : value < 0.8 ? 'ratio--quick' : ''}`}>
      <Num>{`×${value.toFixed(1)}`}</Num> <span className="dim ratio__word">{verdict}</span>
    </span>
  )
}

function Empty({ children }) {
  return <p className="empty dim insights__empty">{children}</p>
}

/**
 * How the planned work actually went: the figures the planner learns from, said
 * plainly, and a weekly review in words. Everything here is computed from the event
 * log and the tracker's own sessions -- nothing is entered by hand.
 */
export default function Insights() {
  const [stats, setStats] = useState(null)
  const [fault, setFault] = useState(null)
  const [writing, setWriting] = useState(false)

  const load = useCallback(() => getJSON('/api/stats').then(setStats).catch((err) => setFault(err.message)), [])
  useEffect(() => {
    load()
  }, [load])

  const review = async () => {
    setWriting(true)
    setFault(null)
    try {
      await postJSON('/api/plan/review', {})
      await load()
    } catch (err) {
      setFault(err.message)
    } finally {
      setWriting(false)
    }
  }

  if (!stats) return fault ? <p className="fault">{fault}</p> : null
  const avoided = new Set(stats.avoid.map((slot) => `${slot.weekday}-${slot.part}`))
  const slot = (day, part) => stats.slots.find((row) => row.weekday === day && row.part === part)

  return (
    <section className="insights">
      {fault && (
        <p className="refusal" role="alert">
          {fault}
        </p>
      )}

      <div className="plan-now insights__week">
        <p className="eyebrow">The last seven days</p>
        <dl className="figures">
          <div>
            <dt>Worked on tasks</dt>
            <dd>
              <Num>{formatCompact(stats.week.worked_seconds)}</Num>
            </dd>
          </div>
          <div>
            <dt>Tasks done</dt>
            <dd>
              <Num>{stats.week.done}</Num>
            </dd>
          </div>
          <div>
            <dt>Planned blocks worked</dt>
            <dd>
              <Num>{stats.week.blocks_worked}</Num>
              <span className="dim"> of </span>
              <Num>{stats.week.blocks_worked + stats.week.blocks_skipped}</Num>
            </dd>
          </div>
        </dl>
      </div>

      <div className="plan-now insights__review">
        <p className="eyebrow">Weekly review</p>
        {stats.review ? (
          <>
            <p className="insights__review-text">{stats.review.text}</p>
            <p className="live__caption">
              Written {formatDate(stats.review.at)} {formatTime(stats.review.at)} by Claude, from the figures on this page
              only.
            </p>
          </>
        ) : (
          <p className="live__caption">
            Claude reads the figures on this page — never your sessions or task notes — and says what you avoid, what you
            underestimate, and one thing to change.
          </p>
        )}
        <div className="controls">
          <button type="button" className="btn btn--go" disabled={writing} onClick={review}>
            {writing ? 'Writing…' : stats.review ? 'Write it again' : "Write this week's review"}
          </button>
        </div>
      </div>

      <h2 className="eyebrow insights__head">How long things really take</h2>
      <p className="dim insights__lede">
        Time worked ÷ estimate, for finished tasks. Once a project or kind has three, new tasks like it are planned that
        much longer.
      </p>
      <div className="insights__cols">
        {GROUPS.map(([key, title]) => (
          <div key={key} className="report">
            <p className="eyebrow report__title">{title}</p>
            {stats.estimates[key].length === 0 ? (
              <Empty>Nothing finished yet.</Empty>
            ) : (
              <ol className="report__rows insights__rows">
                {stats.estimates[key].map((row) => (
                  <li key={row.key}>
                    <span className="report__label">{row.key}</span>
                    <Ratio value={row.ratio} />
                    <span className="report__days dim">
                      {row.n} task{row.n === 1 ? '' : 's'}
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </div>
        ))}
      </div>

      {stats.fast_wins.length > 0 && (
        <p className="insights__wins">
          <span className="eyebrow">Fast wins</span>{' '}
          {stats.fast_wins.map((row) => `${row.key} (×${row.ratio.toFixed(1)})`).join(' · ')}
        </p>
      )}

      <h2 className="eyebrow insights__head">What keeps getting pushed</h2>
      {stats.dodged.length === 0 ? (
        <Empty>Nothing yet — a task shows up here when you move it, or its planned block passes unworked.</Empty>
      ) : (
        <ol className="report__rows insights__rows insights__dodged">
          {stats.dodged.map((row) => (
            <li key={row.task_id}>
              <span className="report__label">{row.title}</span>
              <span className="dim">
                moved {row.pushed}× · skipped {row.skipped}× · {row.age_days} days old
              </span>
            </li>
          ))}
        </ol>
      )}

      <h2 className="eyebrow insights__head">Starting on time</h2>
      <p className="dim insights__lede">
        Of the planned blocks that came round: how many you began within ten minutes, how late the rest began, and how
        many passed untouched (missed 15 minutes in, or never worked).
      </p>
      {!stats.punctuality.blocks ? (
        <Empty>No planned blocks have passed yet.</Empty>
      ) : (
        <>
          <dl className="figures insights__punctual">
            <div>
              <dt>Started on time</dt>
              <dd>
                <Num>{stats.punctuality.overall ? `${Math.round(stats.punctuality.overall.on_time * 100)}%` : '—'}</Num>
              </dd>
            </div>
            <div>
              <dt>Late starts, typically</dt>
              <dd>
                {stats.punctuality.overall?.late_by ? <Num>{`${stats.punctuality.overall.late_by} min`}</Num> : '—'}
              </dd>
            </div>
            <div>
              <dt>Missed</dt>
              <dd>
                <Num>{`${Math.round(stats.punctuality.missed * 100)}%`}</Num>
                <span className="dim"> of {stats.punctuality.blocks}</span>
              </dd>
            </div>
          </dl>
          <div className="insights__cols insights__cols--two">
            {[
              ['type', 'By kind'],
              ['time', 'By time of day'],
            ].map(([key, title]) => (
              <div key={key} className="report">
                <p className="eyebrow report__title">{title}</p>
                {stats.punctuality[key].length === 0 ? (
                  <Empty>Nothing worked yet.</Empty>
                ) : (
                  <ol className="report__rows insights__rows">
                    {stats.punctuality[key].map((row) => (
                      <li key={row.key}>
                        <span className="report__label">{row.key}</span>
                        <span className="ratio">
                          <Num>{`${Math.round(row.on_time * 100)}%`}</Num> <span className="dim ratio__word">on time</span>
                        </span>
                        <span className="report__days dim">{row.late_by ? `late ${row.late_by}m` : `${row.n} block${row.n === 1 ? '' : 's'}`}</span>
                      </li>
                    ))}
                  </ol>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      <h2 className="eyebrow insights__head">When planned work actually happens</h2>
      <p className="dim insights__lede">
        Share of planned blocks worked when they came round. A slot under 40% after four blocks is one deep work is kept
        out of.
      </p>
      {stats.slots.length === 0 ? (
        <Empty>No planned blocks have passed yet.</Empty>
      ) : (
        <div className="cal__scroll">
          <table className="slots">
            <thead>
              <tr>
                <th />
                {WEEKDAYS.map((day) => (
                  <th key={day} className="eyebrow">
                    {day}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {PARTS.map((part) => (
                <tr key={part}>
                  <th className="eyebrow">{part}</th>
                  {WEEKDAYS.map((_, day) => {
                    const row = slot(day, part)
                    return (
                      <td
                        key={day}
                        className={`slots__cell ${avoided.has(`${day}-${part}`) ? 'slots__cell--avoided' : ''}`}
                        style={row ? { '--rate': row.rate } : undefined}
                        title={row ? `${row.worked} worked, ${row.skipped} skipped` : 'no planned blocks yet'}
                      >
                        {row ? <Num>{`${Math.round(row.rate * 100)}%`}</Num> : <span className="dim">—</span>}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
