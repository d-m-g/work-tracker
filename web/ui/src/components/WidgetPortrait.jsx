import { formatDuration } from '../lib/format.js'
import Num from './Num.jsx'

/**
 * The macOS widget, drawn in CSS, showing the same session as the card above it.
 *
 * The demo can hand a visitor the viewer because the viewer is a web page. It
 * cannot hand them the widget — that is a signed app that floats over a desktop
 * they are not sitting at — so this is its portrait: the same geometry, the same
 * palette, the same grammar, reading off the same state. When you pause the demo,
 * this pill dims and holds, because there is only one session and these are two
 * windows onto it. That is the whole claim the widget makes, and it is a claim
 * you can only really believe by watching both at once.
 *
 * **It is a picture, and it says so.** The controls are chrome: they are drawn
 * because a widget without its buttons is not what the widget looks like, and
 * they are inert because a picture of a button is not a button. Drive the demo
 * from the card above. (On a real desktop these are what you press, and the page
 * is what follows.)
 *
 * Everything here is measured off widget/Sources/WorkWidget/MiniPlayer.swift
 * rather than eyeballed: 276×68 at a 16pt radius, 14 of horizontal padding, an
 * 11pt gap, a 7pt dot, a 25pt clock, a 9pt caption, three 26pt controls 4 apart.
 * The colours are Palette.swift's dark values, which are these stylesheet's
 * `:root` — the same two files, in two languages, kept honest by both quoting the
 * same source. If MiniPlayer.swift moves, this is wrong and should be moved too.
 */
export default function WidgetPortrait({ status }) {
  if (!status) return null

  const held = status.state === 'paused'
  const idle = status.state === 'idle'

  const text = formatDuration(status.workedSeconds)
  const cut = text.lastIndexOf(':')

  return (
    <section className="portrait">
      <header className="portrait__head">
        <p className="eyebrow">On the desktop</p>
        <p className="portrait__note dim">
          The menu-bar widget, the same session. Watch it while you press the buttons above.
        </p>
      </header>

      {/* aria-hidden, and not because it is decoration: it is a *duplicate*. Every
          number in it was already read out by the live card above, and a screen
          reader announcing the same clock twice is not access, it is noise. */}
      <div className="pill" aria-hidden="true">
        <span className={`pill__dot ${idle ? 'pill__dot--idle' : held ? 'pill__dot--held' : ''}`} />

        <span className="pill__readout">
          <span className="pill__clock">
            {/* The widget's rule, which is the web's rule: the minutes are what
                you read, the seconds only prove it is alive. And a held clock is
                dimmed whole, because it really is standing still. */}
            <span className={held || idle ? 'dim' : ''}>
              <Num>{text.slice(0, cut)}</Num>
            </span>
            <span className="dim">
              <Num>{text.slice(cut)}</Num>
            </span>
          </span>
          <span className="pill__caption dim">{caption(status)}</span>
        </span>

        <span className="pill__controls">
          <PillControl>
            {/* chart.bar.fill — the way out to this very page. */}
            <rect x="2" y="7" width="2.6" height="4" rx="0.6" />
            <rect x="5.6" y="4" width="2.6" height="7" rx="0.6" />
            <rect x="9.2" y="1" width="2.6" height="10" rx="0.6" />
          </PillControl>

          <PillControl>
            {held || idle ? (
              // play.fill
              <path d="M3 1.6 L10.5 6 L3 10.4 Z" />
            ) : (
              // pause.fill
              <>
                <rect x="3" y="1.6" width="2.6" height="8.8" rx="0.7" />
                <rect x="8.4" y="1.6" width="2.6" height="8.8" rx="0.7" />
              </>
            )}
          </PillControl>

          {/* stop.fill exists only when there is something to stop — the widget
              hides it on an idle tracker rather than offering a dead button. */}
          {!idle && (
            <PillControl>
              <rect x="2.2" y="2.2" width="7.6" height="7.6" rx="1.4" />
            </PillControl>
          )}
        </span>
      </div>
    </section>
  )
}

/** One of the widget's controls: an SF Symbol on a soft pink disc, redrawn. */
function PillControl({ children }) {
  return (
    <span className="pill__btn">
      <svg viewBox="0 0 12 12" className="pill__icon">
        {children}
      </svg>
    </span>
  )
}

/**
 * The caption line, in the widget's own words.
 *
 * `MiniPlayer.captionText` writes these, and it stamps them as labels — the
 * instrument's small vocabulary, not prose. The task line the real widget swaps
 * in on hover is not reproduced: hovering a picture would promise something the
 * picture cannot do.
 */
function caption(status) {
  if (status.state === 'idle') return 'NO SESSION'
  if (status.state === 'paused') return `PAUSED · ${formatDuration(status.pausedSeconds)}`
  const breaks = status.pauseCount
  return breaks === 0 ? 'RUNNING' : `RUNNING · ${breaks} BREAK${breaks === 1 ? '' : 'S'}`
}
