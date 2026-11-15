# P1.0.4 — результаты реализации Manual Web-Chat Bridge

Дата: 2026-11-15
Статус: **IMPLEMENTED / AUTOMATED VERIFIED / CORE CHROME FLOW VERIFIED**

## Реализовано

- IPC v1 расширен без изменения envelope/framing: prompt meta/chunk/copy, task status/reassign, response upload begin/part/commit/abort, response list/chunk.
- Миграция SQLite v2 добавляет только staging-загрузки, durable operation receipts, индексы и уникальность одного `research_result` на task; существующие `research_results`/`response_revisions` остаются каноническим хранилищем.
- Ответы до 8 MiB передаются чанками по 32 768 raw UTF-8 bytes с Base64, SHA-256, строгой UTF-8 проверкой, TTL/quota/open-upload bounds и атомарным commit.
- Commit атомарно создаёт immutable revision, переводит task в `RECEIVED`, увеличивает session revision, пишет event без текста, сохраняет receipt и удаляет staging. Повтор после потерянного ACK возвращает тот же result/version.
- Windows clipboard используется только для явного `task.prompt.copy`; чтение/мониторинг clipboard отсутствуют.
- Каталог ограничен девятью утверждёнными provider ID и фиксированными официальными HTTPS URL. Prompt не помещается в URL; вкладка открывается только по клику пользователя.
- UI содержит task actions, prompt preview с version/hash, двухшаговый response preview/confirm, просмотр подтверждённого ответа, revision, unavailable/skip/reassign и серверный progress/gate.
- Strict gate: только 4 `RECEIVED`, либо ровно 3 `RECEIVED` + 1 `SKIPPED_WITH_REASON` с сохранённой причиной задачи и отдельной причиной перехода.
- Manifest permissions не менялись; content scripts, host permissions, DOM scraping, auto-send, AI API calls и P1.0.5 отсутствуют.

## Автоматически проверено

- Full Python suite: `91 passed in 14.59s` (release-прогон).
- P1.0.4 targeted suite включает v1→v2 migration, exact prompt/clipboard mock, chunk conflicts/bounds/integrity, invalid UTF-8, whitespace-only, lost ACK, immutable revisions, reassignment, strict gate и ровно 8 MiB Unicode boundary roundtrip.
- Frontend: `6 files, 20 tests passed`.
- Production build: PASS.
- `npm audit`: `0 vulnerabilities`.
- Native Host synthetic stdio probe: PASS — protocol v1, 22 operations, 4 chunked manual responses, durable receipt retry, final phase `COMPARISON`, clean stdout framing, empty stderr.
- HKCU registration: PASS; origin `chrome-extension://bjjaimmalalgcdhofmggchjafgijdlkn/`, manifest и launcher существуют.
- После подтверждённого Chrome flow пользовательская `research.db` проверена только read-only: `integrity=ok`, schema versions `1,2`, 3 sessions, 4 results, 5 immutable response revisions и 1 session в `COMPARISON`. Автоматические тесты использовали только временные БД.
- Static security scan: нет `dangerouslySetInnerHTML`, browser storage для текста, IndexedDB, console logging, fetch/XHR/WebSocket, content scripts или host permissions.

## Изменённые области

- `src/multi_llm/research/`: migration, models, repository, service, workflow, prompts, Native protocol, clipboard и provider catalog.
- `extension/src/`: contracts/bridge/background, session controller, provider catalog, shared state/UI/styles.
- `tests/research/` и `extension/tests/`: backend, protocol, migration, large-payload, retry, UI и allowlist regression tests.
- `scripts/native_host_probe.py`: P1.0.4 end-to-end synthetic stdio acceptance.
- `scripts/backup_research_db.py`: read-only consistent backup before first manual migration.

## Chrome Runtime

Основной Chrome runtime flow подтверждён пользователем:

- создание исследовательской сессии;
- получение и копирование сохранённого prompt;
- открытие официального веб-чата;
- импорт четырёх синтетических ответов;
- обратное чтение сохранённого ответа;
- создание Gemini revision 2 при сохранении revision 1 в SQLite;
- серверный отказ при преждевременном переходе;
- успешный переход в `COMPARISON` после четырёх `RECEIVED`;
- блокировка пустого и whitespace-only ответа;
- предупреждение при закрытии неподтверждённой формы;
- отсутствие неподтверждённого результата в SQLite.

Скриншоты в репозитории отсутствуют и не заявляются.

### NOT RUN

Следующие ручные сценарии не выполнялись и не маркируются как PASS:

- потеря ACK, offline/unregister/reconnect и ручная reconciliation без дубликата;
- runtime-проверка `UNAVAILABLE` → `SKIPPED_WITH_REASON`, gap reason и reassignment;
- runtime-отказ при повторном назначении provider и переназначении `RECEIVED` task;
- блокировка import/revision в paused session;
- Chrome roundtrip ответа ровно 8 MiB и разрыв Unicode на границе chunk;
- синхронизация Side Panel/Workspace после каждой response mutation;
- восстановление подтверждённых ответов после полного перезапуска Chrome;
- ручная инспекция browser storage/IndexedDB после P1.0.4;
- расширенная клавиатурная, screen-reader, responsive и overflow-проверка новых диалогов.

Критические инварианты этих сценариев покрыты автоматическими backend/frontend и stdio-тестами. Расширенный ручной checklist сохранён в `P104_MANUAL_ACCEPTANCE.md` как необязательный диагностический материал.

## Оставшиеся ограничения

- Реальные сайты могут требовать собственную авторизацию; расширение не читает их DOM, cookies, вкладки или ответы.
- Draft ответа намеренно хранится только в памяти текущего UI и теряется после закрытия/перезапуска до подтверждения.
- Comparison/critique/fact-check/arbitration остаются за пределами P1.0.4; после перехода в `COMPARISON` показывается placeholder P1.0.5.
- Некритичные UX-улучшения вынесены в `P104_UX_BACKLOG.md` и не блокируют финализацию P1.0.4.
