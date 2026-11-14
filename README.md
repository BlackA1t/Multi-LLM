# Multi-LLM

Минимальный Windows-local CLI-прототип провайдерского слоя. Все модельные маршруты по умолчанию отключены. Неизвестная бесплатность, неизвестная квота, отсутствие подтверждения аккаунта или отсутствие отдельного live-флага блокируют HTTP-запрос **до** адаптера.

## Установка для разработки

Требуется Python 3.11+:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -e ".[test]"
.venv\Scripts\pytest
```

Секреты сохраняются библиотекой `keyring`; в Windows это Windows Credential Manager. SQLite и JSON никогда не предназначены для OAuth-токенов или API-ключей. Длинные OAuth-наборы кодируются в UTF-8/Base64 и разбиваются на Credential Manager записи, каждая из которых занимает не более 2000 байт после UTF-16LE-кодирования (лимит Windows — 2560 байт).

## Команды

```powershell
multi-llm providers
multi-llm status
multi-llm set-key gemini
multi-llm set-key groq
multi-llm auth-chatgpt
multi-llm models gemini
multi-llm set-route gemini MODEL_ID --family gemini --developer Google `
  --enable --allow-live --authenticated `
  --free-tier-source "official pricing page, checked 2026-11-14" `
  --free-route-source "authenticated model catalog, checked 2026-11-14" `
  --account-state-source "AI Studio project settings, checked 2026-11-14" `
  --spend-protection-source "billing not linked, checked 2026-11-14" `
  --model-verification-source "models.list response, checked 2026-11-14" `
  --quota-source "account rate-limit page, checked 2026-11-14" `
  --request-limit 10 --token-limit 10000 --estimated-tokens 1000
