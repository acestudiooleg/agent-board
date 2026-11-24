"""Розмови, що працюють у дошці: окремий сервер tmux. Вікно браузера лише дивиться на них."""
import re
import shlex
import subprocess
import threading
import time
from pathlib import Path

from .jump import clean_env, _parent

CONF = Path(__file__).with_name("tmux.conf")
PREFIX = "ab-"
SID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")
MAX_TERMINALS = 12
DROP = ("TMUX", "TMUX_PANE", "CLAUDE_CONFIG_DIR")
PASTE_ENTER_DELAY = 0.2  # Enter одразу після вставлення Claude вважає частиною вставленого


class TerminalError(Exception):
    pass


def terminal_env(environ=None) -> dict:
    return {k: v for k, v in clean_env(environ).items() if k not in DROP and not k.startswith("PRAGMA_")}


# Помилку запуску лишаємо на екрані. Опція tmux remain-on-exit не годиться: у 3.5a вона часто лишає
# мертвий термінал і після нормального виходу.
KEEP_ERROR = ('; rc=$?; case $rc in 0|129|130|143) ;; *) '
              'printf "\\n[Claude завершився з кодом %s — Enter закриє термінал]\\n" "$rc"; read -r _;; esac')


def claude_argv(sid: str, config_dir: str = "", new: bool = False) -> list[str]:
    flag = "--session-id" if new else "--resume"
    env = f"CLAUDE_CONFIG_DIR={shlex.quote(config_dir)} " if config_dir else ""
    return ["bash", "-lc", f"{env}claude {flag} {shlex.quote(sid)}{KEEP_ERROR}"]


def clean_text(text: str) -> str:
    """Без керівних символів (ESC міг би закрити bracketed paste), крім нового рядка й табуляції."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(ch for ch in text if ch in "\n\t" or (ch >= " " and not "\x7f" <= ch <= "\x9f"))


class Terminals:
    def __init__(self, socket: str, config_dir: str = "", command=None, new_command=None):
        self.socket = socket
        self.command = command or (lambda sid: claude_argv(sid, config_dir))
        self.new_command = new_command or command or (lambda sid: claude_argv(sid, config_dir, new=True))
        self.lock = threading.RLock()

    # ---------- tmux ----------
    def argv(self, *args: str) -> list[str]:
        return ["tmux", "-u", "-L", self.socket, "-f", str(CONF), *args]

    def tmux(self, *args: str, stdin: str | None = None, check: bool = True) -> str:
        try:
            r = subprocess.run(self.argv(*args), input=stdin, capture_output=True, text=True, timeout=10, env=terminal_env())
        except FileNotFoundError:
            raise TerminalError("не знайдено tmux") from None
        except subprocess.TimeoutExpired:
            raise TerminalError("tmux не відповідає") from None
        if check and r.returncode:
            raise TerminalError(r.stderr.strip() or "помилка tmux")
        return r.stdout if r.returncode == 0 else ""

    def panes(self) -> dict[str, int]:
        """Живі термінали: назва сесії tmux → pid процесу в ній."""
        out = {}
        for line in self.tmux("list-panes", "-a", "-F", "#{session_name} #{pane_pid}", check=False).splitlines():
            name, pid = line.split()
            if name.startswith(PREFIX):
                out[name] = int(pid)
        return out

    def name_for_pid(self, pid: int, panes: dict[str, int] | None = None) -> str | None:
        """У якому терміналі дошки працює процес (за предками)."""
        by_pid = {p: n for n, p in (self.panes() if panes is None else panes).items()}
        seen = set()
        while by_pid and pid and pid > 1 and pid not in seen:
            if pid in by_pid:
                return by_pid[pid]
            seen.add(pid)
            pid = _parent(pid)
        return None

    # ---------- дії ----------
    def start(self, sid: str, cwd: str, cols: int = 120, rows: int = 36, new: bool = False) -> str:
        if not SID_RE.fullmatch(sid):
            raise TerminalError("неприпустимий id розмови")
        if not cwd.startswith("/") or not Path(cwd).is_dir():
            raise TerminalError(f"теки розмови немає: {cwd or '—'}")
        name = PREFIX + sid
        with self.lock:
            panes = self.panes()
            if name in panes:
                return name
            if len(panes) >= MAX_TERMINALS:
                raise TerminalError(f"уже відкрито {MAX_TERMINALS} терміналів — завершіть якийсь")
            self.tmux("new-session", "-d", "-s", name, "-x", str(cols), "-y", str(rows), "-c", cwd, "--",
                      *(self.new_command if new else self.command)(sid))
        return name

    def stop(self, name: str) -> None:
        self.tmux("kill-session", "-t", f"={name}", check=False)

    def paste(self, name: str, text: str) -> None:
        text = clean_text(text)
        if not text.strip():
            raise TerminalError("порожній текст")
        target = f"={name}:"
        with self.lock:
            if self.tmux("display-message", "-p", "-t", target, "#{pane_in_mode}").strip() != "0":
                self.tmux("send-keys", "-X", "-t", target, "cancel", check=False)  # прокручений екран не приймає введення
            self.tmux("load-buffer", "-b", name, "-", stdin=text)
            self.tmux("paste-buffer", "-p", "-d", "-b", name, "-t", target)
            time.sleep(PASTE_ENTER_DELAY)
            self.tmux("send-keys", "-t", target, "Enter")

    def attach_argv(self, name: str) -> list[str]:
        return self.argv("attach-session", "-t", f"={name}")

    def client_pids(self, name: str) -> list[int]:
        out = self.tmux("list-clients", "-t", f"={name}", "-F", "#{client_pid}", check=False)
        return [int(x) for x in out.split()]
