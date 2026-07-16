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

Each session can carry one line of free text — **what you were working on**. It is
always optional, and you can write it at any time: when you start, halfway through
the afternoon, or a week later against a day already in the archive.

There is also an **optional React viewer**, which shows your sessions and drives
them — start, pause, resume, stop, and a box to say what you are doing. It is the
one part of the project with third-party dependencies (React and Vite, via npm),
and it is entirely opt-in: the tracker, the CLI and the Shortcuts never touch it,
and everything above stays true whether you build it or not.

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
python3 tracker.py task      # what am I working on?
```

A worked example:

```console
$ python3 tracker.py start --task "rewriting the parser"
Started session 2026-07-14_09-15-02 at 2026-07-14T09:15:02+03:00.
Task:    rewriting the parser

$ python3 tracker.py status
State:   running
Session: 2026-07-14_09-15-02
Started: 2026-07-14T09:15:02+03:00
Task:    rewriting the parser
Worked:  1:32:11
Paused:  0:00:00
Pauses:  0

$ python3 tracker.py pause
Paused at 2026-07-14T10:47:13+03:00.

$ python3 tracker.py status
State:   paused
Session: 2026-07-14_09-15-02
Started: 2026-07-14T09:15:02+03:00
Task:    rewriting the parser
Worked:  1:32:11          # frozen while paused
Paused:  0:12:45          # ...this is what grows
Pauses:  0 (one in progress)

$ python3 tracker.py resume
Resumed at 2026-07-14T10:59:58+03:00 after 0:12:45 paused.

$ python3 tracker.py stop
Stopped session 2026-07-14_09-15-02.
Task:    rewriting the parser
Worked:  7:47:15
Paused:  0:12:45 across 1 pause(s)
Gross:   8:00:00
Saved:   /Users/dmitriigorovoi/_WORK_PROG/work-tracker/sessions/2026-07-14_09-15-02.json
```

### Saying what you are working on

Every session can carry one line of free text: what it is for. It is **always
optional**, and it can be written at any point — including after the day is over.
That is the whole design: being made to name a session before you are allowed to
start one would be a reason not to start one.

```sh
python3 tracker.py start --task "rewriting the parser"   # say it up front
python3 tracker.py task "code review"                    # ...or change it mid-session
python3 tracker.py task                                  # what did I say it was?
python3 tracker.py task --clear                          # never mind

