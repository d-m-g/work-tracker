"""Free text -> task fields, via ``claude -p`` on the Claude subscription.

The LLM only parses. It is asked for a strict JSON schema, told today's date,
and told to guess rather than ask. The CLI validates against the schema; what
comes back is checked again here, because a wrong type in the database is a
crash in the scheduler later.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from typing import Any, Dict, Optional

CLAUDE = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")

TYPES = ("deep", "shallow", "errand")

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "estimate_minutes": {"type": "integer", "minimum": 5},
        "deadline": {"type": ["string", "null"]},
        "deadline_hard": {"type": "boolean"},
        "priority": {"type": "integer", "enum": [1, 2, 3]},
        "earliest_start": {"type": ["string", "null"]},
        "splittable": {"type": "boolean"},
        "min_chunk_minutes": {"type": "integer", "minimum": 5},
        "recurrence": {"type": ["string", "null"]},
        "project": {"type": ["string", "null"]},
        "type": {"type": "string", "enum": list(TYPES)},
    },
    "required": [
        "title", "estimate_minutes", "deadline", "deadline_hard", "priority",
        "earliest_start", "splittable", "min_chunk_minutes", "recurrence",
        "project", "type",
    ],
}

PROMPT = """You turn one line of free text into a task for a personal planner.
Now is {now} ({weekday}). Guess sensible defaults; never ask questions.
- title: the task, cleaned up, in the input's language. No dates or durations in it.
- estimate_minutes: as stated, else your estimate for this kind of task.
- deadline / earliest_start: ISO-8601 local datetime like 2026-10-08T18:00, or null.
  "before Thursday" means Thursday 00:00; "by Thursday" means Thursday 18:00.
- deadline_hard: true only if missing it has real consequences (a meeting, a filing).
- priority: 1 high, 2 normal, 3 low.
- splittable: false for things done in one sitting (a call, an errand).
- min_chunk_minutes: the shortest useful sitting; 60-90 for deep work, 15-30 otherwise.
- recurrence: "daily", "weekly", "weekdays", "monthly", or null.
- project: the project or client the task is for, if one is named anywhere in the text
  ("for Pecto", "ReQ billing", "по ReQ"), spelled as written; else null.
- type: deep (focused making/thinking), shallow (admin, email, small fixes), errand (away from desk).
  If the text names the type itself ("deep", "shallow", "errand"), use exactly that.
- Anything the text states outright (a duration, a deadline, a priority, a type) wins over
  your own guess; guess only what it leaves out."""


class ParseError(Exception):
    pass


def _when(raw: Any, tz: Any) -> Optional[str]:
    if raw is None:
        return None
    moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    return (moment if moment.tzinfo else moment.replace(tzinfo=tz)).isoformat()


def clean(raw: Dict[str, Any], tz: Any) -> Dict[str, Any]:
    """Validate the parser's answer into exactly the columns ``tasks`` stores."""
    try:
        kind = raw["type"] if raw["type"] in TYPES else "shallow"
        return {
            "title": " ".join(str(raw["title"]).split())[:200] or "untitled",
            "estimate_min": max(5, int(raw["estimate_minutes"])),
            "deadline": _when(raw.get("deadline"), tz),
            "deadline_hard": int(bool(raw.get("deadline_hard"))),
            "priority": min(3, max(1, int(raw.get("priority", 2)))),
            "earliest_start": _when(raw.get("earliest_start"), tz),
            "splittable": int(bool(raw.get("splittable", True))),
            "min_chunk": max(5, int(raw.get("min_chunk_minutes", 30))),
            "recurrence": raw.get("recurrence") or None,
            "project": raw.get("project") or None,
            "type": kind,
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ParseError(f"unusable parse: {exc}") from exc


def parse_task(text: str, now: datetime) -> Dict[str, Any]:
    """Ask Claude for the fields of ``text``. One retry, then :class:`ParseError`."""
    prompt = PROMPT.format(now=now.strftime("%Y-%m-%dT%H:%M%z"), weekday=now.strftime("%A"))
    command = [
        CLAUDE, "-p", text, "--model", "haiku", "--output-format", "json",
        "--no-session-persistence", "--tools", "", "--system-prompt", prompt,
        "--json-schema", json.dumps(SCHEMA),
    ]
    error: Exception = ParseError("no attempt")
    for _ in range(2):
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=90,
                                  stdin=subprocess.DEVNULL)
            answer = json.loads(done.stdout)
            if answer.get("is_error") or "structured_output" not in answer:
                raise ParseError(answer.get("result") or done.stderr.strip() or "no answer")
            return clean(answer["structured_output"], now.tzinfo)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, ParseError) as exc:
            error = exc
    raise ParseError(str(error))


REVIEW_PROMPT = """You write a short, honest weekly review for one person, from figures about
how their planned work actually went. Plain text -- no markdown, no asterisks, no bullets,
no headings -- under 150 words, three short paragraphs:
what they avoid (dodged tasks, skipped slots), what they underestimate (estimate ratios above
1.2; a ratio is actual time / estimate), and exactly one concrete change for next week.
Be direct and specific to the figures; do not invent anything they do not show. If the
figures are too thin to say something, say that instead of guessing."""


def review_week(figures: Dict[str, Any]) -> str:
    """Ask Claude for the weekly review. Aggregates only -- never raw logs or sessions."""
    command = [
        CLAUDE, "-p", json.dumps(figures, default=str), "--model", "sonnet",
        "--output-format", "json", "--no-session-persistence", "--tools", "",
        "--system-prompt", REVIEW_PROMPT,
    ]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=180,
                              stdin=subprocess.DEVNULL)
        answer = json.loads(done.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise ParseError(str(exc)) from exc
    if answer.get("is_error") or not answer.get("result"):
        raise ParseError(answer.get("result") or done.stderr.strip() or "no answer")
    return str(answer["result"]).strip()
