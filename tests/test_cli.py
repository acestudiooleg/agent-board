import contextlib
import io
import os
import plistlib
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agent_board import cli
from agent_board.config import Config, load_config
from agent_board.server import ROOT, manifest
from agent_board.system import proc_start


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        for p in (mock.patch.dict(os.environ, {"HOME": self.tmp.name}), mock.patch.object(cli, "DATA", self.home / "data")):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.cfg_path = self.home / "my.toml"
        self.cfg_path.write_text(f'[board]\nboard_name = "Мій Board"\nport = {self.port}\nclaude_dir = "{self.home}/claude"\n'
                                 f'db_path = "{self.home}/b.db"\nsummary_enabled = false\ndictation_model = "no/such"\n')
        self.cfg = load_config(self.cfg_path)

    def quiet(self, fn, *args, **kw):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = fn(*args, **kw)
        return code, out.getvalue() + err.getvalue()

    def test_start_status_stop(self):
        self.addCleanup(lambda: self.quiet(cli.stop, self.cfg_path, self.cfg))
        self.assertEqual(self.quiet(cli.status, self.cfg_path, self.cfg)[0], 1)
        code, text = self.quiet(cli.start, self.cfg_path, self.cfg)
        self.assertEqual(code, 0, text + (self.home / "data/my.log").read_text())
        for _ in range(50):  # pid-файл з'являється одразу після порту
            if cli.running_pid(self.cfg_path):
                break
            time.sleep(0.1)
        self.assertIn(str(cli.running_pid(self.cfg_path)), self.quiet(cli.status, self.cfg_path, self.cfg)[1])
        self.assertIn("уже зайнятий", self.quiet(cli.start, self.cfg_path, self.cfg)[1])
        self.assertEqual(self.quiet(cli.stop, self.cfg_path, self.cfg)[0], 0)
        self.assertFalse(cli.listening(self.cfg))
        self.assertIn("не працює", self.quiet(cli.stop, self.cfg_path, self.cfg)[1])

    def test_stop_never_touches_foreign_process(self):
        p = subprocess.Popen(["sleep", "30"])
        self.addCleanup(lambda: (p.kill(), p.wait()))
        pid_path = cli.pid_file(self.cfg_path)
        pid_path.parent.mkdir(parents=True)
        pid_path.write_text(f'{{"pid": {p.pid}, "start": "інший"}}')
        self.quiet(cli.stop, self.cfg_path, self.cfg)
        self.assertIsNone(p.poll())
        for broken in ("", "[]", '{"pid": "x"}', '{"pid": null, "start": null}'):
            pid_path.write_text(broken)
            self.assertIsNone(cli.running_pid(self.cfg_path), broken)

    def test_failed_start_is_reported(self):
        popen = lambda argv, **kw: subprocess.Popen([sys.executable, "-c", "raise SystemExit(3)"])
        code, text = self.quiet(cli.start, self.cfg_path, self.cfg, popen=popen)
        self.assertEqual(code, 1)
        self.assertIn("my.log", text)

    def test_open(self):
        calls, execs = [], []

        def run(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout=listing)
        with mock.patch.object(cli, "start", lambda *a: 0):
            listing = "0x1 0 x.Other h t\n0x2 0 127.0.0.1.AgentBoardBoard h Мій\n"
            cli.open_board(self.cfg_path, self.cfg, run=run, execvp=lambda *a: execs.append(a), mac=False)
            self.assertEqual((calls[-1], execs), (["wmctrl", "-i", "-a", "0x2"], []))
            listing = ""
            cli.open_board(self.cfg_path, self.cfg, run=run, execvp=lambda *a: execs.append(a), mac=False)
            self.assertIn(f"--app=http://127.0.0.1:{self.port}/", execs[0][1])
            cli.open_board(self.cfg_path, self.cfg, run=run, execvp=lambda *a: 1 / 0, mac=True)
            self.assertEqual(calls[-1], ["open", f"http://127.0.0.1:{self.port}/"])

    def test_mac_autostart_plist(self):
        calls = []
        code, _ = self.quiet(cli.autostart, self.cfg_path, self.cfg, mac=True,
                             run=lambda argv, **kw: calls.append(argv) or subprocess.CompletedProcess(argv, 0))
        path = self.home / "Library/LaunchAgents/io.github.agent-board.my.plist"
        data = plistlib.loads(path.read_bytes())
        self.assertEqual((code, data["ProgramArguments"], data["WorkingDirectory"]),
                         (0, [sys.executable, "-m", "agent_board", str(self.cfg_path)], str(ROOT)))
        self.assertTrue(data["AbandonProcessGroup"])  # tmux із розмовами переживає перезапуск дошки
        self.assertEqual(calls[-1], ["launchctl", "load", "-w", str(path)])
        self.assertEqual(self.quiet(cli.autostart, self.cfg_path, self.cfg, mac=False)[0], 1)

    def test_usage(self):
        for argv in ([], ["nope"], ["start", "a", "b"]):
            self.assertEqual(self.quiet(cli.main, argv)[0], 2)

    def test_manifest(self):
        m = manifest(Config(board_name="Мій", icon=Path("assets/a.svg")))
        self.assertEqual((m["name"], m["display"], m["icons"][0]["sizes"]), ("Мій", "standalone", "any"))
        self.assertNotIn("icons", manifest(Config()))


if __name__ == "__main__":
    unittest.main()
