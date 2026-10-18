"""What was done for Pecto in a month, in words -- for the month report.

Runs on the Mac, where the repos, the Telegram login and ``claude`` are:

    uv run --with telethon python3 planner.py summarize 2026-10

Per day it gathers the Pecto sessions' notes, your commits in the Pecto repos and
the Pecto group's messages, asks Claude for a short account, and stores only that
account (on the VM, through the planner). The raw messages are never stored.

Telegram needs ``.secrets/telegram.json`` -- ``{"api_id": 123, "api_hash": "..."}``
from my.telegram.org -- and one interactive login, which leaves
``.secrets/telegram.session``. That file is your whole account: it stays here.
"""

from __future__ import annotations

import json
import subprocess
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from tracker.storage import Storage
from tracker.utils import TrackerError

from .parse import CLAUDE, ParseError

PROJECT = "pecto"
PECTO_DIR = Path.home() / "_WORK_PROG" / "PECTO"
#: Only these commits count: git's --author is a regex over "Name <email>".
AUTHOR = r"<gorovoi\.dmitrii@gmail\.com>"
#: How your own chat messages are labelled, so Claude can tell them from the rest.
ME = "Dmitrii (me)"
#: ponytail: a busy month is cut here rather than chunked; chunk by week if it bites.
MAX_CHARS = 120_000

Days = Dict[date, List[str]]

PROMPT = """You write the monthly work summary that Dmitrii Gorovoi, a contractor,
sends his client, Pecto. It covers Dmitrii's work and nobody else's.
You get, per day: the hours Dmitrii tracked and his notes on those sessions,
Dmitrii's git commits, what Dmitrii asked his coding assistant (his own work,
told in his own words), and that day's messages from the Pecto team chat, where
his own are marked "Dmitrii (me)". Others in the chat (Vladimir and the rest of
the team) do their own work: never list it, and never credit Dmitrii with it.
Use the chat only to understand Dmitrii's commits and sessions, or for work
Dmitrii himself says there that he did. Write in English: one short line on the
month as a whole, then one bullet per day, saying what Dmitrii did that day in
plain words a non-engineer understands. Never quote the chat, never name other
people, never mention anything private. Do not invent work the inputs do not
show. A day with hours but no detail of its own continued the most recent work
described on an earlier day: say it continued that work, naming it. Only when
no earlier day says anything, write "general work".
Output plain text, no headings, no markdown other than "- " bullets."""


def month_bounds(month: str) -> Tuple[datetime, datetime]:
    """The local midnight the month starts on, and the one it ends on."""
    first = datetime.strptime(month, "%Y-%m").astimezone()
    following = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, following.astimezone()


def sessions_by_day(root: Path, start: datetime, end: datetime) -> Days:
    """Pecto's sessions (and the unassigned ones, as the report counts them)."""
    storage = Storage(root)
    days: Days = defaultdict(list)
    for path in storage.list_sessions():
        try:
            session = storage.load_session(path)
        except TrackerError:
            continue
        if not (start <= session.start < end):
            continue
        if session.project is not None and session.project.lower() != PROJECT:
            continue
        hours = session.worked_seconds / 3600
        days[session.start.astimezone().date()].append(f"{hours:.2f} h: {session.task or '(no note)'}")
    return days


def repos(base: Path = PECTO_DIR) -> List[Path]:
    """Every git repo up to three levels under ``base``."""
    return sorted({git.parent for depth in ("*/.git", "*/*/.git", "*/*/*/.git") for git in base.glob(depth)})


def commits_by_day(paths: Iterable[Path], author: str, start: datetime, end: datetime) -> Days:
    days: Days = defaultdict(list)
    seen = set()
    for repo in paths:
        done = subprocess.run(
            ["git", "-C", str(repo), "log", "--all", "--no-merges", f"--author={author}",
             f"--since={start.isoformat()}", f"--until={end.isoformat()}", "--format=%aI%x09%s"],
            capture_output=True, text=True, timeout=60)
        for line in done.stdout.splitlines():
            stamp, _, subject = line.partition("\t")
            day = datetime.fromisoformat(stamp).astimezone().date()
            if (day, subject) not in seen:  # a clone of a repo repeats its commits
                seen.add((day, subject))
                days[day].append(f"{repo.name}: {subject}")
    return days


