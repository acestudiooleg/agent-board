import base64
import http.client
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from agent_board import system
from agent_board.board import Board
from agent_board.config import Config
from agent_board.server import make_handler
from agent_board.store import Store
from agent_board.system import proc_start
from agent_board.term_api import TermService
from agent_board.terminal import Terminals
from tests.helpers import assistant, human, write_lines
from tests.test_terminal import wait_for

# Підставний claude: пише sessions/<pid>.json і відлунює введене.
FAKE = """
echo '{"pid":'$$',"sessionId":"%s","kind":"interactive","status":"'$(cat %s/status 2>/dev/null || echo idle)'"}' > %s/claude/sessions/$$.json
stty -echo; echo ГОТОВО; while read -r x; do [ "$x" = вихід ] && exit 0; echo "E:$x:E"; done
"""


class FakeDictation:
    available = True

    def transcribe(self, pcm, lang):
        return {"text": f"{len(pcm)} байтів", "lang": lang}

    def close(self):
        pass


@unittest.skipUnless(shutil.which("tmux"), "потрібен tmux")
class TermApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "claude/sessions").mkdir(parents=True)
        for sid, cwd in (("aaa", self.tmp.name), ("gone", "/nope/nope")):
            write_lines(self.root / f"claude/projects/-p/{sid}.jsonl", [human("привіт", cwd=cwd), assistant("вітаю")])
        cfg = Config(claude_dir=self.root / "claude", db_path=self.root / "b.db", home=self.root, summary_enabled=False)
        self.board = Board(cfg, Store(cfg.db_path))
        self.terms = Terminals(f"ab-test-{os.getpid()}-{type(self).__name__[7:10]}{self._testMethodName[-12:]}",
                               command=lambda sid: ["bash", "-c", FAKE % (sid, self.root, self.root)])
        self.service = TermService(self.board, self.terms, FakeDictation())
        self.board.scan()
        self.launched = []
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(
            self.board, cfg, launcher=lambda argv, **kw: self.launched.append(argv), terminator_config=self.root / "term.config", term=self.service))
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.port = self.httpd.server_address[1]
        self.streams = []

    def tearDown(self):
        for conn in self.streams:
            conn.close()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.service.close()
        self.terms.tmux("kill-server", check=False)
        Path(os.environ.get("TMUX_TMPDIR", "/tmp"), f"tmux-{os.getuid()}", self.terms.socket).unlink(missing_ok=True)
        self.board.close()
        self.tmp.cleanup()

    def post(self, path, body=None, raw=None, **headers):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}", **headers}
        conn.request("POST", path, body=raw if raw is not None else json.dumps(body), headers=h)
        r = conn.getresponse()
        data = json.loads(r.read())
        conn.close()
        return r.status, data

    def open(self, start=True, sid="aaa"):
        return self.post("/api/term/open", {"id": sid, "cols": 80, "rows": 24, "start": start})

    def stream(self, key, **headers):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", f"/api/term/stream?key={key}", headers=headers)
        r = conn.getresponse()
        self.streams += [conn, r]
        return r

    def until(self, r, text):
        out, end = b"", time.monotonic() + 8
        while time.monotonic() < end:
            line = r.fp.readline().strip()
            if line == b"event: end":
                out += b"<END>"
            elif line.startswith(b"data: ") and line != b"data: end":
                out += base64.b64decode(line[6:])
            if text.encode() in out:
                return out.decode(errors="replace")
        self.fail(f"немає {text!r} у {out[-300:]!r}")

    def started(self):
        status, data = self.open()
        self.assertEqual(status, 200, data)
        r = self.stream(data["key"])
        self.until(r, "ГОТОВО")
        return data["key"], r

    def test_full_cycle(self):
        self.assertEqual(self.open(start=False), (409, {"error": "розмову закрито", "closed": True}))
        key, r = self.started()
        self.board.scan()
        self.assertTrue(self.board.card("aaa")["term"])
        self.assertEqual(self.post("/api/term/input", {"key": key, "data": "раз\r"})[0], 200)
        self.until(r, "E:раз:E")
        self.assertEqual(self.post("/api/term/paste", {"key": key, "text": "два три"})[0], 200)
        self.until(r, "E:два три:E")
        self.assertEqual(self.post("/api/term/resize", {"key": key, "cols": 100, "rows": 30})[0], 200)
        # друге вікно приєднується до тієї ж розмови
        status, data = self.open(start=False)
        self.assertEqual(status, 200)
        self.until(self.stream(data["key"]), "E:два три:E")
        self.assertEqual(len(self.terms.panes()), 1)
        self.assertEqual(self.post("/api/term/detach", {"key": data["key"]})[0], 200)
        self.assertEqual(self.post("/api/term/input", {"key": data["key"], "data": "x"})[0], 409)
        self.post("/api/term/input", {"key": key, "data": "вихід\r"})
        self.until(r, "<END>")
        self.assertEqual(self.post("/api/term/input", {"key": key, "data": "x"})[0], 409)

    def test_closed_stream_frees_viewer_but_not_session(self):
        key, r = self.started()
        for _ in range(2):
            self.streams.pop().close()
        self.assertTrue(wait_for(lambda: not self.service.viewers.by_key, 12))
        self.assertEqual(len(self.terms.panes()), 1)

    def test_paste_refused_on_menu_and_while_starting(self):
        (self.root / "status").write_text("waiting")
        key, _ = self.started()
        status, data = self.post("/api/term/paste", {"key": key, "text": "ні"})
        self.assertEqual(status, 409)
        self.assertIn("меню", data["error"])
        for f in (self.root / "claude/sessions").glob("*.json"):
            f.unlink()
        self.assertEqual(self.post("/api/term/paste", {"key": key, "text": "ні"})[0], 409)

    def test_jump_attaches_same_tmux(self):
        self.started()
        self.assertTrue(self.post("/api/jump", {"id": "aaa"})[1]["ok"])
        self.assertEqual(self.launched[0][0], "terminator")
        conf = (self.root / "term.config").read_text()
        self.assertIn("attach-session", conf)
        self.assertNotIn("--resume", conf)

    def test_new_chat(self):
        self.board.save_project(self.tmp.name, "Мій")
        self.assertEqual([(f["path"], f["project"]) for f in self.board.folders()], [(self.tmp.name, "Мій")])
        for bad in ("/", "/etc", "/nope/nope", None, 5, self.tmp.name + "/"):
            self.assertEqual(self.post("/api/term/new", {"path": bad, "cols": 80, "rows": 24})[0], 400, bad)
        self.assertEqual(self.post("/api/term/new", {"path": self.tmp.name, "cols": 0, "rows": 24})[0], 400)
        self.assertEqual(self.terms.panes(), {})
        used = []
        self.terms.new_command = lambda sid: used.append(sid) or self.terms.command(sid)
        status, data = self.post("/api/term/new", {"path": self.tmp.name, "cols": 80, "rows": 24})
        self.assertEqual((status, used), (200, [data["id"]]))
        self.assertIsNone(self.board.card(data["id"]))  # дошка ще не бачила — але під'єднатися вже можна
        status, opened = self.open(start=False, sid=data["id"])
        self.assertEqual(status, 200, opened)
        self.until(self.stream(opened["key"]), "ГОТОВО")
        self.board.scan()
        self.assertTrue(self.board.card(data["id"])["term"])
        self.assertEqual(self.post("/api/term/stop", {"id": data["id"]})[0], 200)
        self.assertEqual(self.open(start=True, sid=data["id"])[0], 404)

    def test_stop(self):
        self.started()
        self.assertEqual(self.post("/api/term/stop", {"id": "aaa"})[0], 200)
        self.assertEqual(self.terms.panes(), {})
        self.assertEqual(self.post("/api/term/stop", {"id": "aaa"})[0], 409)

    def test_limit_from_config_tells_page_to_show_terminals(self):
        self.terms.max_terminals = 1
        self.started()
        self.board.scan()
        from agent_board.server import board_payload
        self.assertEqual(board_payload(self.board)["terminals"], {"open": 1, "max": 1})
        status, data = self.post("/api/term/new", {"path": self.tmp.name, "cols": 80, "rows": 24})
        self.assertEqual(status, 409)
        self.assertTrue(data["full"], data)
        self.assertIn("1 терміналів", data["error"])

    def archived_idle(self, status="idle"):
        (self.root / "status").write_text(status)
        key, r = self.started()
        self.board.scan()
        self.board.mark("aaa", "done")
        self.board.scan()
        return key

    def test_archived_idle_terminal_closes_itself(self):
        key = self.archived_idle()
        self.service.close_after = 60  # секунд
        self.service.tick(now=1000)
        self.service.tick(now=1059)
        self.assertEqual(len(self.terms.panes()), 1, "ще не минуло")
        self.service.tick(now=1061)
        self.assertEqual(len(self.terms.panes()), 1, "картка відкрита у вікні")
        self.post("/api/term/detach", {"key": key})
        self.service.tick(now=1062)
        self.assertEqual(self.terms.panes(), {})
        self.assertFalse(self.board.card("aaa")["term"])

    def test_busy_unarchived_or_disabled_are_kept(self):
        key = self.archived_idle("busy")
        self.post("/api/term/detach", {"key": key})
        self.service.close_after = 1
        self.service.tick(now=0)
        self.service.tick(now=100)
        self.assertEqual(len(self.terms.panes()), 1, "агент працює")
        (self.root / "claude/sessions").joinpath(next(f.name for f in (self.root / "claude/sessions").iterdir())).write_text(
            json.dumps({**json.loads(next((self.root / "claude/sessions").iterdir()).read_text()), "status": "idle"}))
        self.board.mark("aaa", "clear")
        self.board.scan()
        self.service.tick(now=200)
        self.service.tick(now=300)
        self.assertEqual(len(self.terms.panes()), 1, "картку повернули з архіву")
        self.board.mark("aaa", "snooze")
        self.board.scan()
        self.service.close_after = 0
        self.service.tick(now=400)
        self.service.tick(now=500)
        self.assertEqual(len(self.terms.panes()), 1, "вимкнено")
        self.service.close_after = 1
        self.service.tick(now=600)
        self.service.tick(now=700)
        self.assertEqual(self.terms.panes(), {})

    def test_live_elsewhere_and_takeover(self):
        p = subprocess.Popen(["sleep", "60"])
        self.addCleanup(lambda: (p.kill(), p.wait()))
        f = self.root / f"claude/sessions/{p.pid}.json"
        d = {"pid": p.pid, "sessionId": "aaa", "kind": "interactive", "status": "waiting", "procStart": proc_start(p.pid)}
        f.write_text(json.dumps(d))
        self.assertTrue(self.open()[1].get("live"))
        self.assertEqual(self.terms.panes(), {})
        status, data = self.post("/api/term/takeover", {"id": "aaa"})
        self.assertEqual((status, data["confirm"]), (409, True))
        f.write_text(json.dumps({**d, "status": "busy"}))
        self.assertEqual(self.post("/api/term/takeover", {"id": "aaa"})[1]["result"], "armed")
        self.assertTrue(self.board.card("aaa")["armed"])
        self.assertEqual(self.post("/api/term/takeover", {"id": "aaa", "cancel": True})[1]["result"], "cancelled")
        self.assertFalse(self.board.card("aaa")["armed"])
        f.write_text(json.dumps({**d, "status": "idle"}))
        self.assertEqual(self.post("/api/term/takeover", {"id": "aaa"})[1]["result"], "done")
        self.assertIsNotNone(p.wait(2))
        self.assertEqual(len(self.terms.panes()), 1)
        self.assertEqual(self.open(start=False)[0], 200)

    def test_rejects(self):
        self.assertEqual(self.open(sid="zzz")[0], 404)
        self.assertEqual(self.open(sid="gone")[0], 409)
        for cols in (0, "80", None, True, 10_000):
            self.assertEqual(self.post("/api/term/open", {"id": "aaa", "cols": cols, "rows": 24, "start": True})[0], 400)
        self.assertEqual(self.post("/api/term/nope", {})[0], 404)
        self.assertEqual(self.post("/api/term/card", {"id": "aaa"})[0], 404)
        self.assertEqual(self.post("/api/term/open", {"id": "aaa"}, Origin="http://evil.example")[0], 403)
        self.assertEqual(self.stream("нема".encode().hex()).status, 404)
        key = self.open()[1]["key"]
        self.assertEqual(self.stream(key, Origin="http://evil.example").status, 403)
        self.assertEqual(self.stream(key, Host="evil.example").status, 403)
        self.assertEqual(self.stream(key).status, 200)
        self.assertEqual(self.stream(key).status, 404)  # ключ одноразовий
        for data in (None, 5, "x" * 70_000):
            self.assertEqual(self.post("/api/term/input", {"key": key, "data": data})[0], 400)

    def test_dictate(self):
        ok = {"Content-Type": "application/octet-stream"}
        self.assertEqual(self.post("/api/dictate?lang=uk", raw=b"ab" * 50, **ok), (200, {"text": "100 байтів", "lang": "uk"}))
        self.assertEqual(self.post("/api/dictate", raw=b"ab")[0], 403)  # лише octet-stream
        self.assertEqual(self.post("/api/dictate", raw=b"ab", **ok, Origin="http://evil.example")[0], 403)
        self.assertEqual(self.post("/api/dictate", raw=b"", **ok)[0], 413)
        self.assertEqual(self._headers_only("/api/dictate", "application/octet-stream", 5_000_000), b"413")
        self.assertEqual(self._headers_only("/api/term/input", "application/json", 300_000), b"400")

    def _headers_only(self, path, ctype, length):
        """Завеликий розмір відхиляється до читання тіла."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as s:
            s.sendall(f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nOrigin: http://127.0.0.1:{self.port}\r\n"
                      f"Content-Type: {ctype}\r\nContent-Length: {length}\r\n\r\n".encode())
            return s.recv(100).split()[1]

    def test_board_tells_whether_dictation_is_available(self):
        def flag():
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            conn.request("GET", "/api/board")
            data = json.loads(conn.getresponse().read())
            conn.close()
            return data["dictation"]
        self.assertTrue(flag())  # сервіс сам під'єднується до дошки
        self.service.dictation.available = False  # лише цього екземпляра
        self.assertFalse(flag())

    def test_static(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        for path, want in (("/vendor/xterm.js", 200), ("/vendor/../index.html", 404), ("/vendor/LICENSE-xterm", 404)):
            conn.request("GET", path)
            r = conn.getresponse()
            r.read()
            self.assertEqual(r.status, want, path)
        conn.close()


class TermApiMacProcessesTest(TermApiTest):
    """Те саме, але процеси читає ps, як на macOS."""

    def setUp(self):
        patcher = mock.patch.object(system, "procs", system.Ps())
        patcher.start()
        self.addCleanup(patcher.stop)
        super().setUp()


if __name__ == "__main__":
    unittest.main()
