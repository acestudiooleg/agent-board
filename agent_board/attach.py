"""Глядач термінала: псевдотермінал із tmux attach. Закриття глядача не чіпає саму розмову."""
import fcntl
import os
import pty
import secrets
import select
import signal
import struct
import subprocess
import termios
import threading
import time

from .terminal import TerminalError, Terminals, terminal_env

MAX_VIEWERS = 8
CLAIM_SECONDS = 15  # за стільки сторінка має під'єднати потік, інакше глядача прибираємо


def size_ok(cols, rows) -> bool:
    return all(isinstance(x, int) and not isinstance(x, bool) and 2 <= x <= 500 for x in (cols, rows))


class Viewer:
    def __init__(self, sid: str, name: str, argv: list[str], cols: int, rows: int):
        self.key, self.sid, self.name = secrets.token_urlsafe(24), sid, name
        self.created, self.claimed, self.closed = time.monotonic(), False, False
        self.lock = threading.Lock()
        self.fd, slave = pty.openpty()
        try:
            self._winsize(cols, rows)
            self.proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                                         env={**terminal_env(), "TERM": "xterm-256color"})
        except OSError as exc:
            os.close(self.fd)
            raise TerminalError(str(exc)) from None
        finally:
            os.close(slave)
        os.set_blocking(self.fd, False)

    def _winsize(self, cols: int, rows: int) -> None:
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def resize(self, cols: int, rows: int) -> None:
        with self.lock:
            if not self.closed:
                self._winsize(cols, rows)
                try:
                    os.kill(self.proc.pid, signal.SIGWINCH)  # без керівного термінала ядро саме не сповістить
                except ProcessLookupError:
                    pass

    def read(self, timeout: float) -> bytes | None:
        """Вивід; b"" — тиша за timeout; None — термінал закрито."""
        try:
            ready, _, _ = select.select([self.fd], [], [], timeout)
            return (os.read(self.fd, 65536) or None) if ready else b""
        except BlockingIOError:
            return b""
        except (OSError, ValueError):
            return None

    def write(self, data: bytes, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        with self.lock:
            while data and not self.closed:
                try:
                    data = data[os.write(self.fd, data):]
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        raise TerminalError("термінал не приймає введення") from None
                    select.select([], [self.fd], [], 0.2)
                except OSError:
                    raise TerminalError("термінал закрито") from None
            if self.closed:
                raise TerminalError("термінал закрито")

    def close(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.closed = True
            os.close(self.fd)
        if self.proc.poll() is None:
            self.proc.terminate()
        try:
            self.proc.wait(3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(3)


class Viewers:
    def __init__(self, terminals: Terminals):
        self.terminals, self.by_key, self.lock = terminals, {}, threading.Lock()

    def open(self, sid: str, name: str, cols: int, rows: int) -> Viewer:
        with self.lock:
            stale = [v for v in self.by_key.values() if not v.claimed and time.monotonic() - v.created > CLAIM_SECONDS]
            for v in stale:
                del self.by_key[v.key]
            full = len(self.by_key) >= MAX_VIEWERS
            if not full:
                v = Viewer(sid, name, self.terminals.attach_argv(name), cols, rows)
                self.by_key[v.key] = v
        for old in stale:
            old.close()
        if full:
            raise TerminalError(f"відкрито вже {MAX_VIEWERS} терміналів у вікнах — закрийте якесь")
        return v

    def get(self, key) -> Viewer | None:
        return self.by_key.get(key) if isinstance(key, str) else None

    def claim(self, key) -> Viewer | None:
        with self.lock:
            v = self.get(key)
            if v is None or v.claimed:
                return None
            v.claimed = True
            return v

    def close(self, viewer: Viewer) -> None:
        with self.lock:
            self.by_key.pop(viewer.key, None)
        viewer.close()

    def close_all(self) -> None:
        for v in list(self.by_key.values()):
            self.close(v)
