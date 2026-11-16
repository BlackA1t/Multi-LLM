# Multi-LLM P1.0.5D — Results

## Итоговый статус

**IMPLEMENTED / AUTOMATED ACCEPTANCE PASS / RUNTIME NOT VERIFIED**

Реализован единственный поддерживаемый маршрут исследования:

`DRAFT → R0 → S1 → S1_REVIEW → S2 → DONE`

При трёх актуальных рецензиях DeepSeek, Grok и Gemini создаётся ровно одно задание ChatGPT S2 с воспроизводимым многочастным пакетом. Первый действительный импорт S2 автоматически завершает сессию; исправленный импорт создаёт новую immutable revision.

Реальная Chrome Runtime Acceptance не выполнялась. Реальные OAuth, LLM/API и DOM-вызовы не выполнялись.

## Реализованные изменения

- Добавлен versioned S2 prompt template, требующий независимой оценки каждого замечания, сохранения подтверждённых выводов S1, реестра решений и явных неопределённостей.
- Добавлен детерминированный S2 builder с фиксированным порядком DeepSeek/Grok/Gemini, immutable provenance S1/R0/Perplexity, input fingerprint и SHA-256.
- Сохранён полный исходный текст всех материалов без эвристического объединения, компрессии и скрытого усечения.
- Пакет делится по UTF-8 на части не более 512 KiB. Каждая часть содержит идентификацию, номер/количество и инструкцию ожидать полный комплект.
- Добавлены размер, количество частей, completeness manifest и предупреждение: multipart не гарантирует вместимость всего контекста ChatGPT.
- Добавлены immutable связи импортированного S2 с конкретным S2 package; повторный импорт сохраняет предыдущую версию.
- Реализованы stale-проверки S1, каждой reviewer revision, fingerprint, package SHA и part count.
- Новая reviewer revision инвалидирует только S2 package; остальные актуальные рецензии сохраняются.
- Новая S1 инвалидирует reviewer/S2 tasks. Recovery возвращает сессию в `S1_REVIEW` и создаёт новый reviewer-набор 0/3 для текущей S1.
- Частичный допустимый R0 переносится в S2 manifest и итоговый статус `COMPLETED_WITH_GAPS`.
- Удалены старые фазы и task kinds `COMPARISON`, `CRITIQUE`, `FACT_CHECK`, `ARBITRATION`, `REVIEW` и их неиспользуемые таблицы/переходы.
- Side Panel/Workspace показывают S2 provenance, актуальность, размер, SHA-256, прогресс копирования, предупреждение о контексте, импорт и историю результата.

## SQLite

По официальному дополнению вместо цепочки экспериментальных migrations v1–v5 создана единая базовая схема `multi-llm-research-v2`, version 1.

Новая установка использует `%LOCALAPPDATA%\Multi-LLM\research-v2.db`. Существующий `research.db` не удаляется, не изменяется и не мигрируется автоматически. Явно переданная legacy-база отклоняется с безопасной диагностикой и остаётся без изменений.

Добавлены таблицы:

- `s2_packages`;
- `s2_package_reviews`;
- `s2_package_inputs`;
- `s2_package_parts`;
- `s2_result_links`.

Параллельная инициализация чистой базы проверена; DDL идемпотентен внутри сериализованной SQLite-транзакции.

## IPC v1

Добавлены операции без изменения envelope/version:

- `s2.package.prepare`;
- `s2.package.get`;
- `s2.package.part.chunk`;
- `s2.package.part.copy`.

Synthetic Native Host probe: **PASS**, protocol v1, 35 операций, 14 задач, полный маршрут до `DONE`, чистые stdout/stderr, lost-ACK retry PASS.

## Проверки stale/recovery

- 0/3, 1/3 и 2/3 рецензий блокируют S2 — PASS.
- 3/3 актуальных рецензий создают фиксированный пакет — PASS.
- Рецензия без замечаний считается полноценным результатом — PASS.
- Новая revision любой рецензии делает прежний S2 stale и фиксируется в новом пакете — PASS.
- Новая S1 делает старые reviewer/S2 tasks stale; старые данные остаются в истории — PASS.
- Recovery новой S1 создаёт новые reviewer packages, привязанные только к S1 v2 — PASS.
- Fingerprint tampering определяется как stale — PASS.
- Перезапуск сервиса сохраняет package metadata, parts, result links и revisions — PASS.
- Повторная prepare/import с тем же idempotency key не создаёт дубликаты — PASS.
- Multipart UTF-8 восстановлен без потери данных — PASS.

## Результаты автоматических проверок

- Full Python pytest: **122 passed in 19.44s**.
- Research pytest: **73 passed in 21.51s**.
- Frontend Vitest: **24 passed (6 files)**.
- TypeScript `tsc --noEmit`: **PASS**.
- Vite production build: **PASS**.
- Python `compileall`: **PASS**.
- Synthetic Native Host probe: **PASS**.
- `npm audit --audit-level=low`: **0 vulnerabilities**.
- `git diff --check`: **PASS**.

Первый sandbox-запуск полного pytest завис до выполнения OAuth-теста при создании Windows asyncio event-loop (`socket._fallback_socketpair`). Повтор вне socket-ограничения sandbox завершился полным результатом 122/122; сетевые OAuth-вызовы не выполнялись.

## Security review

- Все R0, Perplexity, S1 и reviewer texts сериализуются как недоверенные данные и структурно отделены от служебной инструкции.
- UI выводит импортированный текст через React/preformatted text, без HTML injection.
- В логах и IPC-ошибках не добавлены полные ответы, приватные промпты, OAuth tokens, cookies или credentials.
- Внешние веб-чаты не автоматизируются; копирование каждой части остаётся явным действием пользователя.
- Реальные пользовательские SQLite-базы, Chrome-профили, `.env`, токены и локальные ответы не включены в изменения.

## Изменённые компоненты

- Backend: `models.py`, `workflow.py`, `migration.py`, `finalization.py`, `repository.py`, `service.py`, `native_protocol.py`, `native_host.py`.
- Chrome extension: IPC contracts/bridge/controller, shared state, S2 interface and frontend tests.
- Acceptance: `native_host_probe.py`, research regression tests, README.

## Известные ограничения

- **RUNTIME NOT VERIFIED:** реальный Chrome smoke-test P1.0.5D не проводился.
- Разделение на части решает ограничение передачи, но не гарантирует, что внешний ChatGPT вместит или семантически обработает весь контекст.
- Программа не проверяет истинность решений ChatGPT и не выполняет независимую внешнюю верификацию источников.
- Legacy `research.db` сохраняется только как архив; автоматическая совместимость и миграция намеренно отсутствуют.
