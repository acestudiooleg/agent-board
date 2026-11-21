import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_board.jump import (focus_live, open_in_terminator, resume_command, terminator_config,
                               terminator_config_from_user, window_for_pid)

CARD = {"id": "abc", "cwd": "/home/u/my dir", "live_status": None, "title": "Опція подарункового замовлення"}


class ResumeTest(unittest.TestCase):
    def test_quotes(self):
        self.assertEqual(resume_command("/home/u/my dir", "abc"), "cd '/home/u/my dir' && claude --resume abc")
        self.assertEqual(resume_command("/w", "a b;rm"), "cd /w && claude --resume 'a b;rm'")

    def test_work_account(self):
        self.assertEqual(resume_command("/w", "abc", "/home/u/.work"),
                         "cd /w && CLAUDE_CONFIG_DIR=/home/u/.work claude --resume abc")


class TerminatorTest(unittest.TestCase):
    def test_config_has_monokai_maximised_window_and_title(self):
        cfg = terminator_config('Тема "з лапками"', "cd /w && claude --resume abc", "/w")
        self.assertIn("  [[monokai]]", cfg)
        self.assertIn('background_color = "#272822"', cfg)
        self.assertIn("maximised = True", cfg)
        self.assertIn("profile = monokai", cfg)
        self.assertIn("title = 'Тема \"з лапками\"'", cfg)
        self.assertIn("""command = "bash -lc 'cd /w && claude --resume abc; exec bash'\"""", cfg)
        self.assertNotIn("Notebook", cfg)  # Notebook у layout вимагає ≥ 2 вкладок

    def test_opens_closed_session(self):
        started = []
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "t.config"
            r = open_in_terminator(CARD, "/home/u/.work", popen=lambda argv, **kw: started.append(argv), config_path=cfg,
                                   user_config=Path(tmp) / "немає")  # без вашого конфігу — запасний monokai
            text = cfg.read_text(encoding="utf-8")
        self.assertTrue(r["ok"])
        self.assertEqual(started[0], ["terminator", "-u", "-g", str(cfg), "-l", "agent-board"])
        self.assertIn("CLAUDE_CONFIG_DIR=/home/u/.work claude --resume abc", text)
        self.assertIn('title = "Опція подарункового замовлення"', text)

    def test_live_session_is_not_duplicated(self):
        no_windows = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "", "")
        r = open_in_terminator({**CARD, "live_status": "idle", "pid": 999999999},
                               popen=lambda *a, **k: self.fail("не можна відкривати"), run=no_windows)
        self.assertEqual((r["ok"], r["error"]), (False, "live"))
        self.assertIn("claude --resume abc", r["command"])

    def test_live_session_focuses_its_window(self):
        calls = []
        me, parent = os.getpid(), os.getppid()

        def run(argv, **kw):
            calls.append(argv)
            out = f"0x0aa00001  0 {parent} host Вікно батька\n" if argv[:2] == ["wmctrl", "-lp"] else ""
            return subprocess.CompletedProcess(argv, 0, out, "")

        self.assertEqual(window_for_pid(me, run), "0x0aa00001")  # вікно належить предку процесу
        r = open_in_terminator({**CARD, "live_status": "idle", "pid": me}, popen=lambda *a, **k: self.fail(), run=run)
        self.assertEqual((r["ok"], r["method"]), (True, "focus"))
        self.assertIn(["wmctrl", "-ia", "0x0aa00001"], calls)

    def test_option_like_cwd_is_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = open_in_terminator({**CARD, "cwd": "--evil"}, popen=lambda *a, **k: None, config_path=Path(tmp) / "c")
        self.assertNotIn("--evil", r["command"])

    def test_missing_terminator(self):
        def boom(argv, **kw):
            raise FileNotFoundError("terminator")
        with tempfile.TemporaryDirectory() as tmp:
            r = open_in_terminator(CARD, popen=boom, config_path=Path(tmp) / "c")
        self.assertFalse(r["ok"])


class UserConfigTest(unittest.TestCase):
    USER = """[global_config]
  ask_before_closing = never
[keybindings]
  copy = <Primary>c
[profiles]
  [[default]]
    background_type = transparent
  [[Molokai]]
    background_type = image
    background_image = /nonexistent/glass.jpg
    background_darkness = 0.91
    foreground_color = "#bbbbbb"
[layouts]
  [[default]]
    [[[child0]]]
      type = Window
      parent = ""
"""

    def test_tabs_with_user_profile(self):
        from configobj import ConfigObj
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "config"
            src.write_text(self.USER, encoding="utf-8")
            text = terminator_config_from_user("Тема", "cd /w && claude --resume abc", "/w", source=src)
            cfg = ConfigObj(text.splitlines())
        lay = cfg["layouts"]["agent-board"]
        self.assertEqual(lay["notebook0"]["labels"], ["Тема", "термінал"])
        self.assertEqual(lay["terminal0"]["profile"], "Molokai")
        self.assertIn("claude --resume abc", lay["terminal0"]["command"])
        self.assertEqual(lay["terminal1"]["directory"], "/w")
        self.assertEqual(cfg["profiles"]["Molokai"]["background_type"], "transparent")  # файлу фону немає
        self.assertEqual(cfg["keybindings"]["copy"], "<Primary>c")

    def test_missing_user_config(self):
        self.assertIsNone(terminator_config_from_user("T", "x", "/w", source=Path("/nonexistent/config")))


if __name__ == "__main__":
    unittest.main()
