"""The monthly summary: what Claude is given, and where the answer lands."""

from __future__ import annotations

import subprocess
import unittest
from datetime import date
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from planner.planner import Planner
from planner.remote import RemotePlanner, serve_rpc
from planner.summary import AUTHOR, commits_by_day, digest, month_bounds
from tracker.storage import Storage


class TestSummary(unittest.TestCase):
    def test_a_month_runs_midnight_to_midnight(self) -> None:
        start, end = month_bounds("2026-12")
        self.assertEqual((start.date(), end.date(), start.hour), (date(2026, 12, 1), date(2027, 1, 1), 0))

    def test_commits_are_yours_and_counted_once(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "app"
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            for email, message in (("gorovoi.dmitrii@gmail.com", "add lessons"),
                                   ("vladimir.gorovoy.ai@gmail.com", "theirs"),
                                   ("gorovoi.dmitrii@gmail.com.evil", "not mine either")):
                subprocess.run(["git", "-C", str(repo), "-c", "user.name=Dmitrii Gorovoi", "-c", f"user.email={email}",
                                "commit", "-q", "--allow-empty", "-m", message],
                               check=True, env={"GIT_AUTHOR_DATE": "2026-10-05T12:00:00+03:00",
                                                "GIT_COMMITTER_DATE": "2026-10-05T12:00:00+03:00",
                                                "PATH": "/usr/bin:/bin:/opt/homebrew/bin"})
            start, end = month_bounds("2026-10")
            days = commits_by_day([repo, repo], AUTHOR, start, end)
        self.assertEqual(dict(days), {date(2026, 10, 5): ["app: add lessons"]})
        text = digest({date(2026, 10, 5): ["2.00 h: lessons"]}, days,
                      {date(2026, 10, 5): ["Dmitrii (me): shipped"], date(2026, 10, 6): ["Vladimir: did X"]})
        self.assertIn("## 2026-10-05 (Monday)\nTracked:\n- 2.00 h: lessons\nCommits:\n- app: add lessons", text)
        self.assertNotIn("2026-10-06", text)  # only chat that day: not a day worked

    def test_the_summary_is_stored_on_the_vm(self) -> None:
        with TemporaryDirectory() as tmp:
            vm = Path(tmp)

            def transport(request: str) -> str:
                out = StringIO()
                serve_rpc(Planner(vm, calendar=None), StringIO(request), out)
                return out.getvalue()

            RemotePlanner(vm / "mac", "vm", transport=transport).save_summary("2026-10", "- shipped")
            self.assertEqual(Storage(vm).load_summaries(), {"2026-10": "- shipped"})
            with self.assertRaisesRegex(Exception, "not a month"):
                RemotePlanner(vm / "mac", "vm", transport=transport).save_summary("../x", "")


if __name__ == "__main__":
    unittest.main()