# The day is already archived and you never said what it was? Say it now.
python3 tracker.py task --session 2026-07-14_09-15-02 "rewriting the parser"
```

You can equally type it into the box in the [web viewer](#web-viewer), which is
where naming a session is least in the way. Whichever you use, the label is the
*only* thing that a finished session will let you rewrite: every timestamp and
every duration in that file still comes from what the clock actually recorded.

### Options

| Option | Meaning |
| --- | --- |
| `--json` | Print the result as JSON instead of text. Useful for scripting: `python3 tracker.py --json status \| jq .workedSeconds` |
| `--root DIR` | Use `DIR` as the data directory instead of the repository. |
| `--task TEXT` | On `start` and `toggle`: what the session is for. |
| `--session ID` | On `task`: act on an archived session instead of the running one. |
| `--clear` | On `task`: remove the label. |

The data directory can also be set with the `WORK_TRACKER_HOME` environment
variable. The default is the repository itself.

---

## macOS Shortcuts

Five Shortcuts wrap the commands: **Work Start**, **Work Pause**, **Work Resume**,
**Work Toggle** and **Work Stop**. Each one runs the corresponding CLI command and
shows the result as a notification.

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

So the label is written where you can see what you are typing: the box in the
[web viewer](#web-viewer), at any point during the day, or `tracker.py task` from
a terminal. A session started in silence is never a session you have lost the name
of -- the label is amendable for as long as the file exists, and naming yesterday
tomorrow costs you nothing.

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
clock in the menu bar. Its two buttons run the same CLI commands the Shortcuts
run, so — like the web viewer, and like every other way into the tracker — it is
another *caller* of the single writer rather than a second writer.

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

A small React app that shows the live session and every session before it, and
drives it: **Start**, **Pause**, **Resume** and **Stop**, plus a box for saying what
you are working on.

It is not a second writer. Every button is one call into the same `WorkTracker` the
CLI and the Shortcuts drive, so the browser cannot invent a state transition the
CLI would refuse, cannot compute a duration the CLI would disagree with, and cannot
corrupt a session by racing a Shortcut -- the writes are atomic, `start` claims the
session indivisibly, and whichever caller loses is simply told no. **There is still
exactly one writer; the browser is another caller of it.**

Nothing is drawn optimistically, either. Each command answers with the status the
server read back *after* performing it, and that is what appears on screen — so a
click that raced ⌘F8 shows what really happened rather than what the click assumed.

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
| `--allow-origin HOST` | Also accept writes from this host, so another device can drive the tracker. Repeatable. This machine is always allowed. See [Driving it from your phone](#driving-it-from-your-phone). |
| `--password-file PATH` | Require a login. `PATH` holds a password hash (see [Putting it on the public internet](#putting-it-on-the-public-internet-require-a-password)). The `WORK_TRACKER_PASSWORD_HASH` environment variable is used instead if set. |
| `--cookie-insecure` | Don't mark the session cookie `Secure`. Only for testing login over plain `http` on localhost — never for a real, TLS-terminated deployment. |

If you run the server before building the UI, it says so on the page rather than
404-ing at you. The API works either way.

### It refuses writes from anywhere but this machine

Now that the server can stop your session, it matters who is allowed to ask it to.
A `POST` that arrives carrying an `Origin` header from anywhere but this machine is
refused before it reaches the tracker, so a page you happen to have open in another
tab cannot end your day behind your back. That check also closes DNS rebinding,
where an attacker's *name* is made to resolve to `127.0.0.1`: the name may lie, but
the browser still reports the origin as `evil.example`, and that is what is checked.

Reads are left alone — binding to loopback already handles those — and `--host`
anything other than loopback now prints a warning, because it means anyone who can
reach the port can control your sessions.

### Driving it from your phone

Both of those locks are *yours to open*, deliberately and one device at a time. To
control the tracker from a phone you widen exactly two things, and out of the box
you have widened neither:

* `--host` — so the server listens somewhere the phone can actually reach, instead
  of loopback;
* `--allow-origin HOST` — so a write carrying that device's `Origin` passes the
  check above instead of being refused as foreign.

Pass neither and nothing changes: loopback only, this-machine-only writes, exactly
as described above. The flag *adds* to the allowed set and never replaces it, so
loopback keeps working no matter what else you let in, and only the host you name
gets through — not the network at large.

The recommended `HOST` is a **private** address that only your own devices can
reach, so "widen" stays "my devices" rather than becoming "anyone on the wifi".
[Tailscale](https://tailscale.com) is the clean way to get one: it puts your Mac
and phone on a private, encrypted network of *just your devices*, each with a
stable address, working on cellular as well as at home — no port forwarding and
nothing exposed to the public internet. With it running on both:

```sh
# 127.0.0.1 is your Mac's Tailscale address (tailscale ip -4)
python3 web/server.py --host 100.64.0.1 --allow-origin http://100.64.0.1:8765
```

Then open `http://100.64.0.1:8765` on the phone — a live clock and working
buttons. The same two flags work for a plain same-wifi LAN address instead; it is
simply a less private one, and the server says as much on startup. `--allow-origin`
takes a bare host or a full origin, and ignores the port either way — what a write
is allowed by is where it came from, not which port it came in on.

Binding to the Tailscale address *specifically* — rather than `0.0.0.0` — is the
stronger choice: the server then listens on the tailnet interface alone, so it is
not on your wifi at all. Only your own Tailscale devices can reach the port, and
the origin check narrows that to the one you named.

#### One command for both — `web/serve-all.sh`

Running the local viewer and the tailnet viewer as two commands is a chore, and
hard-coding the Tailscale address means editing the command every time it changes.
The launcher does both for you:

```sh
web/serve-all.sh
```

It starts the loopback viewer **always**, and additionally starts a viewer on this
Mac's current Tailscale address **only when Tailscale is connected** — asking
`tailscale ip` at launch, so nothing is hard-coded. If Tailscale is off, logged
out, or not installed, the local viewer comes up exactly as before and the tailnet
one is skipped with a one-line note: a missing tailnet is never an error. `Ctrl-C`
stops both. It honours `PORT`, `ROOT`, `PYTHON` and `TAILSCALE` from the
environment if you need to override any of them; otherwise it needs no arguments.

This is a *caller* being let in from one more place, not a new writer: every button
is still the single call into the one `WorkTracker` that the CLI, the Shortcuts and
the widget all drive. Nothing about the JSON, the atomic writes or the "one writer"
guarantee changes; the phone is another caller of it, exactly as the local browser
is.

### Putting it on the public internet (require a password)

Everything above protects a *loopback* tool from the other tabs in your browser.
A public URL is a different question — *who may look at all* — and the answer is a
login. The origin check alone is not enough there: it stops a hostile page from
writing, but anyone who knows the URL could still read your hours, and a script
sending no `Origin` at all is deliberately allowed. So on the open internet you
turn on a password, and then **every** request — read or write — must carry a
session the server signed, or it gets the login form and nothing else.

It is off by default, so nothing above changes until you switch it on. Two steps:

