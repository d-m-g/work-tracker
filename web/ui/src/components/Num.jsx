/**
 * A number, set so it holds still.
 *
 * Zodiak is a display serif: its figures are proportional and it has no `tnum`
 * feature, so a `1` is a third narrower than a `0`. The clock reprints itself
 * once a second, which on the bare face means the digits shove each other
 * sideways on every tick; a column of durations never lines up either.
 * `font-variant-numeric: tabular-nums` cannot help, because there is no feature
 * in the font for it to switch on.
 *
 * The usual fix is to recut the font with tabular figures. Zodiak's licence
 * forbids it -- see public/fonts/FFL.txt, sections 02 and 05 -- so the digits
 * are given equal cells here instead, and the font is never touched.
 *
 * Only digits get a cell. Everything else (the colons, a stray `+`) is passed
 * through at its natural width, which is fine because those characters do not
 * change as the number does. Kerning is the one thing left that could still
 * move them -- the colon carries kern pairs even though the digits do not -- so
 * the elements that use this turn it off in CSS.
 *
 * There is no wrapper element: this renders a fragment, so it drops into a
 * <dd>, a <span> or a <p> without adding anything for a layout to trip over.
 * The text is left as text, in order, so it still reads as "4:17:32".
 */
export default function Num({ children }) {
  return [...String(children ?? '')].map((char, index) =>
    char >= '0' && char <= '9' ? (
      <span key={index} className="digit">
        {char}
      </span>
    ) : (
      char
    ),
  )
}
