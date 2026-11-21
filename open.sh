#!/bin/sh
# Відкрити дошку окремим застосунком (власне вікно й значок на панелі задач). Спершу запускає дошку, якщо вона ще не працює.
# usage: ./open.sh config.toml | config.work.toml
set -eu
cd "$(dirname "$0")"
CONFIG="${1:-config.toml}"
./run.sh "$CONFIG" >/dev/null
PORT=$(python3 -c "import sys; from agent_board.config import load_config; from pathlib import Path; print(load_config(Path(sys.argv[1])).port)" "$CONFIG")
NAME=$(python3 -c "import re, sys; from agent_board.config import load_config; from pathlib import Path; print(re.sub(r'[^A-Za-z0-9]', '', load_config(Path(sys.argv[1])).board_name) or 'Board')" "$CONFIG")
curl -s -o /dev/null --retry 60 --retry-max-time 120 --retry-connrefused --retry-delay 1 "http://127.0.0.1:$PORT/" || true
exec google-chrome --app="http://127.0.0.1:$PORT/" --class="AgentBoard$NAME" \
  --user-data-dir="$HOME/.local/share/agent-board/chrome-$NAME" --no-first-run --no-default-browser-check
