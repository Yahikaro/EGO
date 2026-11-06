# Руководство разработчика EGO

[English](../en/DEVELOPER_GUIDE.md)

Как настроить окружение, запустить тесты, собрать exe и проверить релиз. Для пользователей см. `USER_GUIDE.md`, для устройства системы — `ARCHITECTURE.md`.

## Структура папок

```
EGO/
├── server/ego_core/   Core: API, база, модель, память, аккаунты, сеть
├── windows_ui/        Control Center (PySide6)
├── EGO-Client/        веб-клиент (js/app, js/core, стили)
├── tests/             тесты: server/, gui/, acceptance/, ui/
├── tools/windows/     BAT-скрипты: установка, тесты, диагностика
├── scripts/           служебные скрипты: smoke, экспорт контрактов, замеры
├── packaging/         сборка exe (PyInstaller)
├── docs/              документация (design/, history/, contracts/)
├── START_EGO_SERVER.bat, START_EGO_CLIENT.bat, FORCE_CLOSE_EGO.bat
└── data/, settings/, logs/, runtime/   данные установки (не в репозитории)
```

## Окружение

1. Python 3.12 x64.
2. `tools\windows\01_SETUP.bat` — создаёт `.venv` и ставит зависимости.
3. `tools\windows\02_ENV_CHECK.bat` — проверяет окружение.

Зависимости: `requirements.txt` (Core), `requirements-gui.txt` (Control Center), `requirements-windows.txt`.

## Тесты

Полный набор:

```bash
tools\windows\03_RUN_ALL_TESTS.bat
```

Или напрямую из корня:

```bash
.venv\Scripts\python.exe -m pytest
```

Ожидаемый результат на момент 2026-11-06: сервер 503 passed, 0 failed (с учётом пропусков по ОС), клиент: три набора 10 + 11 + 7. Точные числа меняются — сверяйте с `CHANGELOG.md`.

Клиентские тесты лежат в `EGO-Client/tests/` и запускаются через Node.js.

## Проверка на реальной модели

`scripts\smoke_release.py` поднимает Core с временной базой, не трогая данные пользователя, и проверяет 12 сценариев: загрузка, ответ, стрим, остановка, перегенерация, «Продолжить», пресет, запреты, память, доступ с другого устройства. Core пользователя при этом должен быть остановлен.

```bash
.venv\Scripts\python.exe scripts\smoke_release.py
```

## Проверка конфигурации

```bash
.venv\Scripts\python.exe -m server.ego_core.control check
```

## Сборка exe

Exe собирается последним шагом перед публикацией. Пересборка нужна после любых изменений кода Core или Control Center.

```bash
.venv\Scripts\python.exe packaging\build_exe.py --install
```

Перед сборкой остановите Core. Скрипт собирает `EGO.exe` (Control Center) и `EGO-Core.exe` (фоновый Core) из PyInstaller onedir. В пакете не должно быть `data\`, `settings\`, `logs\`, `.venv\`, ключей и почтовых данных; скрипт это проверяет.

Клиент exe не собирается: он открывается через `/app/` из Core.

## Контракты API

- `scripts\export_openapi.py` — выгружает снимок контракта API (`docs/contracts/m1-openapi.json`).
- `scripts\export_error_codes.py` — выгружает каталог кодов ошибок в `docs/ERROR_CODES.md`.

Тесты сверяют снимок с кодом. Если меняете API, перевыпустите снимок и опишите изменение в `CHANGELOG.md`.

## Правила работы с кодом

- Изменения обратно совместимы: поля API добавляются, существующие не удаляются.
- Новые коды ошибок сначала добавляются в `server/ego_core/errors.py` с текстом на русском и английском.
- Персональные данные, пароли, токены и пути на чужих машинах не коммитятся. Проверяйте перед коммитом.
- Журналы не должны содержать текст историй.
- Значения по умолчанию в `CoreConfig` не меняются ради совместимости: новые возможности задаются через пресеты и настройки.

## Документы

| Документ | Для чего |
| --- | --- |
| `docs/ARCHITECTURE.md` | Устройство системы |
| `docs/USER_GUIDE.md` | Руководство пользователя |
| `docs/KNOWN_LIMITATIONS.md` | Известные ограничения |
| `docs/RELEASE_READINESS.md` | Критерии готовности версии |
| `docs/ERROR_CODES.md` | Коды ошибок |
| `docs/MAIL_SETUP.md` | Настройка почты |
| `docs/LAN_ACCESS.md` | Доступ с ноутбука |
| `docs/design/` | Проектные решения (память, аккаунты, структура) |
| `docs/history/` | Архив прежних заметок и журнал синхронизации |
