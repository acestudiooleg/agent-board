import os
import sys
import tempfile
import textwrap
import time
import unittest

from agent_board.dictation import Dictation, DictationError

FAKE = textwrap.dedent("""
    import json, sys, time, os
    print(json.dumps({"ready": True}), flush=True)
    n = 0
    for line in sys.stdin.buffer:
        h = json.loads(line); pcm = sys.stdin.buffer.read(h["len"]); n += 1
        if pcm == b"die!": os._exit(1)
        if pcm == b"slow": time.sleep(30)
        print(json.dumps({"error": "біда"} if pcm == b"bad!" else {"text": f"{n}:{len(pcm)}", "lang": h["lang"]}), flush=True)
""")


class DictationTest(unittest.TestCase):
    def make(self, code=FAKE, **kw):
        d = Dictation("m", 1, ["uk"], command=[sys.executable, "-c", code], **kw)
        self.addCleanup(d.close)
        return d

    def test_reuses_process(self):
        d = self.make()
        self.assertEqual(d.transcribe(b"abcd", "uk"), {"text": "1:4", "lang": "uk"})
        self.assertEqual(d.transcribe(b"ab" * 100_000, "auto")["text"], "2:200000")

    def test_validation(self):
        d = self.make()
        for pcm, lang in ((b"", "uk"), (b"abc", "uk"), (b"x" * 4_000_002, "uk"), (b"ab", "xx")):
            with self.assertRaises(DictationError):
                d.transcribe(pcm, lang)
        self.assertIsNone(d.proc)

    def test_errors_and_recovery(self):
        d = self.make()
        for pcm in (b"bad!", b"die!"):
            with self.assertRaises(DictationError):
                d.transcribe(pcm, "uk")
        self.assertEqual(d.transcribe(b"abcd", "uk")["text"], "1:4")  # новий процес після падіння

    def test_timeout_kills(self):
        d = self.make(work_seconds=0.3)
        with self.assertRaises(DictationError):
            d.transcribe(b"slow", "uk")
        self.assertIsNone(d.proc)

    def test_load_error_is_shown(self):
        d = self.make("print('{\"error\": \"немає моделі\"}', flush=True)")
        with self.assertRaisesRegex(DictationError, "немає моделі"):
            d.transcribe(b"ab", "uk")
        with self.assertRaises(DictationError):
            Dictation("m", 1, ["uk"], command=["/nope/nope"]).transcribe(b"ab", "uk")

    def test_worker_is_found_from_any_directory(self):
        d = self.make("import os; import agent_board.dictate_worker; print('{\"ready\": true}', flush=True); input(); print('{\"text\": \"ok\"}', flush=True)")
        old = os.getcwd()
        self.addCleanup(os.chdir, old)
        os.chdir("/")
        self.assertEqual(d.transcribe(b"ab", "uk")["text"], "ok")

    def test_check(self):
        d = self.make()
        self.assertFalse(d.available)
        for code, want in (("pass", True), ("raise SystemExit(1)", False)):
            d.check_command = [sys.executable, "-c", code]
            self.assertEqual((d.check(), d.available), (want, want))
        d.check_command = [sys.executable, "-c", "import time; time.sleep(9)"]
        self.assertFalse(d.check(timeout=0.2))
        d.check_command = ["/nope/nope"]
        self.assertFalse(d.check())

    def test_worker_check_rejects_missing_model(self):
        self.assertFalse(Dictation("no/such-model-xyz", 1, ["uk"]).check())
        with tempfile.TemporaryDirectory() as empty:
            self.assertFalse(Dictation(empty, 1, ["uk"]).check())

    def test_idle_stops_process(self):
        d = self.make(idle_seconds=0.2)
        d.transcribe(b"ab", "uk")
        proc = d.proc
        proc.wait(5)
        time.sleep(0.1)
        self.assertIsNone(d.proc)


if __name__ == "__main__":
    unittest.main()
