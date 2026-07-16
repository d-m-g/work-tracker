#!/usr/bin/env python3
"""Generate the macOS Shortcuts that drive the tracker.

Running this script produces five double-clickable, importable files:

    Work Start.shortcut
    Work Pause.shortcut
    Work Resume.shortcut
    Work Toggle.shortcut     <- the one to bind to a key
    Work Stop.shortcut

Each one contains two actions:

1. **Run Shell Script** -- invokes ``<python> <repo>/tracker.py <command>``;
2. **Show Notification** -- displays that command's output, so a Shortcut run from
   a menu bar or a Focus trigger still tells you what happened.

None of them asks you anything
------------------------------

A Shortcut runs the command and gets out of the way. In particular **Work Start**
starts the session and says so; it does not stop to ask you anything first.
That is what makes it safe to put on a Focus automation or a time-of-day trigger:
a dialog nobody is there to answer is a clock that never started, and that is a
failure you would not notice until the end of the day.

Both the interpreter and the repository path are absolute and are baked in at
build time. That is deliberate: Shortcuts runs shell scripts with a minimal
environment, so neither ``PATH`` nor the working directory can be relied upon.
Every value interpolated into a script is quoted for the shell, so a path
containing a space or a quote is an argument and never code.

The interpreter defaults to ``/usr/bin/python3`` -- the one macOS itself ships --
so the Shortcuts keep working across Homebrew upgrades and on a clean machine.

Usage::

    python3 shortcuts/build_shortcuts.py                    # build and sign
    python3 shortcuts/build_shortcuts.py --python /usr/bin/python3
    python3 shortcuts/build_shortcuts.py --output-dir ~/Desktop

Signing uses the built-in ``shortcuts sign`` tool. If signing is unavailable the
unsigned ``.plist`` files are still written, and the README explains how to build
the same Shortcuts by hand.
"""

from __future__ import annotations

import argparse
import plistlib
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Final, List, Optional, Tuple

#: The repository root: the parent of the directory holding this script.
REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

#: The interpreter macOS ships. Present on every Mac, and never moved by
#: Homebrew, which is exactly why the tracker targets Python 3.9 syntax.
DEFAULT_PYTHON: Final[Path] = Path("/usr/bin/python3")

#: One entry per Shortcut: (name, tracker command, notification title).
#:
#: 'Work Toggle' is the one meant for a keyboard shortcut: it runs 'toggle', which
#: starts, pauses or resumes depending on the state, so a single key can drive the
#: whole day. Its title is generic because -- unlike the others -- it does not know
#: in advance which of the three things it will report.
SHORTCUTS: Final[Tuple[Tuple[str, str, str], ...]] = (
    ("Work Start", "start", "Work started"),
    ("Work Pause", "pause", "Work paused"),
    ("Work Resume", "resume", "Work resumed"),
    ("Work Toggle", "toggle", "Work tracker"),
    ("Work Stop", "stop", "Work stopped"),
)

#: Glyph and colour used for the Shortcut's tile in the Shortcuts app. The
#: values are Shortcuts' own opaque identifiers; these pick a clock on orange.
_ICON: Final[Dict[str, int]] = {
    "WFWorkflowIconStartColor": 4251333119,
    "WFWorkflowIconGlyphNumber": 61440,
}


def _shell_quote(text: str) -> str:
    """Render ``text`` as a single shell word, whatever it contains."""
    return "'" + text.replace("'", "'\\''") + "'"


def _remote_exports(remote: Optional[Dict[str, str]]) -> str:
    """Shell ``export`` lines that switch the CLI to driving a VM, or nothing.

    When ``remote`` is given, each Shortcut sets ``WORK_TRACKER_SSH`` (and, if
    provided, the key and path) before invoking the CLI, so the very same
    ``tracker.py`` drives the VM -- and falls back to local when it is offline.
    Omit it and the Shortcuts stay purely local, exactly as before.
    """
    if not remote:
        return ""
    lines = [f"export WORK_TRACKER_SSH={_shell_quote(remote['destination'])}"]
    if remote.get("key"):
        lines.append(f"export WORK_TRACKER_SSH_KEY={_shell_quote(remote['key'])}")
    if remote.get("path"):
        lines.append(f"export WORK_TRACKER_SSH_PATH={_shell_quote(remote['path'])}")
    return "\n".join(lines) + "\n"


def _script(python: Path, command: str, remote: Optional[Dict[str, str]] = None) -> str:
    """Build the shell script for one Shortcut."""
    tracker = f"{_shell_quote(str(python))} {_shell_quote(str(REPO_ROOT / 'tracker.py'))}"

    # '2>&1' folds stderr into stdout so a refusal ("no session is in progress")
    # reaches the notification instead of vanishing into a log nobody reads.
    return f"{_remote_exports(remote)}{tracker} {command} 2>&1"


