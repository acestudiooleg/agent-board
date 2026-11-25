import http.client
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from agent_board import editor
from agent_board.board import Board
from agent_board.config import Config
from agent_board.server import board_payload, make_handler
from agent_board.store import Store
from tests.helpers import assistant, human, write_lines


class CommandTest(unittest.TestCase):
    def test_code_from_path(self):
        with mock.patch("shutil.which", return_value="/usr/bin/code"):
            self.assertEqual(editor.command(mac=False), "/usr/bin/code")

    def test_absent_on_linux(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertIsNone(editor.command(mac=False))

    def test_mac_app_without_shell_command(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("shutil.which", return_value=None):
            app = Path(tmp) / "code"
            app.write_text("#!/bin/sh\n")
            with mock.patch.object(editor, "MAC_APPS", (str(Path(tmp) / "none"), str(app))):
                self.assertIsNone(editor.command(mac=True))  # не виконуваний
                app.chmod(0o755)
                self.assertEqual(editor.command(mac=True), str(app))
                self.assertIsNone(editor.command(mac=False))


class FolderTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def folder(self, cwd, root, worktree=None):
        return editor.folder({"cwd": str(cwd), "project_root": str(root) if root else "", "worktree": worktree})

    def mkdir(self, path):
        (self.root / path).mkdir(parents=True, exist_ok=True)
        return self.root / path

    def test_project_root_rather_than_subfolder(self):
        sub = self.mkdir("app/src")
        self.assertEqual(self.folder(sub, self.root / "app"), str(self.root / "app"))

    def test_project_chosen_by_files_wins_over_start_folder(self):
        start, other = self.mkdir("start"), self.mkdir("other")
        (start / ".git").write_text("gitdir: x\n")
        self.assertEqual(self.folder(start, other), str(other))

    def test_worktree_opens_itself(self):
        main, wt = self.mkdir("main"), self.mkdir("wt/T-1/src")
        (self.root / "wt/T-1/.git").write_text("gitdir: x\n")
        self.assertEqual(self.folder(wt, main, "T-1"), str(self.root / "wt/T-1"))

    def test_worktree_inside_user_project(self):
        wt = self.mkdir("all/.worktrees/T-1/src")
        (self.root / "all/.worktrees/T-1/.git").write_text("gitdir: x\n")
        self.assertEqual(self.folder(wt, self.root / "all"), str(self.root / "all/.worktrees/T-1"))

    def test_worktree_without_git_file_opens_cwd(self):
        self.assertEqual(self.folder(self.mkdir("rule/bar"), self.mkdir("main"), "bar"), str(self.root / "rule/bar"))

    def test_removed_folders(self):
        main = self.mkdir("main")
        self.assertEqual(self.folder(self.root / "gone", main, "gone"), str(main))
        self.assertEqual(self.folder(main, self.root / "gone"), str(main))
        self.assertIsNone(self.folder(self.root / "gone", self.root / "gone"))

    def test_relative_or_empty_is_refused(self):
        self.assertIsNone(self.folder("", ""))
        self.assertIsNone(self.folder(".", "."))


class ApiTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.project = self.root / "server/x"
        self.project.mkdir(parents=True)
        write_lines(self.root / "claude/projects/-p/aaa.jsonl", [human("привіт", cwd=str(self.project)), assistant("вітаю")])
        write_lines(self.root / "claude/projects/-p/bbb.jsonl", [human("привіт", cwd="/nonexistent/y"), assistant("вітаю")])
        cfg = Config(claude_dir=self.root / "claude", db_path=self.root / "b.db", home=self.root, summary_enabled=False)
        self.board = Board(cfg, Store(cfg.db_path))
        self.addCleanup(self.board.close)
        self.board.scan()
        self.launched = []
        self.fail = None
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.board, cfg, launcher=self.launch))
        httpd.daemon_threads = True
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        self.port = httpd.server_address[1]
        self.command = "/usr/bin/code"
        patch = mock.patch.object(editor, "command", lambda: self.command)
        patch.start()
        self.addCleanup(patch.stop)

    def launch(self, argv, **kw):
        if self.fail:
            raise self.fail
        self.launched.append((argv, kw))

    def post(self, body):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", "/api/editor", body=json.dumps(body),
                     headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}"})
        r = conn.getresponse()
        data = json.loads(r.read())
        conn.close()
        return r.status, data

    def test_opens_card_project(self):
        with mock.patch.dict(os.environ, {"CLAUDECODE": "1"}):
            self.assertEqual(self.post({"id": "aaa"}), (200, {"ok": True, "path": str(self.project)}))
        argv, kw = self.launched[0]
        self.assertEqual(argv, ["/usr/bin/code", str(self.project)])
        self.assertTrue(kw["start_new_session"])
        self.assertNotIn("CLAUDECODE", kw["env"])

    def test_opens_known_folder(self):
        self.assertEqual(self.post({"path": str(self.project)})[0], 200)
        self.assertEqual(self.launched[0][0], ["/usr/bin/code", str(self.project)])

    def test_refuses_anything_else(self):
        for body in ({"path": "/etc"}, {"path": str(self.project / "..")}, {"path": "--help"}, {"path": 5}, {}, {"id": "zzz"}, {"id": 5},
                     {"id": "zzz", "path": "/etc"}):
            self.assertEqual(self.post(body)[0], 404, body)
        self.assertEqual(self.post({"id": "bbb"})[0], 404)  # теки вже немає
        self.assertEqual(self.launched, [])

    def test_editor_missing_or_broken(self):
        self.fail = OSError("boom")
        self.assertEqual(self.post({"id": "aaa"}), (500, {"error": "boom"}))
        self.command = None
        self.assertEqual(self.post({"id": "aaa"})[0], 501)
        self.assertEqual(self.launched, [])

    def test_page_knows_if_editor_is_installed(self):
        self.assertTrue(board_payload(self.board)["editor"])
        self.command = None
        self.assertFalse(board_payload(self.board)["editor"])


if __name__ == "__main__":
    unittest.main()
