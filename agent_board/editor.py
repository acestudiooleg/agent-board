"""Кнопка «VS Code»: чим і яку теку відкривати."""
import os
import shutil
from pathlib import Path

from .system import MAC

# На Mac застосунок часто стоїть без команди code у PATH.
MAC_APPS = tuple(str(Path(root).expanduser() / "Visual Studio Code.app/Contents/Resources/app/bin/code")
                 for root in ("/Applications", "~/Applications"))


def command(mac: bool = MAC) -> str | None:
    found = shutil.which("code")
    if found or not mac:
        return found
    return next((p for p in MAC_APPS if os.access(p, os.X_OK)), None)


def _worktree(cwd: str, root: str) -> str | None:
    """Тека git worktree, у якій почалась розмова (там .git — файл), не вище за корінь проєкту."""
    for d in (Path(cwd), *Path(cwd).parents):
        if (d / ".git").is_file():
            return str(d)
        if str(d) == root or (d / ".git").exists():
            return None
    return None


def folder(card: dict) -> str | None:
    """Тека проєкту картки; для worktree — саме він. Проєкт, визначений за файлами, важливіший за теку старту."""
    cwd, root = (p if p.startswith("/") else "" for p in (card.get("cwd") or "", card.get("project_root") or ""))
    candidates = [root, cwd]
    if cwd and os.path.isdir(cwd) and (card.get("worktree") or not root or cwd == root or cwd.startswith(root + "/")):
        own = _worktree(cwd, root)
        if own or card.get("worktree"):
            candidates = [own or cwd, root]
    return next((p for p in candidates if p and os.path.isdir(p)), None)
