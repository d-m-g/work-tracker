"""The planner as an MCP server, so Claude Desktop / Claude Code can drive it in conversation:
"move everything for ReQ to next week", "what have I been dodging this month?".

Standard library only: JSON-RPC 2.0, one message per line on stdin/stdout -- the MCP
stdio transport. Every tool is one call into the same :class:`Planner` the viewer and
the CLI use; nothing here is a second implementation of anything.

    python3 planner.py mcp
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable, Dict, Optional, TextIO

from tracker.utils import TrackerError

from .planner import Planner

PROTOCOL = "2025-06-18"


def _task_view(task: Dict[str, Any]) -> Dict[str, Any]:
    """A task as Claude needs it: what it is and where it stands, without the plumbing."""
    keys = ("id", "title", "project", "type", "priority", "estimate_min", "worked_min", "deadline",
            "earliest_start", "status", "late", "recurrence", "splittable")
    view = {key: task.get(key) for key in keys}
    view["next_block"] = (task["blocks"] or task.get("pins") or [{}])[0].get("start")
    return view


TOOLS = [
    {
        "name": "add_task",
        "description": "Add a task from one line of free text (Claude parses duration, deadline, "
                       "priority, project, kind) and replan. Returns the parsed task.",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                        "required": ["text"]},
    },
    {
        "name": "list_tasks",
        "description": "Open tasks with their next planned block, plus what is on now and next.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "update_task",
        "description": "Change a task's fields and replan. Editable: title, estimate_min, deadline, "
                       "deadline_hard, priority (1 high..3 low), earliest_start (ISO, 'not before'), "
                       "splittable, recurrence (daily|weekdays|weekly|monthly|null), project, "
                       "type (deep|shallow|errand), status (open|blocked).",
        "inputSchema": {"type": "object",
                        "properties": {"id": {"type": "integer"}, "changes": {"type": "object"}},
                        "required": ["id", "changes"]},
    },
    {
        "name": "complete_task",
        "description": "Mark a task done (a recurring one comes back for its next date) and replan.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "integer"}},
                        "required": ["id"]},
    },
    {
        "name": "replan",
        "description": "Plan every open task again from now, around the calendars.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "query_stats",
        "description": "The analysis: estimate ratios (actual/estimate) by project, kind and time of "
                       "day; dodged tasks; slot reliability by weekday and part of day; fast wins; "
                       "this week's totals.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _call(planner: Planner, name: str, args: Dict[str, Any]) -> Any:
    if name == "add_task":
        task_id = planner.add(args.get("text"))
        planner.parse(task_id)
        return next(_task_view(t) for t in planner.payload()["tasks"] if t["id"] == task_id)
    if name == "list_tasks":
        plan = planner.payload()
        return {"tasks": [_task_view(t) for t in plan["tasks"] if t["status"] != "done"],
                "now": plan["now"], "next": plan["next"]}
    if name == "update_task":
        planner.edit(args.get("id"), args.get("changes") or {})
        return next(_task_view(t) for t in planner.payload()["tasks"] if t["id"] == args.get("id"))
    if name == "complete_task":
        planner.done(args.get("id"))
        return {"done": args.get("id")}
    if name == "replan":
        plan = planner.replan()
        return {"tasks": [_task_view(t) for t in plan["tasks"] if t["status"] != "done"]}
    if name == "query_stats":
        figures = planner.stats()
        figures.pop("titles", None)
        return figures
    raise KeyError(name)


def handle(planner: Planner, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Answer one JSON-RPC message; ``None`` for a notification, which gets no reply."""
    method, ident = message.get("method"), message.get("id")
    if ident is None:
        return None  # notifications/initialized and friends

    def reply(result: Any) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": ident, "result": result}

    if method == "initialize":
        version = (message.get("params") or {}).get("protocolVersion") or PROTOCOL
        return reply({"protocolVersion": version, "capabilities": {"tools": {}},
                      "serverInfo": {"name": "planner", "version": "1.0.0"}})
    if method == "ping":
        return reply({})
    if method == "tools/list":
        return reply({"tools": TOOLS})
    if method == "tools/call":
        params = message.get("params") or {}
        try:
            result = _call(planner, params.get("name", ""), params.get("arguments") or {})
        except KeyError:
            return {"jsonrpc": "2.0", "id": ident,
                    "error": {"code": -32602, "message": f"no such tool: {params.get('name')}"}}
        except (TrackerError, StopIteration) as exc:
            # A refusal is an answer Claude should read and act on, not a protocol error.
            return reply({"content": [{"type": "text", "text": str(exc) or "not found"}],
                          "isError": True})
        return reply({"content": [{"type": "text", "text": json.dumps(result, default=str)}]})
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": f"no method {method}"}}


def serve(planner_factory: Callable[[], Planner], stdin: TextIO = sys.stdin,
          stdout: TextIO = sys.stdout) -> None:
    """Read messages until stdin closes. A fresh Planner per call, so the plan is never stale."""
    for line in stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            stdout.write(json.dumps({"jsonrpc": "2.0", "id": None,
                                     "error": {"code": -32700, "message": "parse error"}}) + "\n")
            stdout.flush()
            continue
        planner = planner_factory()
        try:
            answer = handle(planner, message)
        finally:
            planner.db.close()
        if answer is not None:
            stdout.write(json.dumps(answer, default=str) + "\n")
            stdout.flush()