def _shell_script_action(script: str, output_uuid: str) -> Dict[str, Any]:
    """Build the *Run Shell Script* action.

    Args:
        script: The shell source to run.
        output_uuid: Identifier under which this action's output is published, so
            the notification action can refer back to it.
    """
    return {
        "WFWorkflowActionIdentifier": "is.workflow.actions.runshellscript",
        "WFWorkflowActionParameters": {
            "Script": script,
            "Shell": "/bin/zsh",
            # No 'Input' parameter: nothing upstream feeds this action's stdin.
            "UUID": output_uuid,
        },
    }


def _notification_action(title: str, output_uuid: str) -> Dict[str, Any]:
    """Build the *Show Notification* action that surfaces the script's output.

    The body is a text token whose single object-replacement character is
    substituted with the shell script's output -- this is how Shortcuts encodes
    "insert the previous action's result here".
    """
    return {
        "WFWorkflowActionIdentifier": "is.workflow.actions.notification",
        "WFWorkflowActionParameters": {
            "WFNotificationActionTitle": title,
            "WFNotificationActionSound": False,
            "WFNotificationActionBody": {
                "Value": {
                    "string": "￼",  # placeholder replaced by the attachment
                    "attachmentsByRange": {
                        "{0, 1}": {
                            "Type": "ActionOutput",
                            "OutputUUID": output_uuid,
                            "OutputName": "Shell Script Result",
                        }
                    },
                },
                "WFSerializationType": "WFTextTokenString",
            },
        },
    }


def build_shortcut(
    python: Path, command: str, title: str, remote: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """Assemble the full plist document for one Shortcut."""
    output_uuid = str(uuid.uuid4())

    return {
        "WFWorkflowActions": [
            _shell_script_action(_script(python, command, remote), output_uuid),
            _notification_action(title, output_uuid),
        ],
        "WFWorkflowClientVersion": "1462.2",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": dict(_ICON),
        "WFWorkflowImportQuestions": [],
        "WFWorkflowInputContentItemClasses": [],
        "WFWorkflowTypes": ["ActionExtension"],
        "WFQuickActionSurfaces": [],
    }


def sign(unsigned: Path, signed: Path) -> bool:
    """Sign ``unsigned`` into ``signed`` with the built-in ``shortcuts`` tool.

    Returns:
        ``True`` if a signed Shortcut was produced. ``False`` -- with a warning
        on stderr -- if the tool is missing or refused, in which case the caller
        keeps the unsigned plist and the user imports it manually.
    """
    try:
        result = subprocess.run(
            [
                "shortcuts",
                "sign",
                "--mode",
                "anyone",
                "--input",
                str(unsigned),
                "--output",
                str(signed),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        print("warning: the 'shortcuts' tool is unavailable; leaving files unsigned", file=sys.stderr)
        return False

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        print(f"warning: could not sign {unsigned.name}: {detail}", file=sys.stderr)
        return False
    return True


def main(argv: List[str] | None = None) -> int:
    """Build (and sign) every Shortcut. Returns a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--python",
        type=Path,
        default=DEFAULT_PYTHON,
        help=f"interpreter the Shortcuts invoke (default: {DEFAULT_PYTHON})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="where to write the .shortcut files (default: alongside this script)",
    )
    parser.add_argument(
        "--ssh",
        metavar="DEST",
        help=(
            "drive a VM instead of local files: the SSH destination the Shortcuts "
            "run tracker.py on, e.g. ubuntu@203.0.113.10. Falls back to local when "
            "the VM is unreachable. Omit for the original, purely-local Shortcuts."
        ),
    )
    parser.add_argument(
        "--ssh-key",
        metavar="PATH",
        help="identity file for --ssh (optional; whatever ssh would use otherwise)",
    )
    parser.add_argument(
        "--ssh-path",
        metavar="DIR",
        default="work-tracker",
        help="the repo directory on the VM (default: work-tracker)",
    )
    args = parser.parse_args(argv)

    python: Path = args.python.expanduser()
    if not python.is_file():
        print(f"error: no interpreter at {python}", file=sys.stderr)
        return 1

    remote: Optional[Dict[str, str]] = None
    if args.ssh:
        remote = {"destination": args.ssh, "path": args.ssh_path}
        if args.ssh_key:
            remote["key"] = str(Path(args.ssh_key).expanduser())

    output_dir: Path = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    for name, command, title in SHORTCUTS:
        document = build_shortcut(python, command, title, remote)

        # The signing tool identifies its input by extension and rejects
        # anything not named '.shortcut', so the unsigned file must already
        # carry that suffix rather than a '.plist' one.
        unsigned = output_dir / f"{name}.unsigned.shortcut"
        unsigned.write_bytes(plistlib.dumps(document, fmt=plistlib.FMT_BINARY))

        signed = output_dir / f"{name}.shortcut"
        if sign(unsigned, signed):
            unsigned.unlink()
            print(f"built {signed.name}")
        else:
            print(f"kept {unsigned.name} (unsigned -- import it by hand)")

    print(f"\nInterpreter: {python}")
    print(f"Repository:  {REPO_ROOT}")
    if remote:
        print(f"Drives VM:   {remote['destination']} (falls back to local when offline)")
    else:
        print("Drives:      local files (pass --ssh to drive a VM)")
    print("\nDouble-click each .shortcut to add it to the Shortcuts app.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
