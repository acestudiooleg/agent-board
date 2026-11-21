#!/bin/sh
# Відкрити дошку окремим застосунком (власне вікно й значок на панелі задач). Спершу запускає дошку, якщо вона ще не працює.
# usage: ./open.sh config.toml | config.work.toml
set -eu
# Не передавати змінні сесії Claude Code (якщо запущено з неї) — інакше термінали від дошки їх успадкують.
for v in $(env | sed -n 's/^\(CLAUDE[A-Za-z0-9_]*\)=.*/\1/p'); do [ "$v" = CLAUDE_CONFIG_DIR ] || unset "$v"; done
cd "$(dirname "$0")"
CONFIG="${1:-config.toml}"
./run.sh "$CONFIG" >/dev/null
PORT=$(python3 -c "import sys; from agent_board.config import load_config; from pathlib import Path; print(load_config(Path(sys.argv[1])).port)" "$CONFIG")
NAME=$(python3 -c "import re, sys; from agent_board.config import load_config; from pathlib import Path; print(re.sub(r'[^A-Za-z0-9]', '', load_config(Path(sys.argv[1])).board_name) or 'Board')" "$CONFIG")
# Вікно дошки вже відкрите — лише переводимо на нього фокус, а не відкриваємо копію.
WIN=$(wmctrl -lx 2>/dev/null | awk -v c="AgentBoard$NAME" '$3 ~ ("\\." c "$") {print $1; exit}')
if [ -n "$WIN" ]; then exec wmctrl -i -a "$WIN"; fi
curl -s -o /dev/null --retry 60 --retry-max-time 120 --retry-connrefused --retry-delay 1 "http://127.0.0.1:$PORT/" || true
exec google-chrome --app="http://127.0.0.1:$PORT/" --class="AgentBoard$NAME" \
  --user-data-dir="$HOME/.local/share/agent-board/chrome-$NAME" --no-first-run --no-default-browser-check
