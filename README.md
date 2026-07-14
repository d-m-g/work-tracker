# work-tracker

A local work time tracker for macOS. It records when you start working, when you
pause, and when you stop, and it archives each day's session as a JSON file you
own outright.

* **Python 3 only, no third-party dependencies.** Standard library, start to finish.
* **JSON is the only source of truth.** No database, no binary format, no daemon.
  Every file is human-readable and hand-editable.
* **Crash-safe writes.** Files are written atomically, so a crash or a power cut
  cannot leave you with a half-written session.
* **Runs on the Python macOS ships.** No Homebrew, no virtualenv, no `pip install`.

There is also an **optional React viewer** for browsing your sessions in a
browser. It is the one part of the project with third-party dependencies (React
and Vite, via npm), and it is entirely opt-in: the tracker, the CLI and the
Shortcuts never touch it, and everything above stays true whether you build it or
not.

---

## Contents

* [Installation](#installation)
* [Usage](#usage)
* [macOS Shortcuts](#macos-shortcuts)
* [Menu bar widget](#menu-bar-widget)
* [Web viewer](#web-viewer)
* [JSON format](#json-format)
* [How time is calculated](#how-time-is-calculated)
* [Architecture](#architecture)
* [Testing](#testing)
* [Error handling](#error-handling)
* [Future extensions](#future-extensions)

---

## Installation

There is nothing to install. Clone or copy the repository and run it:

```sh
cd /Users/dmitriigorovoi/_WORK_PROG/work-tracker
python3 tracker.py status
```

**Requirements:** Python 3.9 or newer. That is the version macOS itself ships at
`/usr/bin/python3`, so every Mac already meets it. The code targets 3.9
deliberately -- it means the Shortcuts can call the system interpreter and keep
working across Homebrew upgrades, clean installs, and new machines. It runs
unchanged on 3.10 through 3.14.

Optionally, add a shell alias:

```sh
alias work='python3 /Users/dmitriigorovoi/_WORK_PROG/work-tracker/tracker.py'
```

...after which the commands below become `work start`, `work stop`, and so on.

---

## Usage

```sh
python3 tracker.py start     # begin a session
python3 tracker.py pause     # step away
python3 tracker.py resume    # come back
python3 tracker.py toggle    # do whichever of those three the state calls for
python3 tracker.py stop      # end the session and archive it
python3 tracker.py status    # what's going on right now?
```

A worked example:

```console
$ python3 tracker.py start
Started session 2026-07-14_09-15-02 at 2026-07-14T09:15:02+03:00.

$ python3 tracker.py status
State:   running
Session: 2026-07-14_09-15-02
Started: 2026-07-14T09:15:02+03:00
Worked:  1:32:11
Paused:  0:00:00
Pauses:  0

$ python3 tracker.py pause
Paused at 2026-07-14T10:47:13+03:00.

$ python3 tracker.py status
State:   paused
Session: 2026-07-14_09-15-02
Started: 2026-07-14T09:15:02+03:00
Worked:  1:32:11          # frozen while paused
Paused:  0:12:45          # ...this is what grows
Pauses:  0 (one in progress)

$ python3 tracker.py resume
Resumed at 2026-07-14T10:59:58+03:00 after 0:12:45 paused.

$ python3 tracker.py stop
Stopped session 2026-07-14_09-15-02.
Worked:  7:47:15
Paused:  0:12:45 across 1 pause(s)
Gross:   8:00:00
Saved:   /Users/dmitriigorovoi/_WORK_PROG/work-tracker/sessions/2026-07-14_09-15-02.json
```

### Options

| Option | Meaning |
| --- | --- |
| `--json` | Print the result as JSON instead of text. Useful for scripting: `python3 tracker.py --json status \| jq .workedSeconds` |
| `--root DIR` | Use `DIR` as the data directory instead of the repository. |

The data directory can also be set with the `WORK_TRACKER_HOME` environment
variable. The default is the repository itself.

---

## macOS Shortcuts

Five Shortcuts wrap the commands: **Work Start**, **Work Pause**, **Work
Resume**, **Work Toggle** and **Work Stop**. Each one runs the corresponding CLI
command and shows the result as a notification.

**Work Toggle** is the one worth putting on a key. It runs `toggle`, so it starts,
pauses or resumes depending on where the session already is -- one key for the
whole day, the way a play/pause button works.

### None of them asks you anything

A Shortcut runs its command and gets out of the way. **Work Start** starts the
session and says so; it does not stop to ask what the session is for.

That is what makes every one of them safe to automate. A Shortcut that asked first
would be a Shortcut that could not be put on a Focus trigger: the dialog would sit
there unanswered while you were still on the train, and the clock would never
start -- the kind of failure you only discover at six in the evening.

### Import them

They are already built, in [`shortcuts/`](shortcuts/). Double-click each
`.shortcut` file and confirm **Add Shortcut**:

```
shortcuts/Work Start.shortcut
shortcuts/Work Pause.shortcut
shortcuts/Work Resume.shortcut
shortcuts/Work Toggle.shortcut
shortcuts/Work Stop.shortcut
```

macOS may warn that the Shortcut comes from an untrusted source -- it is one you
just built yourself, and you can inspect exactly what it runs by clicking the
Shortcut in the Shortcuts app.

### Bind Work Toggle to ⌘F8

The keyboard shortcut is not part of the `.shortcut` file: macOS keeps it in the
Shortcuts app's own database, which no script may write to. So it is assigned
once, by hand:

1. Open the **Shortcuts** app and select **Work Toggle**.
2. Open the **Shortcut Details** pane -- the ⓘ button in the top right, or
   **View → Show Shortcut Details**.
3. Click **Add Keyboard Shortcut** and press **⌘F8**.

Now ⌘F8 starts the day, pauses when you step away, and resumes when you sit back
down; the notification tells you which of the three just happened. **Work Stop**
stays a separate, deliberate act -- `toggle` will never end your session, so a
mistyped key cannot archive your day early.

**If pressing ⌘F8 does nothing:** on Mac keyboards F8 is the play/pause *media*
key, and the media layer can swallow the keypress before any app sees it. Either
press **fn+⌘F8**, or turn on **System Settings → Keyboard → Keyboard Shortcuts →
Function Keys → "Use F1, F2, etc. keys as standard function keys"**, after which
plain ⌘F8 works. Whichever you choose, record the binding at step 3 with the same
keys you intend to press.

### Rebuild them

If you move the repository, or want the Shortcuts to use a different interpreter,
rebuild them and re-import:

```sh
python3 shortcuts/build_shortcuts.py
python3 shortcuts/build_shortcuts.py --python /opt/homebrew/bin/python3   # e.g.
```

The interpreter path and the repository path are baked into each Shortcut at
build time, because Shortcuts runs shell scripts with a minimal environment: it
cannot rely on your `PATH` or on a working directory.

### Automate them with a Work Focus

Turning on your Work Focus can start a session, and turning it off can stop it.
Step-by-step instructions are in [`shortcuts/AUTOMATION.md`](shortcuts/AUTOMATION.md).

---

## Menu bar widget

A small always-on-top panel — a mini player for your working day — and a live
clock in the menu bar. Unlike the web viewer, it *can* write: its two buttons run
the same CLI commands the Shortcuts run, so it is another caller of the single
writer rather than a second writer.

It is **optional**, like the viewer. It is Swift rather than Python, and it lives
entirely in [`widget/`](widget/); nothing else in the project imports it, and
"Python 3 only, no third-party dependencies" stays true of everything else
whether you build it or not. It has no dependencies of its own either — AppKit
and SwiftUI are system frameworks — so it builds offline.

### Build and run it

```sh
cd widget
./make-app.sh          # builds WorkWidget.app
open WorkWidget.app
```

Or, from a checkout, without bundling anything:

```sh
cd widget && swift run
```

**Requirements:** macOS 13 or newer, and the Swift toolchain (Xcode, or the
Command Line Tools).

To have it there every morning, add `widget/WorkWidget.app` to **System Settings
→ General → Login Items**.

### What it does

* **A worked-time clock**, always on top, over full-screen apps and across every
  Space — which is where you are when you lose track of the time. The seconds are
  dimmed, because the minutes are what you read and the seconds only prove it is
  alive; while a pause is open the clock dims whole, because the number really is
  standing still.
* **Play/pause**, which runs `toggle` — so it starts, pauses or resumes depending
  on where the session already is, exactly like ⌘F8, and it can never end your
  session. **Stop** is a separate button, and a separate, deliberate act.
* **The same controls in the menu bar**, retitled for the state you are in: *Start
  Session*, *Pause*, *Resume*, *Stop Session*.
* **The live worked time in the menu bar** — minutes only. A number that repaints
  every second in the corner of your eye is a distraction, and the panel is right
  there when you want the seconds.
* **Refusals, shown rather than swallowed.** If the CLI says no — or if
  `current.json` is corrupt — the widget says so in the fault colour and disables
  the buttons. It never guesses.

Clicking it does not steal focus (`.nonactivatingPanel`), so pausing never pulls
you out of what you were typing in. Drag it anywhere; it remembers where.

**Quitting the widget does not stop your session.** The tracker is files on disk
and goes on running whether anything is watching it or not — which is also why
you can quit and relaunch mid-session and it simply picks the session back up.

### How it is wired

It shells out to the CLI, once per tick, and parses `--json status`:

```
python3 tracker.py --json status     -> the clock
python3 tracker.py toggle            -> the play/pause button
python3 tracker.py stop              -> the stop button
```

It deliberately does not read `current.json`, and it deliberately does not do the
duration arithmetic itself. Both would be easy, and both would be a second
implementation of rules that already exist in one place — and a second
implementation is a thing that can drift. The CLI computes the durations, so the
widget cannot disagree with `tracker.py status`.

Polling is therefore the whole cost of the widget: one short-lived `python3` per
tick. It is paced accordingly — every second while running, every two while
paused (worked time is frozen, so there is nothing to animate), every five while
idle.

It finds the repository by checking, in order: `--root DIR`, the
`WORK_TRACKER_HOME` variable the CLI already honours, the `WorkTrackerHome` user
default (which `make-app.sh` sets, since an app in `/Applications` cannot find
the repository by looking around itself), and finally by walking up from the
executable — which is what makes `swift run` work in a fresh clone with no
configuration at all.

The palette is not re-picked by eye: it is the viewer's, re-expressed in Swift,
so the two are one instrument seen through two windows.

---

## Web viewer

A small React app that shows the live session and every session before it. It is
**read-only, by design**: there are no start/stop buttons. The CLI and the
Shortcuts stay the single writer of the JSON files, so the browser can never race
a Shortcut and corrupt a session. This is a window onto your data, not a second
way of editing it.

### Build it once

```sh
cd web/ui
npm install
npm run build
```

### Run it

```sh
python3 web/server.py
# work-tracker viewer -> http://127.0.0.1:8765
```

Open <http://127.0.0.1:8765>. The server binds to `127.0.0.1`, so it is reachable
from this Mac and nowhere else — your working hours are nobody else's business.

| Option | Meaning |
| --- | --- |
| `--port N` | Listen on a different port (default `8765`). |
| `--root DIR` | Read sessions from a different data directory. |
| `--host H` | Bind address. Leave it alone unless you *want* the network to see it. |

If you run the server before building the UI, it says so on the page rather than
404-ing at you. The API works either way.

### What it shows

* **The live session** — a large worked-time clock, polled once a second. While a
  pause is open the clock is *dimmed*, because the number really is standing
  still: worked time is frozen and paused time is the one still moving.
* **The day as a strip** — the signature of the thing. Each session is drawn as a
  band on a shared **time-of-day** axis: worked time is ink, and a pause is the
  *absence* of ink, the track showing through. The live session carries a warm
  edge marking where the day has got to — the only warm colour on the page, so
  the only thing still moving is the only thing that is warm.
* **Every previous session** — newest first, each on that same axis, so the days
  are directly comparable and a late start *looks* late. Click one to read its
  individual pauses; hover any block for its clock times.
* **Totals** across the whole archive.
* **Corrupt files**, if any. A session file the tracker cannot parse is named
  explicitly rather than silently skipped, and the other sessions still load —
  one bad file should not blank out a year of history.

The palette is not a matter of taste: it is checked against an OKLCH lightness
band, a chroma floor, colour-blind separation and contrast-against-surface, in
both light and dark, and the two data colours are the values that pass.

### Developing it

`npm run dev` starts Vite with hot reload on port 5173 and proxies `/api` to the
Python server, so you can edit the UI against real session data:

```sh
python3 web/server.py          # terminal 1: the API
cd web/ui && npm run dev       # terminal 2: the UI, on :5173
```

### How it is wired

The endpoints are built by [`web/api.py`](web/api.py), which is a pure function
of a `Storage` — no sockets, no HTTP — so every response is unit-tested without
binding a port. [`web/server.py`](web/server.py) is a thin `http.server` wrapper
around it: stdlib only, `GET` only, with the static file paths resolved and
checked for containment so a crafted URL cannot escape `dist/` and read your
filesystem.

```
GET /api/status     -> {"state": "running", "workedSeconds": 1800, ...}
GET /api/sessions   -> {"sessions": [...], "totals": {...}, "unreadable": [...]}
```

The durations are computed server-side, so the browser never reasons about clocks
or timezones and cannot drift away from what the CLI reports.

---

## JSON format

### While a session is running: `current.json`

This file exists **only** while a session is in progress, and is deleted when the
session stops. Its presence is what "a session is running" means.

```json
{
  "state": "running",
  "id": "2026-07-14_19-42-18",
  "start": "2026-07-14T19:42:18+03:00",
  "pauseStart": null,
  "pauses": []
}
```

| Field | Type | Meaning |
| --- | --- | --- |
| `state` | `"running"` \| `"paused"` | What the session is doing right now. |
| `id` | string | Identifier, derived from the start time. Also the archive's filename. |
| `start` | ISO-8601 | When the session began. |
| `pauseStart` | ISO-8601 \| `null` | When the current pause began; `null` unless `state` is `paused`. |
| `pauses` | array | Pauses that have already *finished*. |

`state` and `pauseStart` are two views of one fact, and the tracker enforces that
they agree: `pauseStart` is set if and only if `state` is `paused`. A file that
breaks this rule is rejected rather than guessed at.

### Every pause is stored separately

Pauses are never folded into a running total -- each is kept in full, so the
history stays auditable after the fact:

```json
{
  "start": "2026-07-14T19:42:18+03:00",
  "end": "2026-07-14T19:47:43+03:00",
  "seconds": 325
}
```

`seconds` is redundant (it is `end - start`) and is stored purely for
convenience, so that anything reading these files can sum pauses without parsing
timestamps. The tracker never trusts it on the way back in: the timestamps are
the source of truth, and `seconds` is always recomputed from them.

### When a session ends: `sessions/<id>.json`

On `stop`, the session is written to `sessions/`, and only then is `current.json`
deleted. Archived sessions are immutable; the tracker will never overwrite one.

```json
{
  "id": "2026-07-14_19-42-18",
  "start": "2026-07-14T19:42:18+03:00",
  "end": "2026-07-14T20:31:07+03:00",
  "status": "completed",
  "grossSeconds": 2929,
  "pausedSeconds": 325,
  "workedSeconds": 2604,
  "pauses": [
    {
      "start": "2026-07-14T19:52:18+03:00",
      "end": "2026-07-14T19:57:43+03:00",
      "seconds": 325
    }
  ]
}
```

### Timestamps

Every timestamp is ISO-8601 and carries an explicit UTC offset
(`2026-07-14T19:42:18+03:00`). Nothing is stored naively, so a session recorded
before a daylight-saving change still means exactly what it meant when written,
and sessions remain comparable across timezones if you travel.

---

## How time is calculated

Three numbers, and one rule connecting them:

```
grossSeconds  = end - start                     (wall-clock, the whole span)
pausedSeconds = the sum of every pause
workedSeconds = grossSeconds - pausedSeconds
```

`grossSeconds == workedSeconds + pausedSeconds` always holds; there is a test
that says so.

Two consequences worth stating plainly:

* **While you are paused, worked time stands still.** `status` counts the pause
  that is still open, so gross and paused time grow in lockstep and worked time
  does not move until you resume.
* **Stopping while paused is fine.** The open pause is closed automatically at
  the stop time, so every paused second is accounted for. You never need to
  `resume` just so you can `stop`.

Time is measured against the wall clock, not against CPU activity: if your Mac
sleeps mid-session, those seconds still count as worked. See
[Future extensions](#future-extensions).

---

## Architecture

```
work-tracker/
    tracker.py            entry point: python3 tracker.py <command>
    tracker/
        __init__.py       public API, re-exported
        utils.py          time, formatting, atomic JSON writes
        models.py         the dataclasses; the duration arithmetic
        storage.py        the only module that touches the filesystem
        tracker.py        the service layer: the six operations
        cli.py            argument parsing, rendering, exit codes
    sessions/             one JSON file per completed session
    shortcuts/            the five macOS Shortcuts, and their builder
    web/
        api.py            builds the JSON payloads (pure; no HTTP)
        server.py         stdlib http.server: the API + the built UI
        ui/               the React app (Vite); the only npm in the project
    widget/               the always-on-top mini player (Swift; optional)
    tests/                113 unit tests
    README.md
```

The layers only ever depend downwards -- `cli` → `tracker` → `storage` →
`models` → `utils` -- which is what keeps the thing testable:

* **`models`** is pure. It performs no I/O at all, so every rule it encodes (a
  pause cannot end before it starts; a paused session must have a `pauseStart`)
  can be tested with plain in-memory values.
* **`storage`** owns every path and every read and write. Nothing above it knows
  what a file is.
* **`tracker`** receives its storage *and its clock* from the caller. That second
  injection is the important one: the tests hand it a `FakeClock` and assert on
  exact durations -- an eight-hour day with a 45-minute lunch, verified in
  milliseconds, with no `sleep` anywhere in the suite.
* **`cli`** does presentation and nothing else. It is the only layer that knows
  about exit codes, and its streams are injectable, so the whole interface is
  exercised in-process without spawning a subprocess.

Embedding the tracker in another program is a two-liner:

```python
from pathlib import Path
from tracker import Storage, WorkTracker

tracker = WorkTracker(Storage(Path("~/work-tracker").expanduser()))
tracker.start()
print(tracker.status().worked_seconds)
```

### Atomic writes

JSON is the only source of truth, so a half-written file would be a lost day. No
file is ever written in place. Instead, `utils.atomic_write_json`:

1. writes the complete document to a temporary file **in the same directory**
   (same filesystem, so the rename below is a true atomic rename and not a copy);
2. calls `flush()` and `fsync()`, so the bytes are on the disk and not merely in
   the page cache;
3. `os.replace()`s the temporary file over the target.

A reader therefore only ever sees the complete old file or the complete new one.
There is no instant at which a truncated `current.json` exists on disk.

`start` goes one step further and creates `current.json` with `O_CREAT | O_EXCL`,
so that "is a session already running?" and "claim the session" are a single
indivisible step. A plain *check-then-write* would leave a window in which two
Shortcuts fired at once could both pass the check, and the second would silently
discard the first session.

---

## Testing

```sh
python3 -m unittest discover -s tests -t tests -v
```

113 tests, no dependencies, no network, no sleeping. They cover the duration
arithmetic, every state transition and every illegal one, JSON round-trips,
corrupt-file handling, the atomicity guarantees, the CLI's exit codes, and the
web API's payloads.

The suite passes on both `/usr/bin/python3` (3.9) and current Python (3.14).

---

## Error handling

Every anticipated failure is reported as a one-line message and a non-zero exit
code -- never a traceback:

| Situation | Message | Exit |
| --- | --- | --- |
| `start` with a session already running | `a session is already in progress` | 1 |
| `pause` / `resume` / `stop` with no session | `no session is in progress` | 1 |
| `pause` when already paused | `the session is already paused` | 1 |
| `resume` when not paused | `the session is not paused` | 1 |
| `current.json` is corrupt | `... is not valid JSON` | 1 |
| Unknown command | argparse usage message | 2 |
| Success | — | 0 |

`toggle` is absent from that table because it has no wrong state to be in: it
picks whichever operation the current state allows. It can still report a corrupt
`current.json`, and -- if a second Shortcut changed the state in the instant
between it reading the state and acting on it -- the refusal from the operation it
had chosen. It acts on what is on disk, never on a stale reading.

A corrupt or contradictory file is always **reported, never repaired**. The
tracker will not guess what you meant and silently rewrite your data; it tells
you what is wrong and leaves the file alone, so you can fix it by hand.

Note the deliberate ordering in `stop`: the archive is written *before*
`current.json` is deleted. If the process dies between those two steps, the worst
case is a stale `current.json` sitting next to a complete archive -- annoying, but
recoverable by deleting it. The opposite order could lose the session entirely.

---

## Future extensions

The JSON schema was chosen to make these additions non-breaking. Each is a
natural next step rather than a rewrite:

* **Reporting.** `sessions/` is a directory of uniform JSON documents named so
  that a lexicographic sort is a chronological one. A `report` command that sums
  `workedSeconds` by day, week or month is a small amount of code, and needs no
  schema change. Reading it from a spreadsheet is a `--csv` flag away.
* **Idle and sleep detection.** Today, time is wall-clock time: if the Mac sleeps
  with a session running, those hours count. A future `stop --at <time>` flag, or
  an auto-pause driven by `ioreg`'s idle timer, would let a forgotten session be
  corrected rather than merely regretted. The `status` field on an archived
  session exists precisely so an `"auto-closed"` outcome can be distinguished
  from a `"completed"` one.
* **Projects and tags.** `start --project pecto` would add one optional key to
  both documents. Old sessions without it stay perfectly valid.
* **Editing a session.** Because archives are plain JSON and the totals are
  recomputed from timestamps on read, correcting a forgotten `stop` is already
  just editing a file -- a future `amend` command would only be a friendlier way
  of doing what you can do in a text editor today.
A menu bar indicator used to be on this list. It is now
[`widget/`](#menu-bar-widget), and it needed no schema change and no new writer —
which is the argument for the list above.
