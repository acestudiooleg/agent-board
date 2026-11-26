"""agent-board start | stop | restart | status | open | autostart  [config.toml]"""
import json
import os
import plistlib
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

if sys.version_info < (3, 11):
    sys.exit("Потрібен Python 3.11 або новіший (на Mac: brew install python).")

from . import system
from .config import Config, load_config
from .jump import clean_env
from .server import ROOT, pid_file

DATA = Path.home() / ".local/share/agent-board"
START_SECONDS = 90


def listening(cfg: Config) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex((cfg.host, cfg.port)) == 0


def running_pid(cfg_path: Path) -> int | None:
    try:
        d = json.loads(pid_file(cfg_path).read_text())
        return d["pid"] if system.same_start(system.proc_start(d["pid"]), d["start"]) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def start(cfg_path: Path, cfg: Config, popen=subprocess.Popen, seconds: float = START_SECONDS) -> int:
    if listening(cfg):
        print(f"порт {cfg.port} уже зайнятий — дошка працює")
        return 0
    DATA.mkdir(parents=True, exist_ok=True)
    env = clean_env()  # без змінних сесії Claude: інакше термінали від дошки їх успадкують
    env["PATH"] = system.full_path(env.get("PATH", ""))
    if not system.MAC:
        env.setdefault("DISPLAY", ":0")
    log = DATA / f"{cfg_path.stem}.log"
    with open(log, "ab") as out:
        proc = popen([sys.executable, "-m", "agent_board", str(cfg_path)], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                     stdout=out, stderr=out, start_new_session=True)
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if proc.poll() is not None:
            print(f"дошка не запустилась, лог: {log}", file=sys.stderr)
            return 1
        if listening(cfg):
            print(f"запущено: http://{cfg.host}:{cfg.port} (лог {log})")
            return 0
        time.sleep(0.2)
    print(f"дошка ще запускається, лог: {log}")
    return 0


def stop(cfg_path: Path, cfg: Config, seconds: float = 10) -> int:
    pid = running_pid(cfg_path)
    if pid is None:
        if listening(cfg):
            print(f"порт {cfg.port} зайнято, але це не дошка, запущена цією командою — зупиніть її вручну", file=sys.stderr)
            return 1
        print("дошка не працює")
        return 0
    start_mark = system.proc_start(pid)
    os.kill(pid, signal.SIGTERM)
    end = time.monotonic() + seconds
    # Головний потік стає зомбі раніше за інші, а ті ще тримають порт: чекаємо й на порт.
    while system.same_start(system.proc_start(pid), start_mark) or listening(cfg):
        if time.monotonic() > end:
            print("дошка не зупинилась за відведений час", file=sys.stderr)
            return 1
        time.sleep(0.1)
    pid_file(cfg_path).unlink(missing_ok=True)
    print("зупинено (розмови в терміналах працюють далі)")
    return 0


def window_class(cfg: Config) -> str:
    return "AgentBoard" + (re.sub(r"[^A-Za-z0-9]", "", cfg.board_name) or "Board")


def open_board(cfg_path: Path, cfg: Config, run=subprocess.run, execvp=os.execvp, mac: bool = system.MAC) -> int:
    """Linux: вікно Chrome --app (відкрите — лише фокусуємо). Mac: типовий браузер або встановлений із нього застосунок."""
    if code := start(cfg_path, cfg):
        return code
    url, cls = f"http://{cfg.host}:{cfg.port}/", window_class(cfg)
    if mac:
        return run(["open", url]).returncode
    try:
        for line in run(["wmctrl", "-lx"], capture_output=True, text=True).stdout.splitlines():
            parts = line.split()
            if len(parts) > 2 and parts[2].endswith("." + cls):
                return run(["wmctrl", "-i", "-a", parts[0]]).returncode
    except OSError:
        pass
    try:
        execvp("google-chrome", ["google-chrome", f"--app={url}", f"--class={cls}", f"--user-data-dir={DATA}/chrome-{cls.removeprefix('AgentBoard')}",
                                 "--no-first-run", "--no-default-browser-check"])
    except OSError:
        return run(["xdg-open", url]).returncode
    return 0


def agent_plist(cfg_path: Path) -> tuple[Path, dict]:
    label = f"io.github.agent-board.{cfg_path.stem}"
    log = str(DATA / f"{cfg_path.stem}.log")
    return Path.home() / "Library/LaunchAgents" / f"{label}.plist", {
        "Label": label, "ProgramArguments": [sys.executable, "-m", "agent_board", str(cfg_path)], "WorkingDirectory": str(ROOT),
        "RunAtLoad": True, "KeepAlive": {"SuccessfulExit": False}, "ProcessType": "Interactive",
        # Без цього launchd, зупиняючи дошку, вбив би й сервер tmux із вашими розмовами.
        "AbandonProcessGroup": True,
        "EnvironmentVariables": {"PATH": system.full_path(os.environ.get("PATH", ""))}, "StandardOutPath": log, "StandardErrorPath": log}


def autostart(cfg_path: Path, cfg: Config, run=subprocess.run, mac: bool = system.MAC) -> int:
    if not mac:
        print("На Linux: systemd (agent-board.service) або ./run.sh у ~/.config/autostart — див. README")
        return 1
    path, data = agent_plist(cfg_path)
    DATA.mkdir(parents=True, exist_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    run(["launchctl", "unload", str(path)], capture_output=True)
    path.write_bytes(plistlib.dumps(data))
    code = run(["launchctl", "load", "-w", str(path)]).returncode
    print(f"автозапуск увімкнено: {path}\nвимкнути: launchctl unload -w '{path}' && rm '{path}'")
    return code


def status(cfg_path: Path, cfg: Config) -> int:
    up = listening(cfg)
    print(f"{cfg.board_name}: {'працює' if up else 'не працює'} (порт {cfg.port}, pid {running_pid(cfg_path) or '—'})")
    return 0 if up else 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    commands = {"start": start, "stop": stop, "open": open_board, "autostart": autostart, "status": status,
                "restart": lambda p, c: stop(p, c) or start(p, c)}
    if not argv or argv[0] not in commands or len(argv) > 2:
        print(__doc__)
        return 2
    cfg_path = Path(argv[1] if len(argv) > 1 else "config.toml").expanduser()
    cfg_path = cfg_path if cfg_path.is_absolute() else ROOT / cfg_path
    return commands[argv[0]](cfg_path, load_config(cfg_path))


if __name__ == "__main__":
    sys.exit(main())