def prompts_by_day(start: datetime, end: datetime, base: Path = Path.home() / ".claude" / "projects") -> Days:
    """What you asked Claude Code in the Pecto projects: your words only, not its replies."""
    days: Days = defaultdict(list)
    for path in base.glob("*PECTO*/*.jsonl"):
        with path.open(errors="replace") as lines:
            for line in lines:
                try:
                    row = json.loads(line)
                    content = row["message"]["content"]
                    if row.get("type") != "user" or not isinstance(content, str) or content.startswith("<"):
                        continue  # tool results, command echoes
                    at = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                except (ValueError, KeyError, TypeError):
                    continue
                if start <= at < end:
                    days[at.astimezone().date()].append(" ".join(content.split())[:300])
    return days


def telegram_by_day(chat: str, start: datetime, end: datetime, secrets: Path) -> Days:
    """The messages of the one chat whose name contains ``chat``, in the window."""
    try:
        from telethon.sync import TelegramClient
    except ImportError:
        raise ParseError("Telegram needs telethon: run this as "
                         "`uv run --with telethon python3 planner.py summarize ...`") from None
    try:
        keys = json.loads((secrets / "telegram.json").read_text())
    except (OSError, ValueError) as exc:
        raise ParseError(f"Telegram needs {secrets / 'telegram.json'} with api_id and api_hash: {exc}") from exc

    days: Days = defaultdict(list)
    # The first run asks for your phone number and the code Telegram sends you.
    with TelegramClient(str(secrets / "telegram"), int(keys["api_id"]), keys["api_hash"]) as client:
        found = [d for d in client.iter_dialogs() if chat.lower() in (d.name or "").lower()]
        exact = [d for d in found if d.name.lower() == chat.lower()]
        if len(exact or found) != 1:
            names = ", ".join(repr(d.name) for d in found) or "none"
            raise ParseError(f"need exactly one Telegram chat matching {chat!r}; found {names}")
        for message in client.iter_messages((exact or found)[0], offset_date=start, reverse=True):
            if message.date >= end:
                break
            if message.text:
                sender = message.sender
                name = ME if message.out else (
                    getattr(sender, "first_name", None) or getattr(sender, "title", None) or "?")
                days[message.date.astimezone().date()].append(f"{name}: {message.text[:500]}")
    return days


def digest(sessions: Days, commits: Days, chat: Days, prompts: Optional[Days] = None) -> str:
    """Everything, day by day, as Claude reads it."""
    prompts = prompts or {}
    out = []
    previous = None
    # A day with none of your own traces is not a day you worked, whatever the chat says.
    for day in sorted(set(sessions) | set(commits) | set(prompts)):
        out.append(f"## {day.isoformat()} ({day:%A})")
        bare = not commits.get(day) and not prompts.get(day) and all(
            line.split(": ", 1)[-1].strip().lower() in (PROJECT, "(no note)") for line in sessions.get(day, []))
        if bare and previous:
            # Said here, not left to the prompt: Claude skips the rule as often as not.
            out.append(f"No detail of its own: it continued the work of {previous.isoformat()}.")
        previous = day
        for title, lines in (("Tracked", sessions), ("Commits", commits),
                             ("Asked his coding assistant", prompts), ("Chat", chat)):
            if lines.get(day):
                out.append(f"{title}:")
                out.extend(f"- {line}" for line in lines[day])
        out.append("")
    return "\n".join(out)[:MAX_CHARS]


def write(text: str) -> str:
    command = [CLAUDE, "-p", text, "--model", "sonnet", "--output-format", "json",
               "--no-session-persistence", "--tools", "", "--system-prompt", PROMPT]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=300,
                              stdin=subprocess.DEVNULL)
        answer = json.loads(done.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise ParseError(str(exc)) from exc
    if answer.get("is_error") or not answer.get("result"):
        raise ParseError(answer.get("result") or done.stderr.strip() or "no answer")
    return str(answer["result"]).strip()


def summarize(root: Path, month: str, chat: Optional[str], author: str = AUTHOR,
              paths: Optional[List[Path]] = None) -> str:
    start, end = month_bounds(month)
    sessions = sessions_by_day(root, start, end)
    commits = commits_by_day(paths if paths is not None else repos(), author, start, end)
    prompts = prompts_by_day(start, end)
    messages = telegram_by_day(chat, start, end, root / ".secrets") if chat else {}
    if not (sessions or commits or prompts):
        raise ParseError(f"nothing tracked or committed for Pecto in {month}")
    return write(digest(sessions, commits, messages, prompts))
