# Optional: drive the tracker from a Work Focus

This connects macOS's **Work Focus** to the tracker, so that turning the Focus on
starts a session and turning it off stops it. Nothing here is required -- the
Shortcuts work perfectly well on their own, and if all you want is one key for
starting, pausing and resuming, bind **Work Toggle** to ⌘F8 as the README
describes and stop reading here.

Prerequisite: the Shortcuts are imported (see [../README.md](../README.md)).

---

## 1. Create the Work Focus

If you do not already have one:

1. Open **System Settings → Focus**.
2. Click **+** and choose **Work** (or **Custom** and name it *Work*).

## 2. Start a session when the Focus turns on

1. Open the **Shortcuts** app.
2. Select the **Automation** tab in the sidebar.
3. Click **+** (top right) → **Create Personal Automation** if prompted.
4. Scroll to the **Focus** trigger and select it.
5. Choose the **Work** Focus, and tick **When turning on**.
6. Click **Next**, then **Add Action**.
7. Search for **Run Shortcut** and add it.
8. Set the shortcut to **Work Start**.
9. Turn **Ask Before Running** *off*, so the session starts silently.
10. Click **Done**.

## 3. Stop the session when the Focus turns off

Repeat the steps above with two changes:

* at step 5, tick **When turning off**;
* at step 8, choose **Work Stop**.

That is the whole automation. Turn the Work Focus on, and a session starts; turn
it off, and the session is archived under `sessions/`.

---

## Behaviour worth knowing about

**Starting twice is safe, and so is stopping twice.** If a session is already
running, `Work Start` fails with *"a session is already in progress"* and changes
nothing; the existing session keeps running and its timings are untouched. If no
session is running, `Work Stop` fails with *"no session is in progress"* and
writes nothing. In both cases the tracker refuses the operation rather than
corrupting state, so a Focus that flickers on and off cannot damage your data --
you will just see a notification saying the command was refused.

**The Focus only drives start and stop.** Pause and resume stay manual, which is
usually what you want: stepping away for coffee is not the same thing as leaving
Work Focus. That is exactly the gap **Work Toggle** on ⌘F8 fills -- the Focus
brackets your day, and the key handles the comings and goings inside it.

**Toggle and the Focus coexist.** A Focus that starts the session leaves it
`running`, so the first ⌘F8 pauses rather than starting a second session; there
is only ever one `current.json`, and `toggle` reads it before it acts.

**Sleep does not stop the session.** If your Mac sleeps with a session running,
the clock keeps counting, because the tracker measures wall-clock time between
`start` and `stop`. Closing your laptop for the night with the Focus on will
record a very long session. If that matters to you, either stop the session
explicitly, or see *Future extensions* in the README for the idle-timeout idea.

---

## Alternative: pair it with a time-of-day trigger

If your hours are regular, a Focus is not even necessary:

1. **Automation** tab → **+** → **Time of Day**.
2. Pick your start time, choose **Run Shortcut → Work Start**, disable
   *Ask Before Running*.
3. Add a second automation for your end time running **Work Stop**.

## Alternative: call the Shortcuts from a script

Shortcuts can be invoked from the command line, which is handy if you would
rather trigger them from a hotkey tool, a `launchd` job, or another script:

```sh
shortcuts run "Work Start"
shortcuts run "Work Toggle"
shortcuts run "Work Stop"
```

Equally, you can skip Shortcuts altogether and call the tracker directly:

```sh
/usr/bin/python3 /Users/dmitriigorovoi/_WORK_PROG/work-tracker/tracker.py start
/usr/bin/python3 /Users/dmitriigorovoi/_WORK_PROG/work-tracker/tracker.py toggle
```
