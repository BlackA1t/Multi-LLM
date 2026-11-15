# P1.0.4 — некритичный UX backlog

Дата: 2026-11-15
Статус: не блокирует P1.0.4; приоритизация — в будущих UX-этапах без изменения архитектуры research workflow.

## Backlog

1. Добавить просмотр истории immutable response revisions в UI. Сейчас предыдущие версии сохраняются и доступны в SQLite, но интерфейс показывает только текущую revision.
2. Показывать явный прогресс chunked upload/download, кнопку отмены staging upload и состояние безопасного reconciliation после разрыва соединения.
3. Для длинных ответов в Side Panel предложить явное продолжение импорта в Full Workspace без сохранения draft в browser storage.
4. Упростить двухшаговый сценарий `UNAVAILABLE` → `SKIPPED_WITH_REASON`, сохранив два явных серверных перехода и обязательную содержательную причину.
5. Улучшить подсказки при `STALE_REVISION`: объяснять, что текст формы сохранён в памяти, данные перечитаны, а повтор станет новой логической mutation.
6. Провести отдельный accessibility review новых dialogs: focus trap, screen-reader labels/live regions, zoom 200–400%, high-contrast mode и полная клавиатурная навигация.
7. Провести отдельный responsive/overflow review длинных provider names, hash, timestamps, Unicode и 8 MiB preview в Side Panel.
8. При необходимости добавить однократное чтение clipboard по отдельному клику через Native Host. Не добавлять clipboard monitoring, timers или автоматическое сохранение.

## Не входит в backlog

- DOM scraping, content scripts, auto-click/auto-send, чтение cookies или истории официальных веб-чатов.
- Неофициальные provider API, FreeLLMAPI и функциональность P1.0.5.
- Хранение prompt/response draft в Chrome storage, Local/Session Storage или IndexedDB.
