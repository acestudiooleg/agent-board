# Agent Board

Локальна дошка «кімната команди» для всіх сесій [Claude Code](https://claude.com/claude-code): кожна розмова — картка-«співробітник» з проєктом, станом і коротким підсумком. Видно, хто чекає на вашу відповідь, хто працює, що закинуто, і до якого проєкту це належить.

- **Стани:** чекає на вас (зокрема питання / дозвіл), працює, чекає на фонові задачі, спить; «Готово» / «Відкласти» ховають картку, нова активність повертає її.
- **Проєкти:** за git-коренем першого `cwd` розмови, worktree — до головного репо; ваші проєкти «назва + тека» (панель «🗂 Проєкти») мають пріоритет; суміжні проєкти — за файлами й командами.
- **Підсумки:** ізольований `claude -p --model haiku` (без хуків, плагінів і MCP), ліміт на годину, backoff і запобіжник. Робоче (родини й теки з конфігу) у Haiku не відправляється.
- **Дві дошки з одного коду:** особиста (`~/.claude`) і робоча (окремий акаунт з `CLAUDE_CONFIG_DIR`, Haiku вимкнено примусово).
- **Перехід до розмови:** закрита — відкривається в Terminator на весь екран (ваш профіль, вкладки «назва картки» + «термінал»); жива — фокус вікна, де вона працює.
- **Пошук** (`/`), чипи родин, панель звіту з Markdown, космічна тема зі склом.
- Лише stdlib Python 3.13, без залежностей; HTTP + SSE лише на `127.0.0.1` з перевіркою `Host` / `Origin`.

## Запуск

    cp config.example.toml config.toml            # і за потреби config.work.example.toml → config.work.toml
    python3 -m agent_board                        # http://127.0.0.1:8091
    ./run.sh config.toml                          # у фоні, лог у ~/.local/share/agent-board/
    ./open.sh config.toml                         # окремим вікном-застосунком (Chrome --app)

Автозапуск: systemd user-service (`agent-board.service`, `agent-board-work.service`) або `run.sh` у `~/.config/autostart`.

## Конфіг

`config.toml` (у git не потрапляє, зразок — `config.example.toml`): назва й значок дошки, `claude_dir`, порт, родини проєктів, теки без git, `summary_exclude_families` / `summary_exclude_paths`, ліміти Haiku, `planet` (`ringed` / `mars`). Локальні файли (наприклад, чужі логотипи для значка) — у `assets/local/`, теж поза git.

## Тести

    python3 -m unittest discover -s tests -v

## Видалення

    kill $(ss -ltnp | grep -oP ':(8091|8092) .*pid=\K[0-9]+')
    rm -r ~/.local/share/agent-board ~/.config/terminator/agent-board.config
