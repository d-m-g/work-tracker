# Zodiak

The viewer is set in Zodiak, by the [Indian Type Foundry](https://www.fontshare.com/fonts/zodiak).
The font files are **not in this repository** and a fresh clone will not have them:
`FFL.txt` here is the EULA, and section 02 forbids "uploading them in a public
server". This repository is public, so `.gitignore` keeps the binaries out of it.

## Getting them

Drop these two files into this directory:

    Zodiak-Variable.woff2
    Zodiak-Variable.woff

Either from <https://www.fontshare.com/fonts/zodiak>, or from the sibling
portfolio project, which uses the same face:

    cp ../portfolio/public/fonts/Zodiak-Variable.woff* web/ui/public/fonts/

Then `npm run build` as usual. `dist/` carries them to the VM, which is how the
deployed site gets its font.

Without them the UI still builds and runs — it just falls back to Georgia, and
the design will look wrong rather than broken.

## Do not modify them

Sections 02 and 05 of `FFL.txt` prohibit modifying, altering, or reverse
engineering the font software without ITF's prior written consent. That rules
out the obvious fix for Zodiak's figures, which are proportional and have no
`tnum` feature — a recut with tabular digits would be exactly the thing those
clauses forbid.

The numbers are made to line up in CSS instead, by `src/components/Num.jsx`,
which sets each digit in a fixed-width cell. The font is never touched. If you
are tempted to patch the font because the CSS feels roundabout: that was tried,
and the licence is why it is not here.
