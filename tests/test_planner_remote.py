"""The Mac's planner driving the VM's: one planner, over a JSON call per operation."""

from __future__ import annotations

import os
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from planner import remote
from planner.parse import ParseError
from planner.planner import Planner, PlannerError
from planner.remote import RemotePlanner, open_planner, serve_rpc


def fake_parser(text, now):
    if "fail" in text:
        raise ParseError("nope")
    return {"title": text.upper(), "estimate_min": 90, "type": "deep", "evil = 1; --": 2}


class TestRemotePlanner(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.dict(os.environ, {"WORK_TRACKER_SSH": ""})
        patcher.start()
        self.addCleanup(patcher.stop)
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.vm_root = Path(tmp.name) / "vm"
        self.mac_root = Path(tmp.name) / "mac"
        self.requests = []

        def transport(request: str) -> str:  # the VM: `planner.py rpc`, in-process
            self.requests.append(request)
            out = StringIO()
            serve_rpc(Planner(self.vm_root, parser=fake_parser, calendar=None,
                              tracker_cli=lambda args: ""), StringIO(request), out)
            return out.getvalue()

        self.planner = RemotePlanner(self.mac_root, "vm", transport=transport)
        self.planner._sync = lambda: None

    def test_a_task_added_here_lives_on_the_vm(self) -> None:
        task_id = self.planner.add("write the report")
        self.assertEqual([t["id"] for t in self.planner.payload()["tasks"]], [task_id])
        self.assertFalse((self.mac_root / "planner.db").exists())
        self.assertTrue((self.vm_root / "planner.db").exists())

    def test_parse_runs_here_and_is_stored_there(self) -> None:
        task_id = self.planner.add("write the report")
        with mock.patch.object(remote, "parse_task", fake_parser):
            self.planner.parse(task_id)
        task = self.planner.payload()["tasks"][0]
        self.assertEqual((task["title"], task["parsed"]), ("WRITE THE REPORT", "done"))
        failed = self.planner.add("this will fail")
        with mock.patch.object(remote, "parse_task", fake_parser):
            self.planner.parse(failed)
        states = {t["id"]: t["parsed"] for t in self.planner.payload()["tasks"]}
        self.assertEqual(states[failed], "failed")

    def test_refusals_come_back_as_refusals(self) -> None:
        with self.assertRaisesRegex(PlannerError, "no such task"):
            self.planner.done(999)
        with self.assertRaises(AttributeError):
            self.planner.db  # noqa: B018 -- nothing private crosses
        out = StringIO()
        serve_rpc(Planner(self.vm_root, calendar=None), StringIO('{"method": "_log", "args": []}'), out)
        self.assertIn("no such planner call", out.getvalue())

    def test_only_the_widgets_own_root_goes_remote(self) -> None:
        with mock.patch.object(remote.sys, "platform", "darwin"), \
                mock.patch.dict(os.environ, {"WORK_TRACKER_HOME": str(self.mac_root),
                                             "WORK_TRACKER_SSH": "me@vm"}):
            self.assertIsInstance(open_planner(self.mac_root), RemotePlanner)
            local = open_planner(self.vm_root)
            self.assertIsInstance(local, Planner)
            local.close()


if __name__ == "__main__":
    unittest.main()
