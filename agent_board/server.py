import hashlib
import json
import logging
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .board import Board
from .config import Config, load_config
from .dictation import MAX_PCM, Dictation
from .jump import TERMINATOR_CONFIG, clean_env, open_in_terminator
from .store import Store
from .term_api import TermService
from .terminal import Terminals

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web" / "index.html"
ICON_TYPES = {".svg": "image/svg+xml", ".png": "image/png"}
log = logging.getLogger("agent_board")
STATIC = {f"/{n}": t for n, t in (
    ("terminal.js", "text/javascript"), ("themes.js", "text/javascript"), ("themes.css", "text/css"), ("vendor/xterm.js", "text/javascript"), ("vendor/addon-fit.js", "text/javascript"),
    ("vendor/addon-web-links.js", "text/javascript"), ("vendor/xterm.css", "text/css"))}
# Версія сторінки: відкриті вікна застосунку бачать, що після перезапуску дошки вона інша, і оновлюються самі.
BUILD = hashlib.sha256(b"".join(p.read_bytes() for p in [WEB, *(WEB.parent / n[1:] for n in STATIC)])).hexdigest()[:12]
MAX_BODY = 256 * 1024
MAX_SSE = 8
PING_SECONDS = 5  # закрита вкладка звільняє SSE-слот не пізніше ніж за стільки секунд


def board_payload(board: Board) -> dict:
    version, cards = board.snapshot()
    return {"version": version, "build": BUILD, "dictation": bool(board.term and board.term.dictation.available), "board": board.cfg.board_name, "planet": board.cfg.planet, "cards": cards,
            "haiku": board.summary_status()}


def is_web_url(url) -> bool:
    """Лише http(s) із хостом, без пробілів і керівних символів: іде аргументом у xdg-open."""
    if not isinstance(url, str) or len(url) > 4096 or any(ch.isspace() or not ch.isprintable() for ch in url):
        return False
    try:
        parts = urlsplit(url)
        return parts.scheme in ("http", "https") and bool(parts.hostname)
    except ValueError:
        return False


