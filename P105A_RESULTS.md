# Multi-LLM — P1.0.5A Results

Дата проверки: 2026-11-15

## Итог

P1.0.5A реализован поверх существующих SQLite, IPC v1, Native Messaging Host и ручного Web-Chat Bridge P1.0.4.

- Один основной промпт создаёт восемь независимых R0-заданий: ChatGPT, Gemini, DeepSeek, Qwen, Grok, Copilot, GigaChat и Алиса.
- Во все восемь `prompt_artifacts` сохраняется один и тот же UTF-8 текст без смысловых обёрток и без ответов других участников.
- Необязательный промпт Perplexity создаёт отдельное `SEARCH_EVIDENCE`-задание и не входит в шлюз `8/8`.
- Intake можно сохранить как черновик и редактировать с новой версией до `session.start`; после старта изменение запрещено.
- Существующие ручной импорт, SHA-256, immutable response revisions, происхождение, идемпотентность и восстановление SQLite сохранены.
- Частичный переход разрешён только как явно неполный вариант `7 RECEIVED + 1 SKIPPED_WITH_REASON` с сохранённой причиной; семь ответов не выдаются за полный R0.

## Миграция

SQLite migration v3 добавляет в `research_sessions`:

- `perplexity_prompt TEXT NULL`;
- `intake_version INTEGER NOT NULL DEFAULT 1`.

Миграция применяется транзакционно существующим механизмом `schema_migrations`. Тест обновления базы v1 до v3 сохраняет сессию, задачу и prompt artifact.

## Изменённые области

- Python domain/storage: `models.py`, `provider_catalog.py`, `prompts.py`, `migration.py`, `repository.py`, `service.py`, `workflow.py`.
- Native IPC v1: `native_protocol.py`; добавлена операция `session.update_intake`, без изменения версии envelope.
- Chrome extension: contracts, handshake, controller, state hook, intake form, draft editor, R0 progress and separate Perplexity label.
- Synthetic integration probe: `scripts/native_host_probe.py`.
- Regression tests: research workflow/repository/service/manual bridge, IPC/frontend tests и новый `test_p105a_intake.py`.

## Автоматические проверки

- Python pytest: **98 passed**.
- Frontend Vitest: **20 passed**.
- TypeScript `tsc --noEmit` и Vite production build: **PASS**.
- `npm audit --audit-level=high`: **0 vulnerabilities**.
- Synthetic Native Host stdio probe: **PASS** — IPC v1, 23 обязательные операции, 8 R0 + 1 Perplexity, ранний advance отклонён, 8 ответов импортированы, переход в `COMPARISON`, lost-ACK receipt retry, чистые stdout/stderr.
- `git diff --check`: **PASS**.

Все тесты используют синтетические данные и временные базы. Реальные запросы к моделям, OAuth-авторизация и DOM-доступ не выполнялись.

## Security review

- Автоматическая отправка во внешние веб-чаты не добавлена.
- Content scripts, DOM scraping, платные API, логины, cookies и session tokens не добавлены.
- Основной prompt и Perplexity prompt не записываются в session events или диагностические ошибки.
- В Git не включены `research.db`, SQLite/WAL-файлы, Chrome profiles, `.env`, токены или локальные результаты.
- Длинные ответы продолжают передаваться существующими проверяемыми чанками и сохраняются только после проверки размера, UTF-8 и SHA-256.

## Непроверенные runtime-сценарии и ограничения

- Ручной Chrome runtime smoke для новой формы P1.0.5A не выполнялся; автоматические component tests, production build и synthetic Native Host probe прошли.
- Официальные веб-чаты не открывались и реальные ответы не импортировались.
- Perplexity сохранён как отдельный будущий вход для S1, но S1 в этом инкременте не реализован.
- Interview, Research Brief/Contract, review, fact checking, S1/S2 и автоматизация сторонних чатов остаются за границами P1.0.5A.
