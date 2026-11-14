# P1.0.3 — результаты реализации и Chrome Runtime Acceptance

Дата: 2026-11-14

Статус: **P1.0.3 STATIC+CHROME BRIDGE VERIFIED**

## Реализовано

- Manifest V3 extension на React + TypeScript + Vite.
- Независимые `sidepanel.html` и `workspace.html` с общими компонентами и системной светлой/тёмной темой.
- Service worker bridge к `com.blackalt.multi_llm`: `system.hello`, строгий envelope v1, response correlation, hard timeout, bounded pending, disconnect/reconnect и typed errors.
- Реальные UI-операции `session.create/get/list/start/pause/resume/advance` и `task.list`.
- Четыре фиксированных уникальных назначения `gemini/deepseek/qwen/grok` без фиктивных результатов.
- Read-through синхронизация по backend revision после mutation, focus, visibility change и сигнала другого окна.
- Повтор прерванной mutation сохраняет первоначальный idempotency key; `STALE_REVISION` перечитывает revision и требует пользовательского повтора.
- Chrome storage ограничен `activeSessionId`; вопросы, title, prompts и ответы не сохраняются.
- HKCU register/unregister scripts с `-WhatIf`, проверкой ID/path/ownership и rollback/recovery.
- Стабильный Native Host database fallback `%LOCALAPPDATA%\Multi-LLM\research.db`; относительный/пустой override запрещён.
- Синтетический stdio probe проверяет launcher, handshake, CRUD, четыре tasks, pause/resume, workflow gate, stdout framing и пустой stderr.

## Автоматически проверено

- Полный Python suite: `77 passed in 5.71s`.
- Frontend unit tests: `5 files, 15 tests passed`.
- Production build: Vite build успешно создан; Manifest, background worker, оба HTML entrypoint и локальные icons присутствуют.
- `npm ci`: успешно, lockfile воспроизводим.
- `npm audit`: `0 vulnerabilities`.
- Targeted Python tests пути БД и Native Messaging: `16 passed`.
- Launcher probe:

```json
{"status":"PASS","protocol_version":1,"operations_checked":9,"tasks_created":4,"advance_gate":"INVALID_PHASE","stdout_framing":"clean","stderr":"empty"}
```

- Manifest содержит только `sidePanel`, `nativeMessaging`, `storage`; нет content scripts, host permissions или externally connectable.
- HKCU registration, manifest и launcher существуют. Единственный origin: `chrome-extension://bjjaimmalalgcdhofmggchjafgijdlkn/`.
- Фактическая `%LOCALAPPDATA%\Multi-LLM\research.db` проверена read-only: `PRAGMA integrity_check = ok`, 13 таблиц, одна сессия `P103 Runtime Test` в `INDEPENDENT / ACTIVE / rev.3`.

## Chrome Runtime — подтверждено пользователем вручную

Автоматическое управление Chrome ранее было прекращено после защитной остановки Computer Use; блокировка не обходилась. Финальная runtime-приёмка выполнена пользователем в изолированном Chrome-профиле.

Фактический extension ID: `bjjaimmalalgcdhofmggchjafgijdlkn`.

Ручные PASS:

- Load unpacked MV3 без ошибок; toolbar action открывает Side Panel, локальная кнопка открывает Workspace.
- Реальный Chrome ↔ Python Native Messaging handshake успешен.
- Создана синтетическая сессия `P103 Runtime Test`; list/get показывают сохранённые данные.
- `session.start` перевёл сессию в независимые ответы и создал четыре задания: Gemini, DeepSeek, Qwen, Grok.
- Пауза отображена на `rev.2`, возобновление — на `rev.3`.
- Переход к следующему этапу без результатов заблокирован с понятной причиной.
- Side Panel и Workspace синхронно отображают session, tasks, status и revision.
- После полного перезапуска Chrome сессия восстановлена.
- После unregister Host UI показал корректную ошибку; после повторной регистрации соединение восстановилось без перезапуска Chrome.
- В `chrome.storage.local` присутствует только `activeSessionId`; пользовательские тексты отсутствуют.
- На проверенных размерах Side Panel/Workspace видимого горизонтального переполнения нет.
- При клавиатурной навигации textarea имеет видимую контрастную focus-рамку.

## Ограничения визуальной проверки

- Скриншоты в репозитории отсутствуют и не заявляются как артефакты приёмки.
- Адаптивность, отсутствие видимого overflow и keyboard focus подтверждены ручным наблюдением пользователя, без автоматического pixel-diff или формального WCAG-аудита вспомогательными технологиями.
- Реальная регистрация Host сохранена в HKCU для фактического extension ID; штатный unregister остаётся доступен отдельным скриптом.

## Исправления по ходу P1.0.3

- Обновлены Vite и Vitest до исправленных версий после обнаружения уязвимостей первоначального lockfile.
- Исправлено распознавание `HOST_NOT_REGISTERED` после disconnect.
- Добавлена строгая runtime-проверка структуры `result`.
- Повтор `session.create` после разрыва использует прежний idempotency key для того же содержимого формы.
- `STALE_REVISION` перечитывает session и предлагает явный повтор с актуальной revision без автоматической mutation.
- Регистратор отказывается перезаписывать непроверенный существующий manifest без явного `-AllowUpgrade`.
- Запись Native Messaging manifest выполняется через `.NET File.WriteAllText` и `UTF8Encoding(false)`: JSON остаётся UTF-8 без BOM и совместим с Windows PowerShell 5.1 и PowerShell 7.

## Security review

- Нет remote assets, CDN, analytics, fetch/XHR/WebSocket или обращений к AI-сайтам.
- Нет новых Chrome permissions, wildcard origins, content scripts, OAuth или FreeLLMAPI.
- React escaping используется для вопроса; `dangerouslySetInnerHTML` отсутствует.
- Локальные Chrome profiles, `node_modules`, `dist`, SQLite, manifests с пользовательскими ID/paths и временные файлы исключены из Git.
- Использованы только синтетические тестовые вопросы.

## Ограничения

- Clipboard/import, хранение ответов через IPC, веб-автоматизация, сравнение, критика, фактчек, арбитраж и P1.0.4 не реализованы.
