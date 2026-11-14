# P1.0.2 — результаты реализации

Дата: 2026-11-14

## Результат

Реализовано Python-ядро research sessions и минимальный безопасный Chrome Native Messaging Host. Существующие P0.4 CLI, SIWC, Credential Manager, ZeroPaidPolicy, provider adapters, quota и storage не изменялись.

## Реализовано

- Фиксированный workflow `DRAFT → INDEPENDENT → COMPARISON → CRITIQUE → FACT_CHECK → ARBITRATION → REVIEW → DONE`.
- Типизированные состояния с проверкой переходов и обязательной причиной пропуска.
- `ResearchSessionService`: create/get/list/start/pause/resume/advance и внутреннее обновление статуса task.
- Отдельная SQLite-база `data/research.db` с миграцией, foreign keys, WAL, busy timeout и транзакциями.
- Optimistic concurrency через `expected_revision`; идемпотентность mutating operations.
- Таблицы сессий, участников, раундов, tasks, prompt artifacts, результатов и ревизий, comparison/fact-check/arbitration records и безопасных событий.
- Native Messaging framing: UTF-8 JSON, 4-byte native-endian length, вход до 64 MiB, выход Host до 1 MiB, Windows binary mode.
- Ограниченный IPC v1: `system.hello`, `session.create/get/list/start/pause/resume/advance`, `task.list`.
- Типизированные ошибки, включая stale revision, invalid phase, oversized frame и database error; ответы не отражают невалидный пользовательский payload.
- Entry point `multi-llm-native-host`; регистрация Chrome host не выполнялась.
- Защита `research.db`, WAL/SHM/journal и pytest temp artifacts через `.gitignore`.

## Тестирование

Команда:

```powershell
.\.venv\Scripts\python.exe -m pytest --basetemp .pytest-tmp
```

Результат: `70 passed in 5.35s`.

Новые тесты покрывают workflow/status transitions, migration/persistence, rollback, конкурентную инициализацию, optimistic concurrency, idempotency, восстановление через новый экземпляр сервиса, gap rules, Unicode/emoji framing, повреждённые и обрезанные frames, лимит 1 MiB, неизвестную операцию/версию и отсутствие утечки вопроса в события/диагностику.

Полный pytest запускался локально вне ограниченного sandbox, поскольку старые Windows Credential Manager тесты внутри sandbox зависали. Использовались только искусственные тестовые данные. OAuth-авторизация, модельные API и другие реальные сетевые вызовы не выполнялись.

## Изменённые файлы

- `.gitignore`
- `README.md`
- `pyproject.toml`
- `src/multi_llm/research/__init__.py`
- `src/multi_llm/research/migration.py`
- `src/multi_llm/research/models.py`
- `src/multi_llm/research/native_host.py`
- `src/multi_llm/research/native_protocol.py`
- `src/multi_llm/research/prompts.py`
- `src/multi_llm/research/repository.py`
- `src/multi_llm/research/service.py`
- `src/multi_llm/research/workflow.py`
- `tests/research/test_native_protocol.py`
- `tests/research/test_repository.py`
- `tests/research/test_service.py`
- `tests/research/test_workflow.py`

## Ограничения и намеренно не реализовано

- Chrome UI и manifest/регистрация Native Messaging Host.
- Clipboard integration.
- Установка или запуск FreeLLMAPI.
- Реальные модельные вызовы и автоматический provider orchestration.
- Fast/standard/deep modes, расширенный UI и live testing.

Техническое отклонение от предложенной раскладки отсутствует: research-компоненты находятся в отдельном пакете `src/multi_llm/research/`, а P0.4 persistence остаётся неизменным.
