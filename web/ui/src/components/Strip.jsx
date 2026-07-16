import { formatDuration, formatTime } from '../lib/format.js'
import { hourLabel, positionOf } from '../lib/timeline.js'
import Num from './Num.jsx'

/**
 * A band on the shared time-of-day axis. One day of it, or one live session.
 *
 * Worked time is ink. A pause is the *absence* of ink — the track showing
 * through — because that is what a pause is. Reading the strip left to right is
 * reading the day: when it began, where it broke, when it ended.
 *
 * It takes segments rather than a session, because a day is not a session. Six
 * sessions between breakfast and bed are one day and one strip, and the run of
 * track between two of them is not a pause — it is simply time you were not
 * working, which the track already says by being what it is. Only a break you
 * took *inside* a session gets a segment of its own.
 *
 * A live session gets a lit edge at its leading end. It shares the accent's hue
 * rather than opposing it, so it is told apart by being lighter, by breathing,
 * and by standing proud of the strip at both ends.
 */
export default function Strip({ segments, axis, edge = null, held = false, label }) {
  return (
    <div className="strip" role="img" aria-label={label ?? describe(segments)}>
      <div className="strip__track" />

      {segments.map((segment) => {
        const left = positionOf(segment.from, axis)
        const width = positionOf(segment.to, axis) - left
        const clock = `${formatTime(new Date(segment.at).toISOString())}–${formatTime(
          new Date(segment.until).toISOString(),
        )}`

        // Pauses get an element too, not just empty space: it gives the gap a
        // hover target, so even a two-minute break stays inspectable.
        return (
          <div
            key={`${segment.kind}-${segment.from}`}
            className={`strip__seg strip__seg--${segment.kind}`}
            style={{ left: `${left}%`, width: `${Math.max(width, 0.3)}%` }}
            title={`${segment.kind === 'work' ? 'Worked' : 'Paused'} ${clock} · ${formatDuration(
              segment.seconds,
            )}`}
          />
        )
      })}

      {edge !== null && (
        <div
          className={`strip__edge ${held ? 'strip__edge--held' : ''}`}
          style={{ left: `${positionOf(edge, axis)}%` }}
        />
      )}
    </div>
  )
}

/** What a screen reader hears instead of the picture. */
function describe(segments) {
  const blocks = segments.filter((segment) => segment.kind === 'work').length
  const pauses = segments.filter((segment) => segment.kind === 'pause').length
  return `Timeline: ${blocks} working ${blocks === 1 ? 'block' : 'blocks'}, ${pauses} ${
    pauses === 1 ? 'pause' : 'pauses'
  }.`
}

/** The hour ruler every strip is read against. Drawn once, above them all. */
export function Ruler({ axis }) {
  return (
    <div className="ruler" aria-hidden="true">
      {axis.ticks.map((tick) => (
        <span key={tick} className="ruler__tick" style={{ left: `${positionOf(tick, axis)}%` }}>
          <Num>{hourLabel(tick)}</Num>
        </span>
      ))}
    </div>
  )
}