```sh
# 1. Create a password hash. It prompts; only the hash is ever written.
python3 -m web.auth --write .password

# 2. Start the server pointing at it. Behind TLS (see below), that's all it needs.
python3 web/server.py --password-file .password --allow-origin tracker.example.com
```

The `--allow-origin` is the same flag as for a phone, and it is needed for the
same reason: the browser at `https://tracker.example.com` stamps that origin on
every login and every button, and the origin check must let it through. Name the
host you will actually open the viewer at.

How it holds up, in one breath each:

* **The password is never stored** — only a salted PBKDF2 hash of it, in
  `.password` (created `0600`, and git-ignored). A leaked data directory does not
  leak the password.
* **Sessions are stateless, signed cookies** — `HttpOnly`, `SameSite=Strict`,
  `Secure`, thirty-day expiry. The server keeps a random secret in
  `.session_secret` (also `0600` and git-ignored) and signs each session with it;
  a cookie it did not sign is worthless. Persisting the secret means a reboot does
  not log you out.
* **Login is rate-limited** — a handful of wrong guesses from one client and it is
  locked out for a few minutes, checked *before* the password is, so the lockout
  cannot be worn down by guessing through it. Behind a reverse proxy the real
  client is read from a trusted `X-Forwarded-For`, so one attacker cannot lock
  *you* out.
* **It fails closed** — point `--password-file` at an empty or missing file and
  the server refuses to start, rather than quietly coming up with no password on a
  network you meant to lock down.

**You still need TLS in front.** The server speaks plain `http`; run it bound to
loopback behind a reverse proxy that terminates HTTPS —
[Caddy](https://caddyserver.com) does it in a two-line config and fetches the
certificate itself:

```
tracker.example.com {
    reverse_proxy 127.0.0.1:8765
}
```

That is what makes the `Secure` cookie and the whole login meaningful; without
HTTPS a password travels in the clear. (For local testing over `http` only, add
`--cookie-insecure` so the browser will return the cookie — never in production.)

Rotating the password is `python3 -m web.auth --write .password` again and a
restart. Deleting `.session_secret` and restarting logs every device out at once.

### What it shows, and what it lets you do

* **The live session** — a large worked-time clock, polled once a second. While a
  pause is open the clock is *dimmed*, because the number really is standing
  still: worked time is frozen and paused time is the one still moving.
* **The controls** — Start, Pause, Resume, Stop. Each button sends the *precise*
  command it is labelled with, never `toggle`. The difference only shows itself in
  a race, and then it matters: if a Shortcut paused the session in the second
  before you clicked **Pause**, a `toggle` would helpfully *resume* it — the exact
  opposite of what the button promised. `pause` refuses instead, the refusal is
  shown, and what was on screen was true all along. A key bound to ⌘F8 has no label
  to keep faith with, and can toggle; a button does, and cannot.
* **A box for what you are working on** — on the live session, and on every
  archived day. Type into it and press Enter. Writing it down late is not a lesser
  version of writing it down on time; it is the normal case, and the archive takes
  it. Only the label is ever rewritten.
* **Refusals, shown rather than swallowed** — in the same fault colour the widget
  uses, because "the tracker said no" is one idea and should look the same wherever
  you meet it.
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

The endpoints are built by [`web/api.py`](web/api.py), which is a function of a
`Storage` — no sockets, no HTTP — so every response, *including every write*, is
unit-tested without binding a port. [`web/server.py`](web/server.py) is a thin
`http.server` wrapper around it: stdlib only, with the static file paths resolved
and checked for containment so a crafted URL cannot escape `dist/` and read your
filesystem.

```
GET  /api/status     -> {"state": "running", "task": "rewriting the parser", ...}
GET  /api/sessions   -> {"sessions": [...], "totals": {...}, "unreadable": [...]}

POST /api/start      {"task": "..."}   -> {"action": "start",  "status": {...}}
POST /api/pause                        -> {"action": "pause",  "status": {...}}
POST /api/resume                       -> {"action": "resume", "status": {...}}
POST /api/toggle     {"task": "..."}   -> {"action": "paused", "status": {...}}
POST /api/stop                         -> {"action": "stop",   "status": {...}, "session": {...}}
POST /api/task       {"task": "...", "id": "<archived session, optional>"}
```

Every command answers with the status as read back *afterwards*, which is what the
UI renders. A refusal comes back as a `409` carrying the tracker's own sentence
("the session is already paused") — written to be read, so it is exactly what the
page puts on screen. A `500` means something else entirely: the tracker itself is
in trouble, with a corrupt file or an unwritable disk.

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
  "task": "rewriting the parser",
  "pauseStart": null,
  "pauses": []
}
```

| Field | Type | Meaning |
| --- | --- | --- |
| `state` | `"running"` \| `"paused"` | What the session is doing right now. |
| `id` | string | Identifier, derived from the start time. Also the archive's filename. |
| `start` | ISO-8601 | When the session began. |
| `task` | string \| `null` | What it is being spent on. Optional, always. |
| `pauseStart` | ISO-8601 \| `null` | When the current pause began; `null` unless `state` is `paused`. |
| `pauses` | array | Pauses that have already *finished*. |

`state` and `pauseStart` are two views of one fact, and the tracker enforces that
they agree: `pauseStart` is set if and only if `state` is `paused`. A file that
breaks this rule is rejected rather than guessed at.

`task` is the one free-text field, and the rules around it are worth stating:

* it is **never required** — a file written before the field existed reads back as
  a session nobody labelled, not as a corrupt one;
* whitespace is collapsed, so it is always one line, and a blank string is folded
  to `null` — there is one way to say "nothing written down", not two;
* the tracker is **strict about what it accepts and lenient about what it holds**:
  a label longer than 200 characters is refused when you type it, but one that is
  already on disk still loads. Refusing to read it would not make it shorter; it
  would only cost you the day it belongs to.

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
deleted. An archived session's *measurements* are immutable: nothing rewrites a
timestamp or a duration, and nothing will overwrite one day's file with another.

```json
{
  "id": "2026-07-14_19-42-18",
  "start": "2026-07-14T19:42:18+03:00",
  "end": "2026-07-14T20:31:07+03:00",
  "status": "completed",
  "task": "rewriting the parser",
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

Its `task` is the single exception, and is amendable for as long as the file exists
(`tracker.py task --session <id> "..."`, or the box in the web viewer). Forgetting
to say what you were working on is not the same as not having worked, and the
alternative to letting you fix it is a column of unlabelled days you can no longer
identify. The amendment rewrites nothing but the label: `storage.update_session`
*refuses to create a file*, so `archive` stays the only thing in the system that
can bring a session into existence, and a mistyped id can only ever fail — never
quietly mint a new, half-empty day next to the real ones.

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
        tracker.py        the service layer: every operation
        cli.py            argument parsing, rendering, exit codes
    sessions/             one JSON file per completed session
    shortcuts/            the five macOS Shortcuts, and their builder
    web/
        api.py            builds the JSON payloads, runs the commands (no HTTP)
        server.py         stdlib http.server: the API + the built UI
        serve-all.sh      launches the viewer on loopback + Tailscale in one command
        ui/               the React app (Vite); the only npm in the project
    widget/               the always-on-top mini player (Swift; optional)
    tests/                195 unit tests
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

195 tests, no dependencies, no network, no sleeping. They cover the duration
arithmetic, every state transition and every illegal one, JSON round-trips,
corrupt-file handling, the atomicity guarantees, the CLI's exit codes, the web
API's payloads, every web command and every refusal, which origins may write (and
that widening the set never lets loopback slip or the network in), and the rules
around the task label — including that an id naming an archive can never escape
`sessions/`.

