# Multi-LLM P1.0.6 — End-to-End Validation & Runtime Readiness

## Итоговый статус

- **AUTOMATED E2E PASS**
- **CHROME SMOKE PASS**
- **REAL LLM WORKFLOW NOT VERIFIED**

Multi-LLM готов к первой ограниченной пилотной исследовательской сессии с ручным контролем пользователя. Реальные внешние модели, OAuth и LLM/API в рамках P1.0.6 не использовались.

## Первоначальный runtime-аудит

- Git baseline: `c328dc8502381cf78b80117fc1e7fad84b4d62ec`, ветка `main`, исходное рабочее дерево чистое.
- Production extension build: PASS.
- Фактический extension ID: `bjjaimmalalgcdhofmggchjafgijdlkn`.
- HKCU Native Messaging registration: PASS.
- Manifest: `C:\Users\vadim\AppData\Local\Multi-LLM\NativeMessaging\com.blackalt.multi_llm.json`.
- `allowed_origins`: ровно `chrome-extension://bjjaimmalalgcdhofmggchjafgijdlkn/`.
- Host launcher: `C:\Multi-LLM\.venv\Scripts\multi-llm-native-host.exe`.
- Рабочая база: `C:\Users\vadim\AppData\Local\Multi-LLM\research-v2.db`.
- Read-only `PRAGMA integrity_check`: `ok`.
- Schema identity: `multi-llm-research-v2`, version 1.
- Legacy `research.db` сохранена и не удалялась.
- Первый запуск без базы проверен synthetic probe: Host создаёт чистую `research-v2.db` и полный baseline schema.

## AUTOMATED E2E PASS

Обновлённый `scripts/native_host_probe.py` выполняет полный синтетический маршрут через фактический Host executable и временную изолированную `research-v2.db`:

1. Создаёт сессию с основным и optional Perplexity prompt.
2. Создаёт восемь фиксированных R0 и отдельную Perplexity task.
3. Проверяет блокировку преждевременного перехода.
4. Читает prompt по IPC chunks и проверяет SHA-256.
5. Импортирует восемь различных синтетических R0.
6. Проверяет lost-ACK/retry одной операции.
7. Импортирует отдельный Unicode/Markdown/HTML-like synthetic Perplexity result.
8. Перезапускает Host в фазе R0 и подтверждает восстановление 9/9 результатов.
9. Проверяет S1 package manifest, chunks и provenance; импортирует S1.
10. Проверяет фиксированные reviewer packages DeepSeek/Grok/Gemini; импортирует три рецензии.
11. Проверяет S2 package, три reviewer links и chunks; импортирует S2.
12. Подтверждает автоматические `DONE` и `COMPLETED`.
13. Повторно перезапускает Host и подтверждает сохранение 14 ответов, S2 package ID и числа частей.
14. Проверяет чистые stdout/stderr Native Messaging framing.

Результат probe: protocol v1, 35 операций, 14 задач, 9 исходных результатов до S1, 3 reviewer results, S1 и S2, `intermediate_restart=PASS`, `final_restart=PASS`.

Автотесты также покрывают отсутствие обязательного R0/Perplexity, одинаковые тексты разных моделей, idempotency, S1/reviewer stale recovery, fingerprint mismatch, multipart сверх одного IPC frame, immutable revisions и недоверенный Unicode/Markdown.

## CHROME SMOKE PASS

Пользователь выполнил компактную ручную проверку в существующем изолированном профиле:

`C:\Multi-LLM\.chrome-p103-profile-manual`

Фактически подтверждено последовательностью скриншотов:

- расширение установлено, включено и открывается в Side Panel;
- создано исследование `P106 — Chrome Smoke Test`;
- сессия сохранена через Native Host;
- созданы R0 tasks и отображён прогресс 0/8;
- вручную импортирован один синтетический ответ ChatGPT;
- прогресс изменился на 1/8, статус ChatGPT — «Получено»;
- отображаются immutable version, SHA-256 и метаданные ответа;
- Side Panel закрыта и повторно открыта;
- сессия, прогресс 1/8, статус и версия ответа восстановлены.

