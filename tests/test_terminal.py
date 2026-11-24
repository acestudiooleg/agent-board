import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agent_board import attach
from agent_board.attach import Viewers
from agent_board.terminal import KEEP_ERROR, MAX_TERMINALS, TerminalError, Terminals, claude_argv, clean_text, terminal_env


def wait_for(check, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    return check()


class PureTest(unittest.TestCase):
    def test_env_and_argv(self):
        env = terminal_env({"PATH": "/bin", "TMUX": "x", "CLAUDECODE": "1", "CLAUDE_CONFIG_DIR": "/w", "PRAGMA_TAB": "1"})
        self.assertEqual(env, {"PATH": "/bin"})
        self.assertEqual(claude_argv("a-1", "/my dir")[2], "CLAUDE_CONFIG_DIR='/my dir' claude --resume a-1" + KEEP_ERROR)

    def test_new_chat_argv(self):
        self.assertEqual(claude_argv("a-1", new=True)[2], "claude --session-id a-1" + KEEP_ERROR)

    def test_clean_text_drops_escape(self):
        self.assertEqual(clean_text("а\x1b[201~\r\nб\x03\t\x9bв"), "а[201~\nб\tв")


@unittest.skipUnless(shutil.which("tmux"), "потрібен tmux")
class TerminalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.script = "stty -echo; printf 'ГОТОВО\\n'; while read -r x; do printf 'E:%s:E\\n' \"$x\"; done"
        self.t = Terminals(f"ab-test-{os.getpid()}-{self._testMethodName[-12:]}", command=lambda sid: ["bash", "-c", self.script])
        self.viewers = Viewers(self.t)

    def tearDown(self):
        self.viewers.close_all()
        self.t.tmux("kill-server", check=False)
        Path(os.environ.get("TMUX_TMPDIR", "/tmp"), f"tmux-{os.getuid()}", self.t.socket).unlink(missing_ok=True)
        self.tmp.cleanup()

    def screen(self, name):
        return self.t.tmux("capture-pane", "-p", "-t", f"={name}:", check=False)

    def read_until(self, v, text, timeout=5.0):
        buf, end = b"", time.monotonic() + timeout
        while text.encode() not in buf and time.monotonic() < end:
            chunk = v.read(0.2)
            if chunk is None:
                break
            buf += chunk
        self.assertIn(text, buf.decode(errors="replace"))

    def test_start_is_idempotent_and_found_by_pid(self):
        name = self.t.start("s1", self.tmp.name)
        self.assertEqual(self.t.start("s1", self.tmp.name), name)
        panes = self.t.panes()
        self.assertEqual(list(panes), [name])
        self.assertEqual(self.t.name_for_pid(panes[name]), name)
        self.assertIsNone(self.t.name_for_pid(os.getpid()))

    def test_bad_id_and_missing_dir(self):
        for sid, cwd in (("-x", self.tmp.name), ("a;b", self.tmp.name), ("a:b", self.tmp.name), ("ok", "/nope/nope"), ("ok", "rel"), ("ok", "")):
            with self.assertRaises(TerminalError):
                self.t.start(sid, cwd)
        self.assertEqual(self.t.panes(), {})

    def test_viewer_io_resize_and_close_keeps_session(self):
        name = self.t.start("s1", self.tmp.name)
        v = self.viewers.open("s1", name, 80, 24)
        self.read_until(v, "ГОТОВО")
        v.write("привіт\r".encode())
        self.read_until(v, "E:привіт:E")
        v.resize(101, 31)
        self.assertEqual(wait_for(lambda: (s := self.t.tmux("display-message", "-p", "-t", f"={name}:", "#{pane_width}x#{pane_height}").strip()) == "101x31" and s), "101x31")
        self.viewers.close(v)
        self.assertIsNotNone(v.proc.poll())
        self.assertIn(name, self.t.panes())
        # нова дошка з тим самим сокетом бачить ту саму розмову
        again = Terminals(self.t.socket, command=self.t.command)
        v2 = Viewers(again).open("s1", name, 80, 24)
        self.read_until(v2, "E:привіт:E")
        v2.close()

    def test_paste_multiline_is_one_block(self):
        self.script = r"""printf '\033[?2004h'; stty raw -echo; head -c 60 | od -An -c | tr -d ' \n'; sleep 30"""
        name = self.t.start("s1", self.tmp.name)
        time.sleep(0.3)
        self.t.paste(name, "line one\nline two")
        self.t.tmux("send-keys", "-t", f"={name}:", "-l", "z" * 60)
        text = wait_for(lambda: (s := self.screen(name).replace("\n", "")).strip() and s)
        self.assertIn(r"033[200~lineone\rlinetwo033[201~\rzz", text)

    def test_paste_cyrillic_and_specials(self):
        name = self.t.start("s1", self.tmp.name)
        self.assertTrue(wait_for(lambda: "ГОТОВО" in self.screen(name)))
        self.t.paste(name, "привіт $(id) \"x\" ; Enter")
        self.assertTrue(wait_for(lambda: 'E:привіт $(id) "x" ; Enter:E' in self.screen(name)), self.screen(name))
        with self.assertRaises(TerminalError):
            self.t.paste(name, " \n ")

    def test_error_stays_on_screen_until_enter(self):
        self.script = "(echo ПОМИЛКА; exit 3)" + KEEP_ERROR
        name = self.t.start("s1", self.tmp.name)
        self.assertTrue(wait_for(lambda: "ПОМИЛКА" in self.screen(name) and "з кодом 3" in self.screen(name)), self.screen(name))
        self.t.tmux("send-keys", "-t", f"={name}:", "Enter")
        self.assertTrue(wait_for(lambda: self.t.panes() == {}))

    def test_normal_exit_and_sigterm_close_terminal(self):
        for code in (0, 143):
            self.script = f"(exit {code})" + KEEP_ERROR
            self.t.start("s1", self.tmp.name)
            self.assertTrue(wait_for(lambda: self.t.panes() == {}))

    def test_normal_exit_ends_viewer(self):
        self.script = "printf 'ГОТОВО'; read -r x; exit 0"
        name = self.t.start("s1", self.tmp.name)
        v = self.viewers.open("s1", name, 80, 24)
        self.read_until(v, "ГОТОВО")
        time.sleep(0.2)
        v.write(b"\r")
        self.assertTrue(wait_for(lambda: v.read(0.1) is None))

    def test_limits_and_claims(self):
        name = self.t.start("s1", self.tmp.name)
        with mock.patch.object(attach, "MAX_VIEWERS", 1):
            v = self.viewers.open("s1", name, 80, 24)
            with self.assertRaises(TerminalError):
                self.viewers.open("s1", name, 80, 24)
            self.assertIs(self.viewers.claim(v.key), v)
            self.assertIsNone(self.viewers.claim(v.key))
            self.assertIsNone(self.viewers.claim("нема"))
        v2 = self.viewers.open("s1", name, 80, 24)
        v2.created -= attach.CLAIM_SECONDS + 1
        v3 = self.viewers.open("s1", name, 80, 24)
        self.assertIsNone(self.viewers.get(v2.key))
        self.assertTrue(v2.closed and not v3.closed)
        with mock.patch.object(Terminals, "panes", lambda s: {str(i): i for i in range(MAX_TERMINALS)}):
            with self.assertRaises(TerminalError):
                self.t.start("s2", self.tmp.name)

    def test_stop(self):
        name = self.t.start("s1", self.tmp.name)
        self.t.stop(name)
        self.assertEqual(self.t.panes(), {})
        self.assertEqual(self.t.client_pids(name), [])


if __name__ == "__main__":
    unittest.main()