def make_handler(board: Board, cfg: Config, launcher=subprocess.Popen, terminator_config: Path = TERMINATOR_CONFIG,
                 term: TermService | None = None):
    sse_slots = threading.BoundedSemaphore(MAX_SSE)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _hosts(self) -> set[str]:
            port = self.server.server_address[1]
            return {f"127.0.0.1:{port}", f"localhost:{port}"}

        def _guard(self, post: bool, want: str = "application/json") -> bool:
            """Захист від DNS rebinding і CSRF: лише наш Host, а POST — лише JSON з нашого Origin."""
            if self.headers.get("Host") not in self._hosts():
                self._json(403, {"error": "forbidden host"})
                return False
            if post:
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                origin = self.headers.get("Origin")
                if ctype != want or origin is None or origin.split("://", 1)[-1] not in self._hosts():
                    self._json(403, {"error": "forbidden origin"})
                    return False
            return True

        def _body(self) -> dict | None:
            """None — тіло не прочитати (невалідний Content-Length чи JSON)."""
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if not 0 <= length <= MAX_BODY:
                    return None
                data = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                return None
            return data if isinstance(data, dict) else None

        def do_GET(self):
            if not self._guard(post=False):
                return
            path = urlsplit(self.path).path
            if path == "/":
                self._send(200, WEB.read_bytes(), "text/html; charset=utf-8")
            elif path in STATIC:
                self._send(200, (WEB.parent / path[1:]).read_bytes(), STATIC[path] + "; charset=utf-8")
            elif path == "/api/term/stream":
                self._term_stream()
            elif path == "/icon":
                self._icon()
            elif path == "/api/board":
                self._json(200, board_payload(board))
            elif path == "/api/projects":
                self._json(200, board.projects_overview())
            elif path == "/api/folders":
                self._json(200, {"folders": board.folders()})
            elif path == "/api/events":
                self._events()
            else:
                self._json(404, {"error": "not found"})

        def _icon(self):
            icon = cfg.icon
            if icon is not None and not icon.is_absolute():
                icon = ROOT / icon
            ctype = ICON_TYPES.get(icon.suffix.lower()) if icon else None
            if not ctype or not icon.is_file():
                return self._json(404, {"error": "no icon"})
            self._send(200, icon.read_bytes(), ctype)

        def _term_stream(self):
            origin = self.headers.get("Origin")
            if origin is not None and origin.split("://", 1)[-1] not in self._hosts():
                return self._json(403, {"error": "forbidden origin"})
            key = (parse_qs(urlsplit(self.path).query).get("key") or [""])[0]
            viewer = term.viewers.claim(key) if term else None
            if viewer is None:
                return self._json(404, {"error": "unknown key"})

            def write(data: bytes) -> None:
                self.wfile.write(data)
                self.wfile.flush()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
            except OSError:
                return term.viewers.close(viewer)
            term.stream(viewer, write)

        def _dictate(self):
            if not self._guard(post=True, want="application/octet-stream"):
                return
            length = self.headers.get("Content-Length") or ""
            if not term or not length.isdigit() or not 0 < int(length) <= MAX_PCM:
                return self._json(413, {"error": "порожній або завеликий запис"})
            pcm = self.rfile.read(int(length))
            lang = (parse_qs(urlsplit(self.path).query).get("lang") or ["auto"])[0]
            self._json(*term.dictate(pcm, lang))

        def _events(self):
            if not sse_slots.acquire(blocking=False):
                return self._json(503, {"error": "too many event streams"})
            try:
                self._stream()
            finally:
                sse_slots.release()

        def _stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            version = -1
            try:
                while True:
                    new = board.wait(version, PING_SECONDS)
                    if new == version:
                        self.wfile.write(b": ping\n\n")
                    else:
                        payload = board_payload(board)
                        version = payload["version"]
                        data = json.dumps(payload, ensure_ascii=False)
                        self.wfile.write(f"event: board\ndata: {data}\n\n".encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

        def do_POST(self):
            if urlsplit(self.path).path == "/api/dictate":
                return self._dictate()
            if not self._guard(post=True):
                return
            path = urlsplit(self.path).path
            body = self._body()
            if body is None:
                return self._json(400, {"error": "bad body"})
            sid = body.get("id")
            if path == "/api/mark":
                kind = body.get("kind")
                if not isinstance(sid, str) or kind not in ("done", "snooze", "clear"):
                    return self._json(400, {"error": "bad request"})
                try:
                    days = float(body.get("days") or 1)
                    until = float(body["until"]) if body.get("until") else None
                except (TypeError, ValueError):
                    return self._json(400, {"error": "bad days/until"})
                if not board.mark(sid, kind, days, until):
                    return self._json(404, {"error": "unknown session"})
                return self._json(200, {"ok": True})
            if path == "/api/jump":
                card = board.card(sid) if isinstance(sid, str) else None
                if not card:
                    return self._json(404, {"error": "unknown session"})
                return self._json(200, open_in_terminator(card, cfg.config_dir_env(), popen=launcher,
                                                                 config_path=terminator_config,
                                                                 attach=term.jump_info(sid) if term else None))
            if path.startswith("/api/term/") and term:
                return self._json(*term.post(path, body))
            if path == "/api/open":
                return self._open(body.get("url"))
            if path == "/api/projects":
                return self._projects(body)
            return self._json(404, {"error": "not found"})

        def _open(self, url):
            """Посилання з панелі — у вашому звичайному браузері: вікно дошки має окремий профіль Chrome без логінів."""
            if not is_web_url(url):
                return self._json(400, {"error": "bad url"})
            try:
                launcher(["xdg-open", url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, env=clean_env())
            except OSError as exc:
                return self._json(500, {"error": str(exc)})
            return self._json(200, {"ok": True})

        def _projects(self, body: dict):
            """{action: save, path, name, old_path?} | {action: delete, path} | {action: icon, name, icon}.
            Тека — абсолютна або з ~."""
            if body.get("action") == "icon":
                name, icon = body.get("name"), body.get("icon", "")
                if not isinstance(name, str) or not name.strip() or not isinstance(icon, str) or len(icon.strip()) > 8:
                    return self._json(400, {"error": "потрібні назва проєкту й значок (до 8 символів)"})
                board.set_project_icon(name.strip(), icon.strip())
                return self._json(200, board.projects_overview())
            def norm(value):
                if not isinstance(value, str) or not value.strip():
                    return None
                p = os.path.normpath(os.path.expanduser(value.strip()))
                return p if p.startswith("/") else None
            action, folder = body.get("action"), norm(body.get("path"))
            if not folder:
                return self._json(400, {"error": "тека має бути абсолютним шляхом або починатися з ~"})
            if action == "delete":
                board.delete_project(folder)
                return self._json(200, board.projects_overview())
            name = (body.get("name") or "").strip() if isinstance(body.get("name"), str) else ""
            if action != "save" or not name or len(name) > 80:
                return self._json(400, {"error": "потрібні назва (до 80 символів) і тека"})
            board.save_project(folder, name, norm(body.get("old_path")))
            return self._json(200, board.projects_overview())

    return Handler


def scan_loop(board: Board, interval: float, term: TermService | None = None) -> None:
    while True:
        try:
            board.scan()
            if term:
                term.tick()
        except Exception:
            log.exception("scan failed")
        time.sleep(interval)


def build(cfg: Config) -> tuple[Board, ThreadingHTTPServer]:
    """Дошка з уже виконаним першим сканом і сервер, який ще не слухає запити."""
    board = Board(cfg, Store(cfg.db_path))
    term = TermService(board, Terminals(f"agent-board-{cfg.port}", cfg.config_dir_env()),
                       Dictation(cfg.dictation_model, cfg.dictation_threads, cfg.dictation_languages))
    board.term = term
    check = threading.Thread(target=term.dictation.check, daemon=True)
    check.start()  # паралельно зі сканом
    board.scan()  # перший скан до старту HTTP, щоб сторінка одразу показала картки
    check.join()
    log.info("dictation: %s", "on" if term.dictation.available else "off (немає faster-whisper або моделі)")
    httpd = ThreadingHTTPServer((cfg.host, cfg.port), make_handler(board, cfg, term=term))
    httpd.daemon_threads = True
    return board, httpd


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg_path = Path(argv[0]) if argv else Path(os.environ.get("AGENT_BOARD_CONFIG") or ROOT / "config.toml")
    cfg = load_config(cfg_path)
    board, httpd = build(cfg)
    threading.Thread(target=scan_loop, args=(board, cfg.poll_seconds, board.term), daemon=True).start()
    log.info("agent-board «%s» on http://%s:%s (claude_dir=%s, haiku=%s)", cfg.board_name, cfg.host, cfg.port,
             cfg.claude_dir, "on" if cfg.summary_enabled else "off")
    httpd.serve_forever()
