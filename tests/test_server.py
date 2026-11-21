import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from agent_board.board import Board
from agent_board.config import Config
from agent_board.server import MAX_SSE, build, make_handler
from agent_board.store import Store
from tests.helpers import assistant, human, write_lines


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        write_lines(root / "claude" / "projects" / "-p" / "aaa.jsonl",
                    [human("привіт", cwd="/home/u/server/x"), assistant("вітаю")])
        cfg = Config(claude_dir=root / "claude", db_path=root / "b.db", home=root, summary_enabled=False)
        self.board = Board(cfg, Store(cfg.db_path))
        self.board.scan()
        self.launched = []
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(
            self.board, cfg, launcher=lambda argv, **kw: self.launched.append(argv), terminator_config=root / "term.config"))
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.board.close()
        self.tmp.cleanup()

    def req(self, method, path, body=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}"} if method == "POST" else {}
        h.update(headers or {})
        data = raw if raw is not None else (json.dumps(body) if body is not None else None)
        conn.request(method, path, body=data, headers=h)
        r = conn.getresponse()
        payload = r.read()
        conn.close()
        return r.status, r.getheader("Content-Type"), payload

    def test_index_and_board(self):
        status, ctype, _ = self.req("GET", "/")
        self.assertEqual((status, "text/html" in ctype), (200, True))
        status, _, body = self.req("GET", "/api/board?t=1")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual([c["id"] for c in data["cards"]], ["aaa"])
        self.assertFalse(data["haiku"]["enabled"])
        self.assertEqual(data["board"], "Свої")
        self.assertEqual(data["planet"], "ringed")

    def test_mark_and_validation(self):
        self.assertEqual(self.req("POST", "/api/mark", {"id": "aaa", "kind": "done"})[0], 200)
        self.assertEqual(self.board.card("aaa")["state"], "done")
        self.assertEqual(self.req("POST", "/api/mark", {"id": "aaa", "kind": "nope"})[0], 400)
        self.assertEqual(self.req("POST", "/api/mark", {"id": "aaa", "kind": "snooze", "days": "x"})[0], 400)
        self.assertEqual(self.req("POST", "/api/mark", {"id": "zzz", "kind": "done"})[0], 404)

    def test_jump(self):
        status, _, body = self.req("POST", "/api/jump", {"id": "aaa"})
        self.assertEqual(status, 200)
        self.assertIn("claude --resume aaa", json.loads(body)["command"])
        self.assertEqual(self.launched[0][:2], ["terminator", "-u"])  # справжній Terminator у тестах не запускається
        self.assertEqual(self.req("POST", "/api/jump", {"id": "zzz"})[0], 404)

    def test_sse_sends_board_event(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/api/events")
        r = conn.getresponse()
        self.assertIn("text/event-stream", r.getheader("Content-Type"))
        self.assertEqual(r.fp.readline().decode().strip(), "event: board")
        data = json.loads(r.fp.readline().decode()[len("data: "):])
        self.assertEqual(data["cards"][0]["id"], "aaa")
        conn.close()

    def test_foreign_host_is_rejected(self):
        self.assertEqual(self.req("GET", "/api/board", headers={"Host": "evil.example:8091"})[0], 403)

    def test_post_guards(self):
        body = json.dumps({"id": "aaa", "kind": "done"})
        self.assertEqual(self.req("POST", "/api/mark", raw=body, headers={"Content-Type": "text/plain"})[0], 403)
        self.assertEqual(self.req("POST", "/api/mark", raw=body, headers={"Origin": "http://evil.example"})[0], 403)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)  # POST без Origin
        conn.request("POST", "/api/mark", body=body, headers={"Content-Type": "application/json"})
        self.assertEqual(conn.getresponse().status, 403)
        conn.close()
        self.assertNotEqual(self.board.card("aaa")["state"], "done")

    def test_bad_content_length(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.putrequest("POST", "/api/mark")
        for k, v in (("Content-Type", "application/json"), ("Origin", f"http://127.0.0.1:{self.port}"), ("Content-Length", "abc")):
            conn.putheader(k, v)
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()

    def test_sse_connection_cap(self):
        conns = []
        try:
            for _ in range(MAX_SSE):
                conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
                conn.request("GET", "/api/events")
                r = conn.getresponse()
                self.assertEqual(r.status, 200)
                r.fp.readline()  # дочекатися, поки потік зайняв слот
                conns.append(conn)
            extra = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            extra.request("GET", "/api/events")
            self.assertEqual(extra.getresponse().status, 503)  # лише статус: тіло потоку не читаємо
            conns.append(extra)
        finally:
            for conn in conns:
                conn.close()

    def test_build_scans_before_serving(self):
        cfg = Config(claude_dir=self.board.cfg.claude_dir, db_path=self.board.cfg.db_path.with_name("b2.db"),
                     home=self.board.cfg.home, summary_enabled=False, port=0)
        board, httpd = build(cfg)
        try:
            version, cards = board.snapshot()
            self.assertGreaterEqual(version, 1)
            self.assertEqual([c["id"] for c in cards], ["aaa"])
        finally:
            httpd.server_close()
            board.close()

    def test_icon(self):
        self.assertEqual(self.req("GET", "/icon")[0], 404)  # значок не задано
        self.board.cfg.icon = Path("assets/a-mark.svg")  # відносно кореня репозиторію
        status, ctype, body = self.req("GET", "/icon")
        self.assertEqual((status, ctype), (200, "image/svg+xml"))
        self.assertIn(b"<svg", body)
        self.board.cfg.icon = Path(self.tmp.name) / "x.txt"
        self.assertEqual(self.req("GET", "/icon")[0], 404)

    def test_resume_in_card(self):
        resume = self.board.card("aaa")["resume"]
        self.assertTrue(resume.startswith("cd /home/u/server/x && CLAUDE_CONFIG_DIR="))  # тестова тека — не ~/.claude
        self.assertTrue(resume.endswith("claude --resume aaa"))

    def test_projects_api(self):
        status, _, body = self.req("GET", "/api/projects")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["user"], [])
        status, _, body = self.req("POST", "/api/projects", {"action": "save", "path": "/home/u/server/x", "name": "Ікс"})
        data = json.loads(body)
        self.assertEqual((status, data["user"][0]["name"], data["user"][0]["count"]), (200, "Ікс", 1))
        self.assertEqual(self.board.card("aaa")["project"], "Ікс")  # розмова з цієї теки підтягнулась
        self.assertEqual(self.req("POST", "/api/projects", {"action": "save", "path": "relative", "name": "x"})[0], 400)
        self.assertEqual(self.req("POST", "/api/projects", {"action": "save", "path": "/a", "name": ""})[0], 400)
        status, _, body = self.req("POST", "/api/projects", {"action": "delete", "path": "/home/u/server/x"})
        self.assertEqual(json.loads(body)["user"], [])
        self.assertEqual(self.board.card("aaa")["project"], "x")

    def test_project_icon_api(self):
        status, _, body = self.req("POST", "/api/projects", {"action": "icon", "name": "x", "icon": "🚀"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["detected"][0]["icon"], "🚀")
        self.assertEqual(self.board.card("aaa")["project_icon"], "🚀")
        self.assertEqual(self.req("POST", "/api/projects", {"action": "icon", "name": "x", "icon": "x" * 9})[0], 400)
        self.req("POST", "/api/projects", {"action": "icon", "name": "x", "icon": ""})
        self.assertIsNone(self.board.card("aaa")["project_icon"])

    def test_unknown_path(self):
        self.assertEqual(self.req("GET", "/nope")[0], 404)


if __name__ == "__main__":
    unittest.main()
