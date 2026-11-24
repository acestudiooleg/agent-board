#!/bin/sh
# Відкрити дошку вікном-застосунком; спершу запускає її, якщо не працює. usage: ./open.sh config.toml
exec "$(dirname "$0")/agent-board" open "${1:-config.toml}"
