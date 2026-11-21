import os
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .config import Family


@dataclass(frozen=True)
class ProjectRef:
    name: str
    worktree: str | None
    kind: str  # "user" | "git" | "worktree" | "table" | "rule" | "dir"
    root: str = field(default="", compare=False)  # тека проєкту (для панелі «Проєкти»)


def _worktree_main(git_file: str) -> str | None:
    try:
        with open(git_file, encoding="utf-8") as fh:
            text = fh.read().strip()
    except OSError:
        return None
    match = re.match(r"gitdir:\s*(.+)", text)
    if not match or "/.git/worktrees/" not in match.group(1):
        return None
    return match.group(1).split("/.git/worktrees/")[0]


class ProjectResolver:
    """cwd → проєкт: ваші проєкти (панель «Проєкти») → git-корінь → таблиця з конфігу → регулярка worktree → назва теки.
    З кешем; set_user() скидає кеш."""

    def __init__(self, home: str, table: list[tuple[str, str]], rules: list[str], user: list[tuple[str, str]] = ()):
        self.home = home.rstrip("/")
        self.user = self._sorted(user)
        self.table = self._sorted(table)
        self.rules = rules
        self._cache: dict[str, ProjectRef] = {}

    @staticmethod
    def _sorted(pairs) -> list[tuple[str, str]]:
        return sorted(((p.rstrip("/") or "/", n) for p, n in pairs), key=lambda x: -len(x[0]))

    def set_user(self, user: list[tuple[str, str]]) -> None:
        self.user = self._sorted(user)
        self._cache.clear()

    def resolve(self, cwd: str) -> ProjectRef:
        if not cwd:
            return ProjectRef("?", None, "dir")
        path = os.path.normpath(cwd)
        if path not in self._cache:
            self._cache[path] = self._resolve(path)
        return self._cache[path]

    def _git(self, path: str) -> ProjectRef | None:
        d = path
        while d.startswith(self.home + "/"):
            git = os.path.join(d, ".git")
            if os.path.isdir(git):
                return ProjectRef(os.path.basename(d), None, "git", d)
            if os.path.isfile(git):
                main = _worktree_main(git)
                if main:
                    return ProjectRef(os.path.basename(main), os.path.basename(d), "worktree", main)
                return ProjectRef(os.path.basename(d), None, "git", d)
            d = os.path.dirname(d)
        return None

    def _resolve(self, path: str) -> ProjectRef:
        for prefix, name in self.user:
            if path == prefix or path.startswith(prefix + "/"):
                return ProjectRef(name, None, "user", prefix)
        ref = self._git(path)
        if ref:
            return ref
        for prefix, name in self.table:
            if path == prefix or path.startswith(prefix + "/"):
                return ProjectRef(name, None, "table", prefix)
        for rule in self.rules:
            match = re.search(rule, path)
            if match:
                return ProjectRef(match.group("project"), match.group("worktree"), "rule", path[:match.start("worktree") - 1])
        return ProjectRef(PurePosixPath(path).name or "/", None, "dir", path)


def family_of(project: str, families: list[Family]) -> tuple[str, str]:
    for family in families:
        if project in family.projects:
            return family.name, family.icon
    return project, "📁"


def related_projects(resolver: ProjectResolver, files, dirs, own: str) -> list[str]:
    """Проєкти (не «теки»), яких торкалась розмова: теки файлів з tool-викликів і всі cwd."""
    found = set()
    for d in {os.path.dirname(f) for f in files} | set(dirs):
        ref = resolver.resolve(d)
        if ref.kind != "dir" and ref.name != own:
            found.add(ref.name)
    return sorted(found)
