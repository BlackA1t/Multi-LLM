# Multi-LLM — P0.4.2.2 результаты

Дата: 14 ноября 2026 года. Статус: `OFFLINE_TESTED`. OAuth и модельные API не вызывались.

## Исправление

- Добавлена фабрика discovery-адаптера по явному provider ID, независимая от `ProviderRegistry`.
- `models openai_siwc` использует сохранённый OAuth token set и существующий `OpenAITokenProvider` без предварительного маршрута генерации.
- Refresh lifecycle, безопасные ошибки и закрытие HTTP-клиента сохранены.
- При отсутствии OAuth команда сообщает: `ChatGPT authorization is missing; run 'multi-llm auth-chatgpt'`.
- Генерационный `adapters_for()`, `ZeroPaidPolicy` и live-ограничения не изменены.

## Тесты

Финальный результат перед коммитом: `49 passed in 4.59s`.

Новые регрессии проверяют:

- получение mock-каталога SIWC при пустом registry;
- CLI `models openai_siwc` без маршрута;
- понятную ошибку при отсутствии авторизации;
- фильтрацию каталога и закрытие адаптера.

## Изменённые файлы этапа

- `src/multi_llm/cli.py`;
- `tests/test_model_discovery_cli.py`;
- `.gitignore`;
- `README.md`;
- `P0422_RESULTS.md`.

Реальная команда каталога и Live Testing пользователем в рамках автоматической проверки не запускались.
