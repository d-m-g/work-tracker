"""One planner: the VM's, driven from the Mac over SSH.

When this Mac drives a VM, every planner call here -- the widget, the CLI, the
local viewer, the MCP server -- goes to the planner on the VM instead of a copy on
this disk, so a task added anywhere is one task, planned once, into one calendar.

Claude still runs here, because ``claude`` is here: :meth:`RemotePlanner.parse`
and :meth:`RemotePlanner.review` fetch their input from the VM, ask Claude on
this Mac, and send back only the result. A task added on the website stays
*parsing…* until the Mac next looks at the plan (the widget does, every 30s).

The VM end is ``planner.py rpc``: one JSON request on stdin, one reply on stdout.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional, TextIO, Tuple, Union

from tracker.utils import TrackerError

from .parse import ParseError, parse_task, review_week
from .planner import Planner, PlannerError

#: What may be asked of the VM's planner. Everything the entry points call, and
#: the halves of parse/review -- nothing that starts with an underscore.
METHODS = frozenset({
    "payload", "stats", "replan", "add", "edit", "done", "delete", "start", "stop", "start_new",
    "set_hours", "pin", "unpin", "retime_work", "edit_event", "link", "calendar_payload",
    "raw_text", "parsed", "review_figures", "save_review", "save_summary",
})

#: How the VM runs its planner: inside the viewer's container, on its data.
#: Override with WORK_TRACKER_PLANNER_REMOTE for a VM laid out differently.
REMOTE_COMMAND = ("sudo -n docker exec -i \"$(sudo -n docker ps -q -f name=work-tracker | head -n1)\" "
                  "python planner.py --root /data rpc")

#: The clock-moving calls: after one, this Mac's own copy of the session is stale.
_MOVES_CLOCK = frozenset({"start", "stop", "start_new", "done"})


def _defaults(key: str) -> Optional[str]:
    try:
        done = subprocess.run(["defaults", "read", "com.work-tracker.widget", key],
                              capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() if done.returncode == 0 and done.stdout.strip() else None


def _vm_for(root: Path) -> Optional[Tuple[str, Optional[str]]]:
    """The VM to plan on, for this data root -- or None to plan locally.

    Only for the widget's own data root: that is the tracker this Mac drives. Any
    other root (a test's temporary folder, a scratch copy) stays local, whatever
    the environment says.
    """
    if sys.platform != "darwin":
        return None
    home = os.environ.get("WORK_TRACKER_HOME") or _defaults("WorkTrackerHome")
    if not home or Path(home).resolve() != Path(root).resolve():
        return None
    destination = os.environ.get("WORK_TRACKER_SSH") or _defaults("WorkTrackerRemote")
    if not destination:
        return None
    return destination, os.environ.get("WORK_TRACKER_SSH_KEY") or _defaults("WorkTrackerRemoteKey")


def open_planner(root: Path) -> Union[Planner, "RemotePlanner"]:
    """The planner for ``root``: the VM's when this Mac drives one, else this disk's."""
    vm = _vm_for(root)
    return RemotePlanner(root, *vm) if vm else Planner(root)


class RemotePlanner:
    """Stands in for :class:`Planner`; each call runs on the VM's planner."""

    calendar = None  # the VM's planner owns the calendar; nothing here touches it

    def __init__(self, root: Path, destination: str, key: Optional[str] = None,
                 transport: Optional[Callable[[str], str]] = None) -> None:
        self.root = Path(root)
        self._destination = destination
        self._key = key
        self._transport = transport or self._ssh

    def _ssh(self, request: str) -> str:
        from tracker.remote import Remote

        command = Remote(self._destination, key=self._key)._ssh_command()
        command.append(os.environ.get("WORK_TRACKER_PLANNER_REMOTE", REMOTE_COMMAND))
        try:
            done = subprocess.run(command, input=request, capture_output=True, text=True, timeout=90)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PlannerError(f"cannot reach the planner on the VM: {exc}") from exc
        if not done.stdout.strip():
            raise PlannerError("cannot reach the planner on the VM: "
                               + (done.stderr.strip()[:200] or f"ssh exited {done.returncode}"))
        return done.stdout

    def _call(self, method: str, *args: Any) -> Any:
        reply_text = self._transport(json.dumps({"method": method, "args": list(args)}))
        try:
            reply = json.loads(reply_text.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise PlannerError("the planner on the VM gave an answer that is not JSON") from None
        if not reply.get("ok"):
            raise PlannerError(reply.get("error") or "the planner on the VM refused")
        if method in _MOVES_CLOCK:
            self._sync()
        return reply.get("result")

    def _sync(self) -> None:
        """Fold the session the VM just started or stopped into this Mac's copy,
        so the widget shows it now rather than on its next sync."""
        from tracker.cli import main

        main(["--root", str(self.root), "sync"], out=io.StringIO(), err=io.StringIO())

    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name not in METHODS:
            raise AttributeError(name)
        return lambda *args: self._call(name, *args)

    def parse(self, task_id: int) -> None:
        from tracker.utils import now

        try:
            fields = parse_task(self._call("raw_text", task_id), now())
        except ParseError as exc:
            self._call("parsed", task_id, None, str(exc))
            return
        self._call("parsed", task_id, fields)

    def review(self) -> Dict[str, Any]:
        try:
            text = review_week(self._call("review_figures"))
        except ParseError as exc:
            raise PlannerError(f"the review could not be written: {exc}") from exc
        return self._call("save_review", text)

    def close(self) -> None:
        pass


def serve_rpc(planner: Planner, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> int:
    """The VM end: run one request against the real planner and answer in JSON."""
    try:
        request = json.loads(stdin.read())
        method, args = request["method"], request.get("args") or []
        if method not in METHODS or not isinstance(args, list):
            raise PlannerError(f"no such planner call: {method!r}")
        result: Dict[str, Any] = {"ok": True, "result": getattr(planner, method)(*args)}
    except TrackerError as exc:
        result = {"ok": False, "error": str(exc)}
    except (ValueError, KeyError, TypeError) as exc:
        result = {"ok": False, "error": f"bad planner request: {exc}"}
    finally:
        planner.close()
    stdout.write(json.dumps(result, default=str) + "\n")
    return 0
