# P1.0.3 — ручная Chrome Runtime Acceptance

Эта процедура нужна, потому что автоматическое управление окном Chrome было безопасно остановлено: URL окна не удалось достоверно определить. Все действия ниже выполняет пользователь в отдельном Chrome-профиле. Используются только синтетические данные.

## 1. Сборка

В PowerShell из `C:\Multi-LLM`:

```powershell
Set-Location C:\Multi-LLM\extension
npm ci
npm test
npm run build
```

Ожидается успешная сборка `C:\Multi-LLM\extension\dist`.

## 2. Изолированный Chrome и фактический ID

Закройте ранее открытый тестовый Chrome. Затем запустите отдельный профиль:

```powershell
& 'C:\Program Files\Google\Chrome\Application\chrome.exe' `
  '--user-data-dir=C:\Multi-LLM\.chrome-p103-profile-manual' `
  '--no-first-run' '--no-default-browser-check' 'chrome://extensions/'
```

1. Включите «Режим разработчика».
2. Нажмите «Загрузить распакованное расширение» и выберите `C:\Multi-LLM\extension\dist`.
3. Убедитесь, что карточка `Multi-LLM Research` не содержит ошибок.
4. Скопируйте показанный Chrome ID — ровно 32 строчные буквы `a-p`.
5. До регистрации Host нажмите toolbar action. Side Panel должен показать «Python Host не подключён» и кнопку повторного подключения.

Не используйте ID из примеров этого документа.

## 3. Регистрация Host

Подставьте фактический ID:

```powershell
$extensionId = '<ФАКТИЧЕСКИЙ_ID_ИЗ_CHROME>'
$hostExe = (Resolve-Path 'C:\Multi-LLM\.venv\Scripts\multi-llm-native-host.exe').Path

& 'C:\Multi-LLM\scripts\register_native_host.ps1' `
  -ExtensionId $extensionId -HostExePath $hostExe -WhatIf

& 'C:\Multi-LLM\scripts\register_native_host.ps1' `
  -ExtensionId $extensionId -HostExePath $hostExe

& 'C:\Multi-LLM\scripts\check_native_host.ps1' -ExtensionId $extensionId
```

`check_native_host.ps1` проверяет точное `allowed_origins`, HKCU manifest, существование `.exe`, `system.hello`, framing stdout, пустой stderr и синтетический workflow через отдельную временную SQLite.

## 4. Runtime-сценарии

После регистрации вернитесь в Side Panel и нажмите «Повторить подключение».

1. Индикатор меняется на «Python подключён» только после handshake.
2. Создайте сессию:
   - название: `P103 Runtime Test`;
   - вопрос: `Как проверить устойчивость локальной исследовательской сессии к перезапуску?`
3. Проверьте, что сессия появилась в «Истории», а её вопрос и revision читаются после повторного выбора.
4. Нажмите «Начать исследование». Должны появиться четыре реальные задачи: `gemini`, `deepseek`, `qwen`, `grok`; у них нет фиктивных ответов.
5. Нажмите «Пауза», затем «Возобновить». Статус и revision должны измениться после ACK.
6. Кнопка следующего этапа остаётся недоступной, рядом показана причина незавершённого gate. Probe отдельно подтверждает серверный `INVALID_PHASE` без изменения стадии.
7. Нажмите кнопку Workspace в заголовке. Открывается локальный `workspace.html`; история, вопрос, задачи и revision совпадают с Side Panel.
8. Закройте Side Panel, откройте повторно и переключитесь между Side Panel/Workspace. Данные должны перечитываться из Host.
9. Полностью закройте тестовый Chrome и запустите его снова с тем же `--user-data-dir`. Сессия должна сохраниться.
10. Завершите процесс Native Host во время чтения либо временно выполните unregister. UI должен показать отключение; после восстановления регистрации и явного «Повторить подключение» прежняя сессия возвращается.
11. В DevTools расширения проверьте `chrome.storage.local`: допускается только `activeSessionId`; текста вопроса, title, prompts или ответов быть не должно.
12. Проверьте Side Panel при ширине около 320 px и Workspace при широком и узком окне: горизонтального overflow нет, Tab показывает видимый focus.

Сохраните снимки только с указанными синтетическими данными:

- `C:\Multi-LLM\docs\screenshots\p103\sidepanel-connected.png`
- `C:\Multi-LLM\docs\screenshots\p103\workspace-session.png`
- `C:\Multi-LLM\docs\screenshots\p103\host-disconnected.png`

## 5. Штатное удаление регистрации

```powershell
& 'C:\Multi-LLM\scripts\unregister_native_host.ps1' -WhatIf
& 'C:\Multi-LLM\scripts\unregister_native_host.ps1'
```

Скрипт отказывается удалять неизвестный или чужой manifest и сохраняет локальную recovery-копию собственного manifest.

## 6. Что сообщить Codex

Передайте:

- фактический extension ID;
- вывод `check_native_host.ps1`;
- результат каждого пункта 1–12 (`PASS` или точная ошибка);
- подтверждение успешного повторного запуска Chrome;
- наличие трёх файлов скриншотов по указанным путям;
- выполнялся ли unregister после проверки.

До получения этих данных статус остаётся `STATIC VERIFIED — CHROME RUNTIME PENDING`; commit/push не выполняются.
