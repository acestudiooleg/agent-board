import copy
import os
import shlex
import subprocess
from pathlib import Path

TERMINATOR_CONFIG = Path.home() / ".config/terminator/agent-board.config"
USER_TERMINATOR_CONFIG = Path.home() / ".config/terminator/config"  # лише читаємо
USER_PROFILE = "Molokai"  # ваш профіль з напівпрозорим «скляним» фоном

# Класична палітра Monokai
MONOKAI = {
    "background_color": "#272822",
    "foreground_color": "#f8f8f2",
    "cursor_bg_color": "#f8f8f0",
    "palette": "#272822:#f92672:#a6e22e:#f4bf75:#66d9ef:#ae81ff:#a1efe4:#f8f8f2:"
               "#75715e:#f92672:#a6e22e:#f4bf75:#66d9ef:#ae81ff:#a1efe4:#f9f8f5",
}


def resume_command(cwd: str, session_id: str, config_dir: str = "") -> str:
    """config_dir — для робочої дошки: розмова відкривається в тому самому акаунті (CLAUDE_CONFIG_DIR)."""
    env = f"CLAUDE_CONFIG_DIR={shlex.quote(config_dir)} " if config_dir else ""
    return f"cd {shlex.quote(cwd or str(Path.home()))} && {env}claude --resume {shlex.quote(session_id)}"


def _value(text: str) -> str:
    """Рядок для конфігу Terminator (ConfigObj): у лапках, без символів, що ламають формат."""
    text = text.replace("\n", " ").replace("\r", " ")
    if '"' not in text:
        return f'"{text}"'
    return "'" + text.replace("'", "’") + "'"


def terminator_config(title: str, command: str, cwd: str) -> str:
    """Окремий конфіг Terminator: профіль monokai і layout agent-board — вікно на весь екран із назвою картки."""
    shell = f"{command}; exec bash"
    lines = [
        "[global_config]",
        "  ask_before_closing = never",
        "  title_hide_sizetext = True",
        "[keybindings]",
        "  copy = <Primary>c",
        "  paste = <Primary>v",
        "[profiles]",
        "  [[default]]",
        *(f"    {k} = {_value(v)}" for k, v in MONOKAI.items()),
        "  [[monokai]]",
        *(f"    {k} = {_value(v)}" for k, v in MONOKAI.items()),
        "    scrollback_lines = 10000",
        "[layouts]",
        "  [[agent-board]]",
        "    [[[window0]]]",
        "      type = Window",
        '      parent = ""',
        "      maximised = True",
        f"      title = {_value(title)}",
        # Notebook у layout Terminator вимагає ≥ 2 вкладок, тож одна вкладка — це вікно з одним терміналом;
        # назва картки — у назві вікна й закріпленому заголовку терміналу (Claude Code його не перепише).
        "    [[[terminal0]]]",
        "      type = Terminal",
        "      parent = window0",
        f"      title = {_value(title)}",
        "      order = 0",
        "      profile = monokai",
        f"      directory = {_value(cwd or str(Path.home()))}",
        f"      command = {_value('bash -lc ' + shlex.quote(shell))}",
    ]
    return "\n".join(lines) + "\n"


def terminator_config_from_user(title: str, command: str, cwd: str, source: Path = USER_TERMINATOR_CONFIG,
                                profile: str = USER_PROFILE) -> str | None:
    """Ваш конфіг Terminator (профілі з фоном, плагіни, клавіші) + layout agent-board: вікно на весь екран,
    одна секція з вкладками — «назва картки» (claude --resume) і «термінал» (шел у теці проєкту).
    Notebook у layout Terminator вимагає ≥ 2 вкладок. None, якщо ваш конфіг не прочитати."""
    try:
        from configobj import ConfigObj
        user = ConfigObj(str(source), encoding="utf-8", file_error=True)
    except Exception:
        return None
    out = ConfigObj(encoding="utf-8")
    for section in ("global_config", "keybindings", "profiles", "plugins"):
        if section in user:
            out[section] = copy.deepcopy(user[section].dict())
    if profile not in out.get("profiles", {}):
        profile = "default"
    prof = out.get("profiles", {}).get(profile)
    if isinstance(prof, dict) and prof.get("background_type") == "image" and not Path(prof.get("background_image") or "").is_file():
        prof["background_type"] = "transparent"  # файлу фону немає — напівпрозорість замість суцільного кольору
    directory = cwd or str(Path.home())
    out["layouts"] = {"agent-board": {
        "window0": {"type": "Window", "parent": "", "order": "0", "maximised": "True", "fullscreen": "False", "title": title},
        "notebook0": {"type": "Notebook", "parent": "window0", "order": "0", "labels": [title, "термінал"]},
        "terminal0": {"type": "Terminal", "parent": "notebook0", "order": "0", "profile": profile, "directory": directory,
                      "title": title, "command": "bash -lc " + shlex.quote(f"{command}; exec bash")},
        "terminal1": {"type": "Terminal", "parent": "notebook0", "order": "1", "profile": profile, "directory": directory},
    }}
    lines = [x.decode("utf-8") if isinstance(x, bytes) else x for x in out.write()]
    return "\n".join(lines) + "\n"