The suite passes on both `/usr/bin/python3` (3.9) and current Python (3.14).

---

## Error handling

Every anticipated failure is reported as a one-line message and a non-zero exit
code -- never a traceback:

| Situation | Message | Exit |
| --- | --- | --- |
| `start` with a session already running | `a session is already in progress` | 1 |
| `pause` / `resume` / `stop` / `task` with no session | `no session is in progress` | 1 |
| `pause` when already paused | `the session is already paused` | 1 |
| `resume` when not paused | `the session is not paused` | 1 |
| `task --session` naming no archive | `no such session: ...` | 1 |
| A task longer than 200 characters | `a task may be at most 200 characters` | 1 |
| `current.json` is corrupt | `... is not valid JSON` | 1 |
| Unknown command, or `task "x" --clear` | argparse usage message | 2 |
| Success | — | 0 |

The web UI reports the same refusals, in the same words: they come back as `409`s
(or `404`s, or `400`s) carrying the tracker's own sentence, and the page shows that
sentence. There is one set of rules and one place they are enforced.

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
* **Projects and tags.** `task` is one line of free text, deliberately: it is a
  note to yourself, not a taxonomy. A `--project` key would be a *second* optional
  string on both documents, and the schema is already shaped to take one — but it
  is only worth adding once you want something that *groups* sessions rather than
  merely describes them.
* **Correcting the clock.** Because archives are plain JSON and the totals are
  recomputed from timestamps on read, fixing a forgotten `stop` is already just
  editing a file -- a future `amend` command would only be a friendlier way of
  doing what a text editor does today. Note what the tracker deliberately does
  *not* do meanwhile: `task` is the only amendment it will make to a finished day,
  and it touches no measurement.
A menu bar indicator used to be on this list. It is now
[`widget/`](#menu-bar-widget), and it needed no schema change and no new writer.
So did the task label; so did the buttons in the viewer, which are a new *caller*
and not a new writer — which is the argument for the list above.
