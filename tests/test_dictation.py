import sys
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

    def test_idle_stops_process(self):
        d = self.make(idle_seconds=0.2)
        d.transcribe(b"ab", "uk")
        proc = d.proc
        proc.wait(5)
        time.sleep(0.1)
        self.assertIsNone(d.proc)


if __name__ == "__main__":
    unittest.main()
