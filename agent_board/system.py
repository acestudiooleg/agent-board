"""Усе, що відрізняється між Linux і macOS: процеси, посилання, оболонка, PATH."""
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

MAC = sys.platform == "darwin"
# Служба входу (launchd, автозапуск) дає бідний PATH — додаємо місця, де зазвичай стоять claude і tmux.
EXTRA_PATH = ("~/.local/bin", "~/.claude/local", "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")


class ProcFS:
    """Linux: /proc."""

    def _stat(self, pid: int) -> list[str] | None:
        try:
            # назва процесу в дужках може містити пробіли й дужки
            return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            return None

    def parent(self, pid: int) -> int | None:
        fields = self._stat(pid)
        return int(fields[1]) if fields else None

    def start(self, pid: int) -> str | None:
        fields = self._stat(pid)
        return None if not fields or fields[0] in "ZX" else fields[19]


class Ps:
    """macOS: ps. Формат часу старту — той самий, який записує Claude Code (LC_ALL=C TZ=UTC ps -o lstart=)."""
    TTL = 1.0

    def __init__(self):
        self.table: dict[int, int] = {}
        self.read_at = float("-inf")
        self.lock = threading.Lock()

    def _ps(self, *args: str) -> str:
        try:
            return subprocess.run(["ps", *args], capture_output=True, text=True, timeout=5,
                                  env={**os.environ, "LC_ALL": "C", "TZ": "UTC"}).stdout
        except (OSError, subprocess.TimeoutExpired):
            return ""

    def parent(self, pid: int) -> int | None:
        # Одна таблиця на весь прохід предками, а не окремий ps на кожен крок.
        with self.lock:
            if time.monotonic() - self.read_at > self.TTL or pid not in self.table:
                rows = (line.split() for line in self._ps("-axo", "pid=,ppid=").splitlines())
                self.table = {int(r[0]): int(r[1]) for r in rows if len(r) == 2 and r[0].isdigit() and r[1].isdigit()}
                self.read_at = time.monotonic()
            return self.table.get(pid)

    def start(self, pid: int) -> str | None:
        parts = self._ps("-o", "stat=", "-o", "lstart=", "-p", str(pid)).strip().split(None, 1)
        return None if len(parts) < 2 or parts[0].startswith("Z") else parts[1].strip()


procs = Ps() if MAC else ProcFS()


def parent(pid: int) -> int | None:
    return procs.parent(pid)


def proc_start(pid: int) -> str | None:
    """Час старту процесу; None — процесу немає або він уже завершився (зомбі)."""
    return procs.start(pid)


def same_start(a: str | None, b: str | None) -> bool:
    return bool(a and b) and a.split() == b.split()  # ps вирівнює день місяця пробілами


def open_url_argv(url: str, mac: bool = MAC) -> list[str]:
    return ["open" if mac else "xdg-open", url]


def shell_argv(command: str, mac: bool = MAC, shell: str | None = None) -> list[str]:
    """Оболонка для запуску claude. На Mac — ваша інтерактивна: там PATH (nvm, brew) зазвичай задає ~/.zshrc."""
    if not mac:
        return ["bash", "-lc", command]
    shell = shell or os.environ.get("SHELL") or "/bin/zsh"
    if Path(shell).name not in ("zsh", "bash") or not Path(shell).exists():
        shell = "/bin/zsh"
    return [shell, "-ilc", command]


def full_path(path: str) -> str:
    """PATH, доповнений наявними типовими теками (порядок користувача не міняємо)."""
    have = [p for p in path.split(os.pathsep) if p]
    extra = (str(Path(p).expanduser()) for p in EXTRA_PATH)
    return os.pathsep.join(have + [p for p in extra if p not in have and Path(p).is_dir()])


def prepare_environ() -> None:
    os.environ["PATH"] = full_path(os.environ.get("PATH", ""))
    if MAC:
        os.environ.setdefault("LANG", "en_US.UTF-8")  # launchd не задає локалі, а tmux і claude потрібен UTF-8


def can_open_window() -> bool:
    """Чи є чим відкрити розмову окремим вікном («Перейти»). Без Terminator лишається термінал у картці."""
    return not MAC and shutil.which("terminator") is not None