def _parent(pid: int) -> int | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    # поле 4 після «(назва)», назва може містити пробіли й дужки
    return int(stat.rsplit(")", 1)[1].split()[1])


def window_for_pid(pid: int, run=subprocess.run) -> str | None:
    """Вікно X, якому належить процес або найближчий його предок (claude → шел → Terminator)."""
    try:
        listing = run(["wmctrl", "-lp"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    by_pid: dict[int, str] = {}
    for line in listing.splitlines():
        parts = line.split(None, 4)
        if len(parts) >= 3 and parts[2].isdigit():
            by_pid.setdefault(int(parts[2]), parts[0])
    seen = set()
    while pid and pid > 1 and pid not in seen:
        if pid in by_pid:
            return by_pid[pid]
        seen.add(pid)
        pid = _parent(pid)
    return None


def focus_live(card: dict, run=subprocess.run) -> str | None:
    """Вивести на передній план вікно, у якому вже працює розмова. Повертає id вікна або None."""
    pid = card.get("pid")
    window = window_for_pid(pid, run) if isinstance(pid, int) else None
    if window:
        try:
            if run(["wmctrl", "-ia", window], capture_output=True, timeout=5).returncode == 0:
                return window
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


def clean_env(environ=None) -> dict:
    """Середовище без змінних Claude Code (CLAUDECODE, CLAUDE_CODE_*…). Якщо дошку запустили з сесії Claude,
    вони успадковуються терміналом, і новий claude у ньому вважає себе дочірньою сесією: не з'являється на дошці."""
    environ = os.environ if environ is None else environ
    return {k: v for k, v in environ.items() if not (k.startswith("CLAUDE") and k != "CLAUDE_CONFIG_DIR")}


def open_in_terminator(card: dict, config_dir: str = "", popen=subprocess.Popen,
                       config_path: Path = TERMINATOR_CONFIG, user_config: Path = USER_TERMINATOR_CONFIG,
                       run=subprocess.run, attach: dict | None = None) -> dict:
    """«Перейти»: закрита розмова — Terminator на весь екран з вашим фоном, вкладка «назва картки» з resume.
    Жива — не відкриваємо (друга копія зламала б сесію), лише повертаємо команду."""
    raw_cwd = card.get("cwd") or ""
    cwd = raw_cwd if raw_cwd.startswith("/") else ""  # не даємо cwd стати опцією
    command = resume_command(cwd, card["id"], config_dir)
    if attach:
        # Працює в дошці: те саме tmux в окремому вікні, без другої копії Claude.
        for pid in attach["pids"]:
            if focus_live({"pid": pid}, run=run):
                return {"ok": True, "method": "focus", "command": attach["command"]}
        command = attach["command"]
    elif card.get("live_status"):
        # Жива розмова: другу копію не відкриваємо — фокусуємо вікно, де вона вже працює.
        if focus_live(card, run=run):
            return {"ok": True, "method": "focus", "command": command}
        return {"ok": False, "error": "live", "command": command}
    title = (card.get("title") or card["id"])[:60]
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        text = terminator_config_from_user(title, command, cwd, source=user_config) or terminator_config(title, command, cwd)
        config_path.write_text(text, encoding="utf-8")
        popen(["terminator", "-u", "-g", str(config_path), "-l", "agent-board"], stdin=subprocess.DEVNULL,
              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, env=clean_env())
    except OSError as exc:
        return {"ok": False, "error": str(exc), "command": command}
    return {"ok": True, "command": command}
