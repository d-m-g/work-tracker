"""``python3 planner.py <command>`` -- the planner from a terminal or a Shortcut."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from tracker.cli import default_root
from tracker.utils import TrackerError

from .planner import Planner
from .remote import RemotePlanner, open_planner


def _hm(stamp: str) -> str:
    return datetime.fromisoformat(stamp).astimezone().strftime("%a %d %b %H:%M")


def _render(plan: dict) -> str:
    lines = []
    if plan["now"]:
        lines.append(f"Now:  {plan['now']['title']} until {_hm(plan['now']['end'])[-5:]}")
    elif plan["next"]:
        lines.append(f"Next: {plan['next']['title']} at {_hm(plan['next']['start'])}")
    for task in plan["tasks"]:
        if task["status"] == "done":
            continue
        flags = "".join([
            " LATE" if task["late"] else "",
            " (blocked)" if task["status"] == "blocked" else "",
            " (unparsed)" if task["parsed"] != "done" else "",
        ])
        first = f"  next {_hm(task['blocks'][0]['start'])}" if task["blocks"] else ""
        lines.append(f"#{task['id']:<4} {task['title']}  "
                     f"[{task['worked_min']}/{task['estimate_min']}m {task['type']}]{flags}{first}")
    if plan["calendar"].get("error"):
        lines.append(f"calendar: {plan['calendar']['error']}")
    return "\n".join(lines) or "Nothing planned."


def _parse_later(root: Path, task_id: int) -> None:
    """Parse in a detached process: the clock is already running, nothing waits on Claude."""
    import subprocess

    script = Path(__file__).resolve().parent.parent / "planner.py"
    try:
        subprocess.Popen([sys.executable, str(script), "--root", str(root), "parse", str(task_id)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    except OSError:
        pass  # unparsed is still a task: its text stands as its title


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="planner", description="The personal planner.")
    parser.add_argument("--root", type=Path, default=None, metavar="DIR")
    parser.add_argument("--json", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("add", help="add a task in free text").add_argument("text", nargs="+")
    commands.add_parser("list", help="open tasks and their next block")
    commands.add_parser("replan", help="plan from now and write the calendar")
    edit = commands.add_parser("edit", help="edit fields: edit 3 priority=1 deadline=2026-10-08T18:00")
    edit.add_argument("id", type=int)
    edit.add_argument("changes", nargs="+", metavar="FIELD=VALUE")
    for name in ("done", "rm", "start"):
        commands.add_parser(name).add_argument("id", type=int)
    commands.add_parser("stop", help="stop the clock").add_argument(
        "outcome", nargs="?", choices=("done", "more", "blocked"))
    commands.add_parser("mcp", help="serve the planner to Claude over MCP (stdio)")
    commands.add_parser("start-new", help="make a task of free text and start it now").add_argument(
        "text", nargs="+")
    commands.add_parser("parse", help="(internal) parse a task's text").add_argument("id", type=int)
    commands.add_parser("rpc", help="(internal) one JSON call from another machine's planner")
    summarize = commands.add_parser("summarize", help="write what was done for Pecto in a month")
    summarize.add_argument("month", metavar="YYYY-MM")
    summarize.add_argument("--chat", default="Pecto", help="Telegram chat name ('' to skip)")
    summarize.add_argument("--repo", type=Path, action="append", help="default: every repo in ~/_WORK_PROG/PECTO")
    args = parser.parse_args(argv)
    root = args.root or default_root()

    if args.command == "summarize":
        from .parse import ParseError
        from .summary import summarize as write_summary
        try:
            text = write_summary(root, args.month, args.chat or None, paths=args.repo)
            open_planner(root).save_summary(args.month, text)
        except (ParseError, TrackerError, ValueError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(text)
        return 0

    if args.command == "rpc":
        from .remote import serve_rpc
        return serve_rpc(Planner(root))  # always this disk's planner: this *is* the far end

    if args.command == "mcp":
        from .mcp import serve
        serve(lambda: open_planner(root))
        return 0

    planner = open_planner(root)
    try:
        if args.command == "add":
            task_id = planner.add(" ".join(args.text))
            planner.parse(task_id)
        elif args.command == "edit":
            changes = {}
            for pair in args.changes:
                field, _, raw = pair.partition("=")
                try:
                    changes[field] = json.loads(raw)
                except ValueError:
                    changes[field] = raw  # bare text: a title, a date
            planner.edit(args.id, changes)
        elif args.command == "done":
            planner.done(args.id)
        elif args.command == "rm":
            planner.delete(args.id)
        elif args.command == "start":
            planner.start(args.id)
        elif args.command == "start-new":
            task_id = planner.start_new(" ".join(args.text))
            _parse_later(root, task_id)
        elif args.command == "parse":
            planner.parse(args.id)
        elif args.command == "stop":
            planner.stop(args.outcome)
        elif args.command == "replan":
            planner.replan()
    except TrackerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    plan = planner.payload()
    if isinstance(planner, RemotePlanner):
        # Tasks added on the website wait for a Mac to parse them: the VM has no
        # Claude. The widget lists the plan every 30s, so this is that Mac.
        # ponytail: a parse slower than the poll can run twice; harmless, costs a call.
        for task in plan["tasks"]:
            if task["parsed"] == "pending" and args.command != "parse":
                _parse_later(root, task["id"])
    print(json.dumps(plan, indent=2) if args.json else _render(plan))
    return 0
