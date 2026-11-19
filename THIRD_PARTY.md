# Сторонние компоненты / Third-party components

EGO использует перечисленные ниже компоненты. Их лицензии остаются за их авторами и не меняются лицензией EGO.
EGO uses the components below. Their licenses belong to their authors and are not changed by EGO's license.

## Python-зависимости / Python dependencies

Устанавливаются из `requirements*.txt` в `.venv`; в репозиторий не входят. / Installed from `requirements*.txt` into `.venv`; not part of the repository.

| Компонент / Component | Лицензия / License | Где используется / Used for |
| --- | --- | --- |
| FastAPI, Starlette, Pydantic, uvicorn, anyio, h11 | MIT / BSD-3-Clause | Core API |
| httpx, httpcore, idna | BSD-3-Clause | HTTP-клиент / HTTP client |
| cryptography | Apache-2.0 OR BSD-3-Clause | TLS, ключи / TLS, keys |
| sqlcipher3 (+ SQLCipher by Zetetic) | zlib (+ BSD-style) | Зашифрованная база / Encrypted database |
| PySide6, shiboken6 (Qt for Python) | LGPL-3.0-only OR GPL-2.0/3.0 | Control Center |
| psutil | BSD-3-Clause | Процессы / Processes |
| certifi | MPL-2.0 | Корневые сертификаты / CA bundle |
| typing_extensions | PSF-2.0 | — |
| pytest, pluggy, iniconfig, Pygments | MIT / BSD-2-Clause | Тесты / Tests |
| PyInstaller (только сборка / build only) | GPL-2.0-or-later с исключением для загрузчика / with the bootloader exception | Сборка `EGO.exe`; исключение разрешает распространять собранные программы под любой лицензией / building `EGO.exe`; the exception allows distributing built programs under any license |

**PySide6 / Qt (LGPL-3.0).** EGO использует PySide6 как динамически подключаемые библиотеки без изменений. При распространении `EGO.exe` (папка `_internal`) нужно приложить текст LGPL-3.0 и указать, где взять исходники Qt for Python: https://code.qt.io/cgit/pyside/pyside-setup.git/ — пользователь вправе заменить эти библиотеки своими.
EGO uses PySide6 unmodified as dynamically loaded libraries. When distributing `EGO.exe` (the `_internal` folder), include the LGPL-3.0 text and point to the Qt for Python sources: https://code.qt.io/cgit/pyside/pyside-setup.git/ — users may replace these libraries with their own.

## Внешние программы / External programs

Не входят в репозиторий; пользователь скачивает их сам. / Not part of the repository; the user downloads them.

| Программа / Program | Лицензия / License | Зачем / Why |
| --- | --- | --- |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) (`llama-server.exe`) | MIT | Запуск модели / Runs the model |
| NVIDIA CUDA runtime (в сборках llama.cpp / in llama.cpp builds) | NVIDIA CUDA EULA | Ускорение на GPU / GPU acceleration |
| [cloudflared](https://github.com/cloudflare/cloudflared) (`runtime\tunnel\cloudflared.exe`) | Apache-2.0 | Туннель для доступа вне дома (по желанию) / Tunnel for access from outside home (optional) |

## Модели / Models

EGO не содержит и не распространяет модели. У каждой GGUF-модели своя лицензия — смотрите карточку модели на Hugging Face. Например, Mistral Nemo 12B (база моделей A и B) выпущена под Apache-2.0; у сборок и смесей могут быть дополнительные условия.
EGO does not include or distribute models. Every GGUF model has its own license — see the model card on Hugging Face. For example, Mistral Nemo 12B (the base of Models A and B) is released under Apache-2.0; merges and fine-tunes may add terms.

## Изображения клиента / Client images

Значки EGO (`client/assets/icons/`) созданы для проекта.
The EGO icons (`client/assets/icons/`) were made for the project.
