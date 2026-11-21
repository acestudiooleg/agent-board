#!/bin/sh
# Запуск дошки без systemd (автозапуск Cinnamon або вручну). Не дублює вже запущену дошку.
# usage: ./run.sh config.toml | config.work.toml
set -eu
# Не передавати змінні сесії Claude Code (якщо запущено з неї) — інакше термінали від дошки їх успадкують.
for v in $(env | sed -n 's/^\(CLAUDE[A-Za-z0-9_]*\)=.*/\1/p'); do [ "$v" = CLAUDE_CONFIG_DIR ] || unset "$v"; done
cd "$(dirname "$0")"
CONFIG="${1:-config.toml}"
PORT=$(python3 -c "import sys; from agent_board.config import load_config; from pathlib import Path; print(load_config(Path(sys.argv[1])).port)" "$CONFIG")
if ss -ltn | grep -q ":$PORT "; then echo "порт $PORT уже зайнятий — дошка працює"; exit 0; fi
LOGDIR="$HOME/.local/share/agent-board"; mkdir -p "$LOGDIR"
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:$PATH"
export DISPLAY="${DISPLAY:-:0}"
nohup setsid python3 -m agent_board "$CONFIG" >> "$LOGDIR/${CONFIG%.toml}.log" 2>&1 < /dev/null &
echo "запущено: $CONFIG на порту $PORT (лог $LOGDIR/${CONFIG%.toml}.log)"
