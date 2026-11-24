import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_board import system
from agent_board.system import Ps, ProcFS, full_path, open_url_argv, same_start, shell_argv
from agent_board.terminal import KEEP_ERROR


class ProcessTest(unittest.TestCase):
    """Обидва способи на справжніх процесах: ps є і на Linux, тож гілка macOS перевіряється й тут."""
    BACKENDS = [Ps] + ([ProcFS] if Path("/proc/self/stat").exists() else [])

    def test_parent_and_start(self):
        for backend in self.BACKENDS:
            procs = backend()
            p = subprocess.Popen(["sleep", "30"])
            try:
                self.assertEqual(procs.parent(p.pid), os.getpid(), backend)
                self.assertEqual(procs.parent(os.getpid()), os.getppid())
                first = procs.start(p.pid)
                self.assertTrue(first)
                self.assertTrue(same_start(first, backend().start(p.pid)))
            finally:
                p.kill()
            self.assertIsNone(self._zombie_start(procs, p), backend)
            p.wait()
            self.assertIsNone(procs.start(p.pid))
            self.assertIsNone(procs.parent(2 ** 30))

    def _zombie_start(self, procs, p):
        os.waitid(os.P_PID, p.pid, os.WEXITED | os.WNOWAIT)  # завершився, але ще не прибраний
        return procs.start(p.pid)

    def test_start_has_the_format_claude_writes_on_mac(self):
        raw = subprocess.run(["sh", "-c", f"LC_ALL=C TZ=UTC ps -o lstart= -p {os.getpid()}"], capture_output=True, text=True).stdout.strip()
        self.assertTrue(same_start(Ps().start(os.getpid()), raw), raw)

    def test_new_child_is_found_despite_cached_table(self):
        procs = Ps()
        procs.parent(os.getpid())
        p = subprocess.Popen(["sleep", "30"])
        try:
            self.assertEqual(procs.parent(p.pid), os.getpid())
        finally:
            p.kill()
            p.wait()

    def test_same_start(self):
        self.assertTrue(same_start("Tue Nov  4 10:00:00 2026", "Tue Nov 4 10:00:00 2026"))
        for a, b in ((None, None), ("", ""), ("1", None), ("1", "2")):
            self.assertFalse(same_start(a, b))


class CommandTest(unittest.TestCase):
    def test_open_url(self):
        self.assertEqual(open_url_argv("https://e.com", mac=True), ["open", "https://e.com"])
        self.assertEqual(open_url_argv("https://e.com", mac=False), ["xdg-open", "https://e.com"])

    def test_shell(self):
        self.assertEqual(shell_argv("x", mac=False), ["bash", "-lc", "x"])
        self.assertEqual(shell_argv("x", mac=True, shell="/opt/homebrew/bin/fish"), ["/bin/zsh", "-ilc", "x"])
        self.assertEqual(shell_argv("x", mac=True, shell="/bin/bash"), ["/bin/bash", "-ilc", "x"])

    @unittest.skipUnless(shutil.which("zsh"), "потрібен zsh")
    def test_error_wrapper_works_in_zsh(self):
        for code, text in ((0, ""), (143, ""), (3, "з кодом 3")):
            r = subprocess.run(["zsh", "-fc", f"(exit {code}){KEEP_ERROR}"], input="\n", capture_output=True, text=True, timeout=10)
            self.assertEqual((text in r.stdout, r.stderr, r.returncode == 0 or code != 3), (True, "", True), (code, r))

    def test_full_path_keeps_order_and_adds_missing(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(system, "EXTRA_PATH", (d, "/nope/nope", "/b")):
            self.assertEqual(full_path("/a:/b"), f"/a:/b:{d}")
            self.assertEqual(full_path(""), d + (":/b" if Path("/b").is_dir() else ""))

    def test_no_external_window_on_mac(self):
        with mock.patch.object(system, "MAC", True):
            self.assertFalse(system.can_open_window())


if __name__ == "__main__":
    unittest.main()
