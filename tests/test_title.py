import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from agent_board import title_hook
from agent_board.board import Board, short_title
from agent_board.config import Config
from agent_board.server import make_handler
from agent_board.store import Store
from agent_board.terminal import claude_argv, title_settings
from tests.helpers import assistant, human, write_lines


class TitleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "claude/sessions").mkdir(parents=True)
        write_lines(self.root / "claude/projects/-p/aaa.jsonl",
                    [human("привіт", cwd="/home/u/x"), assistant("вітаю"), json.dumps({"type": "ai-title", "aiTitle": "Назва від Claude", "sessionId": "aaa"})])
        self.cfg = Config(claude_dir=self.root / "claude", db_path=self.root / "b.db", home=self.root, summary_enabled=False)
        self.board = Board(self.cfg, Store(self.cfg.db_path))
        self.addCleanup(self.board.close)
        self.name("x-1", "derived")

    def name(self, name, source):
        (self.root / "claude/sessions/1.json").write_text(json.dumps(
            {"pid": os.getpid(), "sessionId": "aaa", "kind": "interactive", "name": name, "nameSource": source, "status": "busy"}))
        self.board.scan()

    def summary(self, title):
        self.key = getattr(self, "key", 0) + 1
        self.board.store.put_summary("aaa", self.key, {"title": title, "about": "Про щось"})
        self.board.scan()

    def test_prefers_board_topic_over_claude_title(self):
        self.assertEqual(self.board.take_title("aaa"), "Назва від Claude")
        self.summary("«Кошик і оплата».")
        self.assertEqual(self.board.take_title("aaa"), "Кошик і оплата")

    def test_not_repeated_and_follows_topic(self):
        first = self.board.take_title("aaa")
        self.name(first, "hook")
        self.assertIsNone(self.board.take_title("aaa"))
        self.summary("Нова тема")
        self.assertEqual(self.board.take_title("aaa"), "Нова тема")

    def test_user_name_is_never_replaced(self):
        for source in ("user", "", "щось нове"):
            self.name("Моя назва", source)
            self.assertIsNone(self.board.take_title("aaa"), source)

    def test_our_name_restored_by_resume_may_change(self):
        self.name(self.board.take_title("aaa"), "user")  # після --resume Claude може показати нашу назву як власну
        self.summary("Далі")
        self.assertEqual(self.board.take_title("aaa"), "Далі")

    def test_off_unknown_and_closed(self):
        self.assertIsNone(self.board.take_title("zzz"))
        self.cfg.auto_title = False
        self.assertIsNone(self.board.take_title("aaa"))
        self.cfg.auto_title = True
        (self.root / "claude/sessions/1.json").unlink()
        self.assertIsNone(self.board.take_title("aaa"))

    def test_short_title(self):
        self.assertEqual(short_title(' "А\nб\x1b[31m". '), "А б [31m")
        self.assertEqual(short_title("«Кошик» і «оплата»."), "«Кошик» і «оплата»")
        self.assertEqual(short_title("слово " * 20), "слово " * 9 + "слово")
        for bad in (None, 5, "", " . "):
            self.assertEqual(short_title(bad), "")

    def test_argv(self):
        settings = title_settings(8091)
        hook = json.loads(settings)["hooks"]["UserPromptSubmit"][0]["hooks"][0]
        self.assertIn("title_hook.py 8091", hook["command"])
        argv = claude_argv("a-1", settings=settings)[2]
        self.assertIn("claude --resume a-1 --settings '{", argv)
        self.assertNotIn("--settings", claude_argv("a-1")[2])

    def hook(self, port, stdin):
        return subprocess.run([sys.executable, "-I", title_hook.__file__, str(port)], input=stdin, capture_output=True, text=True, timeout=10,
                              env={**os.environ, "http_proxy": "http://127.0.0.1:1"})

    def test_hook_end_to_end(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.board, self.cfg))
        httpd.daemon_threads = True
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (httpd.shutdown(), httpd.server_close()))
        port = httpd.server_address[1]
        r = self.hook(port, json.dumps({"session_id": "aaa", "prompt": "секрет"}))
        self.assertEqual(json.loads(r.stdout), {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "sessionTitle": "Назва від Claude"}})
        self.name("Назва від Claude", "hook")
        for port_, stdin in ((port, '{"session_id": "aaa"}'), (port, '{"session_id": "zzz"}'), (port, "сміття"), (port, "{}"), (1, '{"session_id": "aaa"}')):
            r = self.hook(port_, stdin)
            self.assertEqual((r.stdout, r.stderr, r.returncode), ("", "", 0), stdin)  # Claude не має помітити збій


if __name__ == "__main__":
    unittest.main()
