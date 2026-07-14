import { formatDuration, formatTime } from '../lib/format.js'
import { baseOf, hourLabel, minutesOf, positionOf, segmentsOf, spanOf } from '../lib/timeline.js'

/**
 * One session, drawn as a band on the shared time-of-day axis.
 *
 * Worked time is ink. A pause is the *absence* of ink — the track showing
 * through — because that is what a pause is. Reading the strip left to right is
 * reading the day: when it began, where it broke, when it ended.
 *
 * A live session gets a lit edge at its leading end. It is the only warm colour
 * on the page, so the only thing still moving is the only thing that is warm.
 */
export default function Strip({ session, axis, live = false }) {
  const segments = segmentsOf(session)
  const held = Boolean(session.pauseStart)
  const edge = minutesOf(spanOf(session).end, baseOf(session))

  return (
    <div className="strip" role="img" aria-label={label(session, segments)}>
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

      {live && (
        <div
          className={`strip__edge ${held ? 'strip__edge--held' : ''}`}
          style={{ left: `${positionOf(edge, axis)}%` }}
        />
      )}
    </div>
  )
}

/** What a screen reader hears instead of the picture. */
function label(session, segments) {
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
          {hourLabel(tick)}
        </span>
      ))}
    </div>
  )
}
