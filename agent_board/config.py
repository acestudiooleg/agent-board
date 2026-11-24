import logging
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

FALLBACK_WORKTREE_RULES = [r"/\.worktrees/(?P<project>[^/]+)/(?P<worktree>[^/]+)"]

# Ізольований виклик: без user settings (хуків), плагінів, MCP і скілів; транскрипт не зберігається.
SUMMARY_COMMAND = [
    "claude", "-p", "--model", "haiku", "--output-format", "json",
    "--no-session-persistence", "--tools", "",
    "--setting-sources", "", "--strict-mcp-config", "--disable-slash-commands",
]

SIMPLE_KEYS = (
    "board_name", "planet", "host", "port", "poll_seconds", "max_age_days", "summary_enabled", "summary_quiet_seconds",
    "summary_concurrency", "summary_per_hour", "summary_retry_seconds", "summary_backoff_max_seconds",
    "summary_breaker_failures", "summary_breaker_seconds", "summary_timeout_seconds",
    "summary_command", "summary_exclude_families", "worktree_rules",
    "dictation_model", "dictation_threads", "dictation_languages",
)
PATH_KEYS = ("claude_dir", "db_path", "haiku_cwd", "icon")
DEFAULT_CLAUDE_DIR = Path.home() / ".claude"
log = logging.getLogger("agent_board")


@dataclass
class Family:
    name: str
    icon: str
    projects: list[str]


@dataclass
class Config:
    board_name: str = "Свої"
    icon: Path | None = None  # значок у заголовку й favicon; відносний шлях — від кореня репозиторію
    planet: str = "ringed"  # велика планета на фоні: "ringed" (блакитна з кільцем) або "mars"
    claude_dir: Path = field(default_factory=lambda: DEFAULT_CLAUDE_DIR)
    db_path: Path = field(default_factory=lambda: Path.home() / ".local/share/agent-board/board.db")
    haiku_cwd: Path = field(default_factory=lambda: Path.home() / ".local/share/agent-board/haiku")
    home: Path = field(default_factory=Path.home)
    host: str = "127.0.0.1"
    port: int = 8091
    poll_seconds: float = 5.0
    max_age_days: int = 14
    summary_enabled: bool = True
    summary_quiet_seconds: int = 30
    summary_concurrency: int = 2
    summary_per_hour: int = 20
    summary_retry_seconds: int = 600
    summary_backoff_max_seconds: int = 21600
    summary_breaker_failures: int = 5
    summary_breaker_seconds: int = 3600
    summary_timeout_seconds: int = 120
    dictation_model: str = "small"  # модель faster-whisper з локального кешу
    dictation_threads: int = 8
    dictation_languages: list[str] = field(default_factory=lambda: ["uk", "ru", "en"])  # між чим вибирає «авто»
    summary_command: list[str] = field(default_factory=lambda: list(SUMMARY_COMMAND))
    summary_exclude_families: list[str] = field(default_factory=list)
    summary_exclude_paths: list[str] = field(default_factory=list)
    worktree_rules: list[str] = field(default_factory=lambda: list(FALLBACK_WORKTREE_RULES))
    project_paths: list[tuple[str, str]] = field(default_factory=list)
    families: list[Family] = field(default_factory=list)

    def config_dir_env(self) -> str:
        """Значення CLAUDE_CONFIG_DIR для команди resume; порожньо для типової ~/.claude."""
        return "" if self.claude_dir == DEFAULT_CLAUDE_DIR else str(self.claude_dir)


def _expand(path: str) -> str:
    return str(Path(path).expanduser()).rstrip("/")


def load_config(path: Path | None) -> Config:
    data = {}
    if path is not None and path.exists():
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    cfg = Config()
    board = data.get("board", {})
    for key in SIMPLE_KEYS:
        if key in board:
            setattr(cfg, key, board[key])
    for key in PATH_KEYS:
        if key in board:
            setattr(cfg, key, Path(board[key]).expanduser())
    cfg.summary_exclude_paths = [_expand(p) for p in board.get("summary_exclude_paths", [])]
    if os.environ.get("AGENT_BOARD_CLAUDE_DIR"):
        cfg.claude_dir = Path(os.environ["AGENT_BOARD_CLAUDE_DIR"])
    if os.environ.get("AGENT_BOARD_DB"):
        cfg.db_path = Path(os.environ["AGENT_BOARD_DB"])
    if os.environ.get("AGENT_BOARD_NO_SUMMARY") == "1":
        cfg.summary_enabled = False
    cfg.families = [
        Family(name=f["name"], icon=f.get("icon", "📁"), projects=list(f.get("projects", [])))
        for f in data.get("family", [])
    ]
    cfg.project_paths = [(_expand(p["path"]), p["name"]) for p in data.get("project", [])]
    if cfg.summary_enabled and cfg.claude_dir != DEFAULT_CLAUDE_DIR:
        # Безпечна відмова: дошка не свого акаунта ніколи не шле розмови в Haiku, навіть якщо рядок забули.
        log.warning("Haiku disabled: claude_dir %s is not %s", cfg.claude_dir, DEFAULT_CLAUDE_DIR)
        cfg.summary_enabled = False
    return cfg
