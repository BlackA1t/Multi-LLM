# Multi-LLM — P1.0.5C Results

Дата проверки: 2026-11-16

## Итоговый статус

**PASS — реализация P1.0.5C завершена автоматическими проверками. RUNTIME NOT VERIFIED.**

После действительной immutable-версии S1 система создаёт ровно три независимых задания проверки: DeepSeek, Grok и Gemini. Каждый reviewer получает воспроизводимый пакет с оригинальным запросом, конкретной версией S1, исходными R0 и использованным материалом Perplexity. Другие рецензии и проект S2 в пакет не включаются.

Старый двухмодельный CRITIQUE-маршрут и переход к FACT_CHECK для нового процесса отключены. S2 и P1.0.5D не реализовывались.

## Реализованные компоненты

- Добавлен тип задачи `S1_REVIEW` и фиксированный состав `deepseek`, `grok`, `gemini`.
- Переход `COMPARISON → CRITIQUE` разрешён только после сохранённого S1, связанного с актуальным S1 package.
- Добавлен versioned reviewer prompt template v1 с полным S1, полными immutable R0, optional Perplexity, provenance и правилами обработки недоверенного текста.
- Reviewer packages детерминированы, имеют fingerprint/SHA-256 и безопасно разбиваются на части не более 512 KiB без усечения UTF-8.
- Использован существующий chunked response upload; оригинальный текст каждой рецензии хранится immutable revisions без содержательной интерпретации Python.
- Reviewer result связан с конкретным reviewer package и конкретной версией S1.
- Повторная подготовка для одной версии S1 идемпотентна и не создаёт дубликатов.
- При новой версии S1 предыдущие reviewer tasks немедленно получают `STALE`; прежние packages, reviews и revision history сохраняются, но перестают учитываться в текущем прогрессе.
- Для новой версии S1 создаётся новый набор из трёх задач и пакетов; старые результаты не перепривязываются.
- UI показывает этап «Независимая проверка S1», прогресс `0/3`–`3/3`, части каждого пакета, размер, SHA, просмотр, последовательное копирование, импорт и новые редакции.
- При stale S1 UI сразу показывает `0/3` по статусам задач без дополнительного обновления манифеста и предлагает подготовить новый набор.

## SQLite migration v5

Добавлены таблицы:

- `reviewer_packages` — reviewer task/provider, template version, точная S1 result revision, S1 package/fingerprint, package fingerprint/SHA, размер, части и полнота R0;
- `reviewer_package_inputs` — immutable ссылки на версии R0 и Perplexity;
- `reviewer_package_parts` — prompt artifacts и SHA-256 каждой части;
- `reviewer_result_links` — точная связь импортированной рецензии с reviewer package.

Миграция добавочная: старые таблицы, S1 packages, результаты и исторические задачи не удаляются и не переопределяются.

## IPC v1

Версия envelope сохранена. Добавлены четыре операции:

- `review.package.prepare`;
- `review.package.list`;
- `review.package.part.chunk`;
- `review.package.part.copy`.

Native Host теперь объявляет 31 операцию. Большие материалы по-прежнему передаются ограниченными чанками с проверяемыми размерами и контрольными суммами.

## Изменённые компоненты

- Backend research domain: models, workflow, repository, service, Native Protocol.
- Новый сборщик `src/multi_llm/research/reviewer.py`.
- SQLite migration v5.
- Chrome extension contracts, bridge, controller, state hook и research UI.
- Synthetic Native Host probe.
- Research, migration, protocol и frontend regression tests.

## Stale/recovery invariant

Отдельный регрессионный сценарий подтверждает:

1. Три актуальные рецензии имеют состояние `3/3`.
2. Импорт S1 revision 2 в той же активной review-фазе атомарно помечает три старые reviewer tasks как `STALE`.
3. Все три старые рецензии, result IDs, revisions, links и packages остаются в SQLite.
4. Текущий прогресс сразу становится `0/3`; UI вычисляет его без загрузки package manifest.
5. Старые packages получают `stale=true` и блокируются для копирования/импорта.
6. Новый идемпотентный набор содержит ровно три packages, связанные только с S1 revision 2.
7. Старые reviewer results остаются связаны только с packages S1 revision 1; у новых задач нет скрыто перенесённых результатов.

Таким образом старые рецензии не могут автоматически считаться входом будущего S2 для новой версии S1.

## Результаты проверок

- Полный Python pytest: **112 passed**.
- Research tests: **63 passed**.
- Frontend Vitest: **23 passed**.
- TypeScript `tsc --noEmit`: **PASS**.
- Vite production build: **PASS**.
- Synthetic Native Host probe: **PASS** — IPC v1, 31 операция, 13 задач, 8 R0, S1, три reviewer packages, три импорта, FACT_CHECK blocked, lost-ACK retry, чистые stdout/stderr.
- `npm audit --audit-level=high`: **0 vulnerabilities**.
- `git diff --check`: **PASS**.

Полный pytest в ограниченной среде останавливался на старом OAuth-тесте при инициализации HTTP-клиента. Точка была локализована до любого OAuth HTTP-запроса. Тот же полный набор выполнен в обычной локальной среде проекта и завершился `112 passed`; настоящая OAuth-авторизация не запускалась.

## Security review

- R0, S1, Perplexity и reviewer responses сериализуются как недоверенные данные и не исполняются как инструкции приложения.
- Reviewer package не содержит рецензии других моделей, агрегированные замечания или S2.
- Полные ответы не включаются в диагностические ошибки или Native Host stdout/stderr.
- Реальные OAuth, LLM/API-вызовы, DOM automation и web-chat automation не выполнялись.
- Тесты и probe использовали только синтетические данные и временные SQLite-базы.
- Пользовательская `research.db`, Chrome profiles, `.env`, credentials, токены, cookies и локальные логи не предназначены для Git.

## Ограничения и runtime gaps

- **RUNTIME NOT VERIFIED:** ручной Chrome smoke нового reviewer UI не проводился.
- Реальные официальные чаты DeepSeek, Grok и Gemini не открывались; реальные пакеты не отправлялись и ответы не импортировались.
- Заявленное пользователем имя модели сохраняется как provenance, но не считается доказательством фактической идентичности внешней модели.
- Независимость обеспечивается изоляцией пакетов, но ручная работа во внешнем чате не даёт криптографического доказательства происхождения рассуждений.
- Получение 3/3 только показывает готовность данных для будущего S2. S2 автоматически не запускается.
