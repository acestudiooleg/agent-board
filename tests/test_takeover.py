import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_board.takeover import NeedsConfirm, Takeover, proc_start
from agent_board.terminal import TerminalError


class FakeTerminals:
    def __init__(self):
        self.started, self.inside = [], None

    def name_for_pid(self, pid):
        return self.inside

    def start(self, sid, cwd):
        self.started.append((sid, cwd))


class TakeoverTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.procs, self.state, self.t = [], {}, FakeTerminals()
        self.take = Takeover(self.dir, self.t, state_of=self.state.get, exit_seconds=1)

    def tearDown(self):
        for p in self.procs:
            p.kill()
            p.wait()
        self.tmp.cleanup()

    def live(self, status="idle", argv=("sleep", "60"), **over):
        p = subprocess.Popen(argv, stdout=subprocess.PIPE)
        self.addCleanup(p.stdout.close)
        self.procs.append(p)
        d = {"pid": p.pid, "sessionId": "s1", "kind": "interactive", "cwd": "/", "status": status, "procStart": proc_start(p.pid)}
        (self.dir / f"{p.pid}.json").write_text(json.dumps({**d, **over}))
        return p

    def set_status(self, p, status):
        path = self.dir / f"{p.pid}.json"
        path.write_text(json.dumps({**json.loads(path.read_text()), "status": status}))

    def test_idle_is_taken(self):
        p = self.live()
        self.assertEqual(self.take.request("s1", self.tmp.name), "done")
        self.assertEqual(p.wait(2), -15)
        self.assertEqual(self.t.started, [("s1", self.tmp.name)])

    def test_busy_waits_then_taken(self):
        p = self.live("busy")
        self.assertEqual(self.take.request("s1", self.tmp.name), "armed")
        self.assertFalse(self.take.tick())
        self.set_status(p, "waiting")
        self.assertFalse(self.take.tick())  # поставив питання — не чіпаємо
        self.assertIsNone(p.poll())
        self.set_status(p, "idle")
        self.state["s1"] = "waiting_bg"
        self.assertFalse(self.take.tick())
        self.state["s1"] = "waiting"
        self.assertTrue(self.take.tick())
        self.assertEqual((self.t.started, self.take.armed), ([("s1", self.tmp.name)], {}))

    def test_cancel(self):
        p = self.live("busy")
        self.take.request("s1", self.tmp.name)
        self.take.cancel("s1")
        self.set_status(p, "idle")
        self.assertFalse(self.take.tick())
        self.assertIsNone(p.poll())

    def test_risky_needs_confirm(self):
        for status, state in (("waiting", None), ("shell", None), ("idle", "waiting_bg")):
            p = self.live(status)
            self.state["s1"] = state
            with self.assertRaises(NeedsConfirm):
                self.take.request("s1", self.tmp.name)
            self.assertIsNone(p.poll())
            self.assertEqual(self.take.request("s1", self.tmp.name, confirm=True), "done")
            (self.dir / f"{p.pid}.json").unlink()

    def test_refuses_foreign_pid_bg_inside_and_missing_dir(self):
        for over in ({"procStart": "1"}, {"procStart": None}, {"kind": "bg"}):
            p = self.live(**over)
            with self.assertRaises(TerminalError):
                self.take.request("s1", self.tmp.name, confirm=True)
            self.assertIsNone(p.poll())
            (self.dir / f"{p.pid}.json").unlink()
        p = self.live()
        with self.assertRaises(TerminalError):
            self.take.request("s1", "/nope/nope")
        self.t.inside = "ab-s1"
        with self.assertRaises(TerminalError):
            self.take.request("s1", self.tmp.name)
        self.assertIsNone(p.poll())
        self.assertEqual(self.t.started, [])
        with self.assertRaises(TerminalError):
            self.take.request("s2", self.tmp.name)

    def test_stubborn_process_is_left_alone(self):
        p = self.live(argv=("bash", "-c", "trap '' TERM; echo ok; while :; do sleep 0.1; done"))
        p.stdout.readline()
        with self.assertRaises(TerminalError):
            self.take.request("s1", self.tmp.name)
        self.assertIsNone(p.poll())
        self.assertEqual(self.t.started, [])

    def test_auto_failure_is_reported(self):
        p = self.live("busy")
        self.take.request("s1", self.tmp.name)
        p.kill()
        p.wait()
        self.assertTrue(self.take.tick())
        self.assertIn("закрита", self.take.errors["s1"])


if __name__ == "__main__":
    unittest.main()
