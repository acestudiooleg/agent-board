#!/bin/sh
# Запуск дошки у фоні (автозапуск або вручну). usage: ./run.sh config.toml | config.work.toml
exec "$(dirname "$0")/agent-board" start "${1:-config.toml}"