multi-llm dry-run "Кратко объясни принцип работы радуги"
multi-llm export RUN_ID exports\run.json
```

Команда `models PROVIDER` использует явный provider ID и не требует заранее созданного маршрута генерации. Для `models openai_siwc` достаточно успешного `auth-chatgpt`; OAuth access token проверяется и при необходимости обновляется существующим token provider. Отсутствующая авторизация сообщает команду, которую нужно выполнить, и не создаёт маршрут автоматически.

`set-route` без источников проверки и числовой квоты создаёт заблокированный маршрут. Политика отдельно требует доказательства бесплатного тарифа, конкретного маршрута, состояния аккаунта, внешней защиты от списаний, фактической модели и квоты. Для каждого доказательства сохраняются источник и время проверки. Один булев флаг больше не может подтвердить финансовую безопасность.

Даже полностью подтверждённый маршрут не вызывается, пока одновременно не установлено `live_mode_enabled: true` в `config/local.json` и не переданы `run --live --confirm-zero-paid I_UNDERSTAND`. Обычный запуск и `dry-run` не обращаются к провайдерам.

## Выбор моделей

- OpenAI SIWC: только slug из аутентифицированного `GET /v1/models`, где `visibility == "list"`.
- Gemini: только модель из `models.list`, поддерживающая `generateContent`, после ручного подтверждения Free Tier проекта.
- Groq: только активная модель из `GET /models`; предпочтение Qwen не является автоматическим разрешением. Free Plan проверяется в кабинете.

ID моделей намеренно не зашиты: доступность и тарифы меняются. Три участника обязаны иметь разные проверенные `model_family_id`. Запрошенная и фактическая модели сохраняются отдельно; несовпадение является ошибкой, алиасы и автоматическая подмена запрещены.

## SIWC

Команда `auth-chatgpt` реализует PKCE, `state`, `nonce`, динамический `client_id`, loopback callback на `127.0.0.1`, обмен токенов, проверку подписи ID token, issuer/audience/nonce и scope `chatgpt.tokens.use.direct`. Перед каждым запросом адаптер проверяет срок access token. Истёкший токен обновляется официальным refresh flow; ротируемый набор заменяется одной операцией в Credential Manager. Async- и межпроцессная блокировки не допускают гонку refresh. Терминальный `invalid_grant` очищает непригодный token set, сохраняет регистрацию и требует явной команды `auth-chatgpt` — браузер не открывается автоматически.

Перед сохранением `auth-chatgpt` выводит только безопасные числовые показатели: UTF-8 размер, размер прямого Windows UTF-16LE blob, число фрагментов и максимальный размер фрагмента. Содержимое токенов не выводится.

Credential Manager storage использует версионированные фрагменты, manifest с длиной и SHA-256 и отдельный атомарный active pointer. Новая версия полностью записывается, читается обратно и проверяется до переключения. При ошибке предыдущая версия остаётся активной. Старые одиночные записи `keyring` продолжают читаться и удаляются после успешной миграции.

## Квоты

Состояние хранится в `data/quota.db`: использованные и зарезервированные запросы/токены, известные заголовки лимитов, reset и `Retry-After`. Резервирование выполняется транзакцией `BEGIN IMMEDIATE` до HTTP-вызова. Если приложение завершится после резервирования, расход остаётся зарезервированным до подтверждённого reset — это намеренно безопасный отказ. Без числового лимита маршрут блокируется.

## Финансовые границы

Приложение не включает billing, не меняет тариф, не пополняет баланс и не переключается на альтернативу. Локальный счётчик квот — дополнительный предохранитель, а не гарантия отсутствия списаний. Live-доступ разрешается только при отдельном актуальном доказательстве внешней защиты аккаунта. До проверки аккаунтов статус остаётся `OFFLINE_TESTED`, не `LIVE_VERIFIED`.

## Research-session core (P1.0.2)

В `multi_llm.research` реализовано автономное ядро исследовательских сессий с фиксированным маршрутом `DRAFT → INDEPENDENT → COMPARISON → CRITIQUE → FACT_CHECK → ARBITRATION → REVIEW → DONE`. Состояние хранится отдельно от P0.4 в `data/research.db`; включены внешние ключи, WAL, транзакционные мутации, optimistic revision и idempotency keys.

Минимальный Chrome Native Messaging Host запускается entry point `multi-llm-native-host`. Протокол v1 использует JSON-фреймы с нативным 32-битным префиксом длины и поддерживает только `system.hello`, `session.create/get/list/start/pause/resume/advance` и `task.list`. Host не регистрируется автоматически и не выполняет модельные или сетевые вызовы. Chrome UI, clipboard-интеграция и установка FreeLLMAPI в P1.0.2 намеренно отсутствуют.

## Chrome Extension (P1.0.3)

Исходники Manifest V3 расширения находятся в `extension/`. Сборка полностью локальная:

```powershell
Set-Location extension
npm ci
npm test
npm run build
```

Собранный каталог `extension/dist` загружается через `chrome://extensions` → «Загрузить распакованное расширение». Расширение использует только разрешения `sidePanel`, `nativeMessaging`, `storage`; content scripts и доступ к сайтам отсутствуют.

Native Host регистрируется только явным запуском пользователя и только для фактического ID загруженного расширения:

```powershell
$extensionId = '<ID из chrome://extensions>'
$hostExe = (Resolve-Path '.\.venv\Scripts\multi-llm-native-host.exe').Path
.\scripts\register_native_host.ps1 -ExtensionId $extensionId -HostExePath $hostExe -WhatIf
.\scripts\register_native_host.ps1 -ExtensionId $extensionId -HostExePath $hostExe
.\scripts\check_native_host.ps1 -ExtensionId $extensionId
```

Полная ручная процедура и штатный unregister описаны в `P103_MANUAL_ACCEPTANCE.md`. Native Host по умолчанию использует стабильную базу `%LOCALAPPDATA%\Multi-LLM\research.db`. Для осознанного использования существующей dev-базы задайте абсолютный `MULTI_LLM_RESEARCH_DB`; автоматического переноса данных нет.
