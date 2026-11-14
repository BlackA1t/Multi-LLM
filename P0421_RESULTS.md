# Multi-LLM — P0.4.2.1 результаты

Дата: 14 ноября 2026 года. Статус: `OFFLINE_TESTED`. OAuth и модельные API не вызывались.

## Подтверждённая причина

Фактический backend проекта: `keyring.backends.Windows.WinVaultKeyring`.

`keyring` передаёт Python `str` в `win32ctypes` как `wchar_t[]`; `CredentialBlobSize` вычисляется в байтах без завершающего NUL. Windows ограничивает `CREDENTIALW.CredentialBlobSize` значением `CRED_MAX_CREDENTIAL_BLOB_SIZE = 5 * 512 = 2560` байт.

Контрольная проверка на текущем Windows Credential Manager:

- искусственная строка 1000 ASCII-символов → 2000 UTF-16LE байт → записана и прочитана;
- искусственная строка 1400 ASCII-символов → 2800 UTF-16LE байт → воспроизведён WinError 1783;
- контрольные записи удалены в `finally`.

Следовательно, ошибка сохранения SIWC token set вызвана превышением 2560-байтного Windows credential blob после преобразования строки backend-ом, а не ошибкой OAuth callback.

## Исправление

`CredentialStore` теперь использует только Windows Credential Manager, но хранит длинный секрет как версионированный комплект:

1. Исходная строка кодируется UTF-8, затем Base64.
2. Base64 разбивается на фрагменты не более 1000 ASCII-символов — 2000 UTF-16LE байт на запись при лимите 2560.
3. Каждый фрагмент записывается под уникальным generation ID и читается обратно.
4. Manifest хранит формат, generation ID, число фрагментов, исходную длину и SHA-256.
5. Полный комплект восстанавливается и проверяется до публикации.
6. Отдельная малая запись active pointer атомарно переключается на новую версию.
7. Только после успешного переключения удаляются предыдущая версия и legacy single-entry secret.
8. При любом отказе до переключения прежняя версия остаётся доступной; частично записанная новая версия очищается.

`get()`, `set()`, `set_atomic()` и `delete()` сохранили прежний интерфейс. Старые одиночные keyring-записи читаются без миграции и удаляются после первой успешной атомарной записи новой версии.

Размер ограничен 1 МиБ исходных UTF-8 данных как защита от ошибочного создания чрезмерного количества Credential Manager записей. Для OAuth-наборов это имеет большой запас.

## Безопасная диагностика

Перед сохранением `auth-chatgpt` сообщает только:

- `utf8_bytes`;
- `direct_windows_blob_bytes`;
- `chunk_count`;
- `max_chunk_windows_blob_bytes`.

Токены, фрагменты, Base64 и SHA-256 в CLI не выводятся. Ошибки backend оборачиваются сообщениями без secret value и без вывода содержимого OAuth-набора.

## Атомарность и конкурентность

- Межпроцессная file-lock и async-lock refresh lifecycle не изменены.
- Ротированный token set сначала полностью сохраняется и проверяется под новым generation ID.
- Active pointer переключается одной небольшой Credential Manager записью.
- Ошибка записи фрагмента, manifest, проверки или pointer не уничтожает предыдущий комплект.
- Неактивные фрагменты удаляются после переключения; `delete()` очищает active generation, известные stale generations, pointer и legacy entry.

## Тесты

Полный прогон на Windows с реальным backend `keyring.backends.Windows`:

```powershell
.venv\Scripts\python.exe -m pytest --basetemp .pytest-tmp
```

Результат: `46 passed in 3.58s`.

Проверено:

- короткий synthetic secret;
- token set существенно длиннее 2560 байт;
- ротация 12–15 КБ комплектов;
- сохранение предыдущей версии при искусственном отказе active pointer;
- SHA-256/length/Base64 integrity и обнаружение повреждения;
- совместимость legacy single-entry записи;
- полная очистка pointer, manifest и всех известных chunks;
- отсутствие искусственного sensitive value в тексте исключения;
- непосредственное воспроизведение WinError 1783 выше Windows blob limit.

Все интеграционные записи использовали уникальный тестовый service ID и искусственные строки `A/B/C/L/S`; они удалены после проверки.

## Изменённые файлы

- `src/multi_llm/secrets.py` — versioned chunk storage, manifest, integrity, atomic pointer, legacy compatibility и size diagnostics.
- `src/multi_llm/errors.py` — безопасные ошибки Credential Manager и integrity.
- `src/multi_llm/cli.py` — числовая диагностика размера и обработка ошибки сохранения без traceback с токенами.
- `tests/test_credential_store.py` — unit/regression тесты хранилища.
- `tests/test_windows_credential_backend.py` — проверка реального Windows backend.
- `README.md` — описание лимита, chunking и атомарной ротации.
- `P0421_RESULTS.md` — настоящий отчёт.

## Готовность

Исправление готово. Пользователь может самостоятельно повторить `multi-llm auth-chatgpt`. В рамках P0.4.2.1 настоящая OAuth-авторизация и модельные запросы не выполнялись.