Не маркируются проверенными в Chrome:

- отдельная операция копирования промпта;
- полный браузерный прогон восьми R0;
- браузерные стадии S1, S1_REVIEW, S2 и DONE;
- негативные/recovery-сценарии UI за пределами повторного открытия панели.

Эти сценарии проверены автоматическим backend/IPC E2E там, где указано, но не полным реальным UI-прогоном.

## Исправленный дефект

`scripts/backup_research_db.py` продолжал выбирать старый `%LOCALAPPDATA%\Multi-LLM\research.db` и использовать историческое имя backup P1.0.4.

Исправлено:

- default source изменён на `research-v2.db`;
- backup name изменён на `research-v2-<UTC timestamp>.db`;
- описание команды актуализировано;
- добавлен регрессионный тест, создающий рядом synthetic `research-v2.db` и legacy `research.db` и подтверждающий, что backup содержит только v2 source, а оба исходных файла остаются неизменными.

## UI-аудит

- Действующий UI использует только `DRAFT`, `R0`, `S1`, `S1_REVIEW`, `S2`, `DONE`.
- Старые названия `COMPARISON`, `CRITIQUE`, `FACT_CHECK`, `ARBITRATION`, `REVIEW` в `extension/src` и production build отсутствуют.
- Прогресс R0 и reviewer stages выводится контекстно; stale tasks исключаются из актуального 0/3.
- Ошибки импорта и соединения имеют видимый `role="alert"` и путь восстановления.
- Multipart packages показывают число частей, размер, SHA-256, copy progress и предупреждение об ограничении внешнего контекста.
- Масштабный редизайн не выполнялся.

## Финальные проверки

- Python compileall: **PASS**.
- Full Python pytest: **123 passed in 25.50s**.
- Research pytest: **74 passed in 20.29s**.
- Frontend Vitest: **24 passed, 6 files**.
- TypeScript `tsc --noEmit`: **PASS**.
- Vite production build: **PASS**.
- HKCU registration check: **PASS**.
- Synthetic Native Host E2E probe: **PASS**.
- `npm audit --audit-level=low`: **0 vulnerabilities**.
- `git diff --check`: **PASS**.

## Security review

- Автоматические внешние LLM/API-вызовы и DOM-автоматизация отсутствуют.
- Synthetic probe использует только временную БД и искусственные ответы.
- Пользовательские prompt/response texts не добавлены в логи или Git.
- OAuth tokens, cookies, credentials, `.env`, локальные SQLite-базы и Chrome-профили не включены в изменения.
- Imported Markdown/HTML-like text остаётся недоверенными данными и не исполняется.
- Immutable revisions, SHA-256, provenance, stale detection и idempotency сохранены.
- Пользовательская `research-v2.db` проверена только read-only запросами integrity/schema/count; содержимое ответов не читалось.
- Legacy `research.db` не изменялась и не удалялась.

## Изменённые файлы

- `scripts/backup_research_db.py`;
- `scripts/native_host_probe.py`;
- `tests/research/test_backup_research_db.py`;
- `P106_RESULTS.md`.

## Оставшиеся ограничения

- **REAL LLM WORKFLOW NOT VERIFIED:** реальные ответы внешних моделей не использовались.
- Копирование prompt через Chrome UI не подтверждено в ручном smoke.
- Полный S1/S1_REVIEW/S2 Chrome UI flow не повторялся; он подтверждён synthetic IPC E2E и компонентными тестами.
- Multipart не гарантирует вместимость или полную семантическую обработку внешним ChatGPT.
- Legacy `research.db` является архивом и не мигрируется автоматически.

## Готовность

**READY FOR FIRST CONTROLLED PILOT RESEARCH**, при условии ручной проверки пользователем каждого отправляемого пакета и сохранения текущего запрета на автоматические внешние вызовы.
