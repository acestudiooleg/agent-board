import sys
import threading
import time
import unittest
from pathlib import Path

import os
import subprocess
import tempfile

from agent_board.summarizer import BUDGET, Summarizer, build_prompt, parse_response, run_claude
from agent_board.transcript import Transcript, _add

FAKE = [sys.executable, str(Path(__file__).with_name("fake_claude.py"))]


def transcript(n=3, size=10):
    t = Transcript("s", Path("/x"))
    t.cwd = "/home/u/server/tutor-app"
    for i in range(n):
        _add(t, "user" if i % 2 == 0 else "assistant", f"m{i} " + "x" * size)
    t.first_user = "m0 " + "x" * size
    t.files = {"/home/u/server/tutor-app/db.py"}
    return t


class Collector:
    def __init__(self):
        self.results, self.event = [], threading.Event()

    def __call__(self, sid, key, summary, err):
        self.results.append((sid, key, summary, err))
        self.event.set()


class PromptTest(unittest.TestCase):
    def test_contains_context(self):
        p = build_prompt(transcript(), "tutor-app", ["tutor-app", "viewer"])
        for needle in ("tutor-app", "viewer", "КОРИСТУВАЧ: m0", "АГЕНТ: m1", "db.py"):
            self.assertIn(needle, p)
        self.assertNotIn("ПЕРШЕ ПОВІДОМЛЕННЯ", p)  # уже є в head

    def test_budget_keeps_real_head_and_tail(self):
        p = build_prompt(transcript(n=60, size=2000), "tutor-app", [])
        self.assertLess(len(p), BUDGET + 3000)
        self.assertIn("m0 ", p)
        self.assertIn("m5 ", p)
        self.assertIn("m59 ", p)
        self.assertIn("[…пропущено…]", p)


class ParseTest(unittest.TestCase):
    def test_extracts_json_from_fenced_result(self):
        out = parse_response('{"result": "```json\\n{\\"about\\": \\"A\\", \\"done\\": [\\"x\\", 2]}\\n```"}')
        self.assertEqual(out, {"title": "", "about": "A", "done": ["x", "2"], "waiting_for": "",
                               "related_projects": [], "likely_project": ""})

    def test_errors(self):
        with self.assertRaises(ValueError):
            parse_response('{"result": "без json"}')
        with self.assertRaises(ValueError):
            parse_response('{"result": "x", "is_error": true}')


class RunClaudeTest(unittest.TestCase):
    def test_stable_cwd_and_no_work_config_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp) / "haiku"
            os.environ["CLAUDE_CONFIG_DIR"] = "/home/u/.claude-work"
            try:
                out = parse_response(run_claude("ENVCHECK", FAKE, 30, cwd))
            finally:
                del os.environ["CLAUDE_CONFIG_DIR"]
        self.assertEqual(out["about"], f"env=none cwd={cwd}")

    def test_timeout_kills_process_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            started = time.time()
            with self.assertRaises(subprocess.TimeoutExpired):
                run_claude("HANG", FAKE, 1.5, Path(tmp))
            self.assertLess(time.time() - started, 10)  # не чекаємо, поки нащадок сам завершиться
            child = int((Path(tmp) / "child.pid").read_text())
        deadline = time.time() + 3
        while time.time() < deadline:
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.kill(child, 9)
            self.fail("нащадок claude пережив тайм-аут")


class SummarizerTest(unittest.TestCase):
    def make(self, on_result, prompts, **kw):
        """prompts: sid -> текст промпту (ключ версії завжди "1"); None — підсумок уже не потрібен."""
        prepare = lambda sid: None if prompts.get(sid) is None else ("1", prompts[sid])
        s = Summarizer(FAKE, timeout=30, concurrency=1, prepare=prepare, on_result=on_result, **kw)
        self.addCleanup(s.stop)
        return s

    def wait_results(self, c, n):
        deadline = time.time() + 30
        while len(c.results) < n and time.time() < deadline:
            time.sleep(0.05)

    def test_success(self):
        c = Collector()
        s = self.make(c, {"s": "звичайний"})
        self.assertTrue(s.request("s", (0, 0)))
        self.assertTrue(c.event.wait(30))
        sid, key, summary, err = c.results[0]
        self.assertEqual((sid, key, err), ("s", "1", None))
        self.assertEqual(summary["waiting_for"], "Видаляти таблицю?")

    def test_failure_and_garbage(self):
        c = Collector()
        s = self.make(c, {"a": "FAIL", "b": "GARBAGE"})
        s.request("a", (0, 0))
        s.request("b", (0, 1))
        self.wait_results(c, 2)
        self.assertEqual([r[2] for r in c.results], [None, None])
        self.assertIn("boom", c.results[0][3])
        self.assertEqual(s.status()["last_error"][:10], c.results[1][3][:10])

    def test_one_request_per_session(self):
        gate = threading.Event()
        s = self.make(lambda *a: gate.wait(5), {"s": "p"})
        self.assertTrue(s.request("s", (0, 0)))
        self.assertTrue(s.is_pending("s"))
        self.assertFalse(s.request("s", (0, 0)))
        gate.set()

    def test_callback_error_does_not_kill_worker(self):
        c = Collector()
        calls = []

        def on_result(*args):
            calls.append(args)
            if len(calls) == 1:
                raise RuntimeError("callback boom")
            c(*args)

        s = self.make(on_result, {"a": "p", "b": "p"})
        with self.assertLogs("agent_board", level="ERROR"):
            s.request("a", (0, 0))
            s.request("b", (0, 1))
            self.assertTrue(c.event.wait(30))
        self.assertEqual(c.results[0][0], "b")

    def test_unneeded_job_does_not_spend_slot(self):
        c = Collector()
        s = self.make(c, {"stale": None, "b": "p"}, per_hour=1)
        s.request("stale", (0, 0))
        s.request("b", (0, 1))
        self.assertTrue(c.event.wait(30))
        self.assertEqual([r[0] for r in c.results], ["b"])
        self.assertFalse(s.is_pending("stale"))

    def test_hourly_limit_and_persisted_starts(self):
        c = Collector()
        recorded = []
        s = self.make(c, {"a": "p", "b": "p"}, per_hour=2, starts=[time.time() - 60], on_start=recorded.append)
        s.request("a", (0, 0))
        s.request("b", (0, 1))
        self.assertTrue(c.event.wait(30))
        time.sleep(1.5)
        self.assertEqual([r[0] for r in c.results], ["a"])  # другий слот зайнятий викликом до рестарту
        self.assertEqual(len(recorded), 1)
        self.assertEqual(s.pending_count(), 1)

    def test_breaker_pauses_after_failures(self):
        c = Collector()
        s = self.make(c, {sid: "FAIL" for sid in "abc"}, breaker_failures=2, breaker_seconds=3600)
        with self.assertLogs("agent_board", level="WARNING"):
            for i, sid in enumerate("abc"):
                s.request(sid, (0, i))
            self.wait_results(c, 2)
            time.sleep(1.5)
        self.assertEqual(len(c.results), 2)
        self.assertIsNotNone(s.status()["paused_until"])


if __name__ == "__main__":
    unittest.main()
