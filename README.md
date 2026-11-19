# EGO

[English](#english)

## Русский

EGO — локальное приложение для длинных ролевых историй с языковой моделью. Модель работает на вашей видеокарте, истории хранятся в зашифрованной базе на вашем компьютере. Все действия в историях вымышленные. Жёсткие запреты действуют всегда: несовершеннолетние, реальные люди, содержание без согласия.

**Версия 1.0.0.** Лицензия — [PolyForm Noncommercial 1.0.0](LICENSE) (`PolyForm-Noncommercial-1.0.0`): программа некоммерческая, донаты — добровольная поддержка автора, а не плата за программу. Автор и владелец: Yahikaro (© 2026).

Скриншоты будут добавлены позже.

*Скриншоты будут добавлены позже.*

### Что умеет

- Истории с персонажами и без них, групповые сцены, главы, закладки, заметка сцены.
- Память истории: важные факты записываются между ответами, их можно править и закреплять.
- Миры с каноном и локациями, процедурные планеты, шаблоны миров и персонажей, помощник «Enchant» для карточек.
- Ответ в реальном времени, «Остановить», «Перегенерировать», «Продолжить»; защита от зацикливания модели.
- Профиль, облик клиента и значок, темы-настроения, мобильная раскладка.
- Пять языков: русский, английский, французский, испанский, немецкий — интерфейс, сообщения сервера, письма и ответы модели.
- Вход с ноутбука или телефона: по HTTPS в домашней сети или через туннель Cloudflare с секретной ссылкой; вход по почте и паролю (аккаунты создаёт владелец в окне сервера).
- Безопасность: зашифрованная база (SQLCipher), ключи под защитой Windows (DPAPI), PIN и режим «не хранить истории» на устройствах.

### Быстрый старт

1. Windows 10/11 x64 и видеокарта NVIDIA (проверено на RTX 3060 12 ГБ).
2. Скачайте `EGO-Setup.exe` из раздела Releases и запустите: выберите папку и компоненты. Установщик может сам скачать llama.cpp (запуск моделей), cloudflared (туннель) и поставить озвучку.
3. Скачайте модель GGUF (EGO её не содержит) и положите в папку `models` внутри EGO — см. [Модели](docs/ru/MODELS.md).
4. Откройте «EGO» из меню «Пуск»: во вкладке «Запуск» выберите модель и пресет, нажмите «Применить». Во вкладке «Доступ» добавьте аккаунт для входа с других устройств.
5. «EGO Client» из меню «Пуск» откроет клиент в браузере.

Запуск из исходников (для разработчиков): Python 3.12 x64, `START_EGO_SERVER.bat` и `START_EGO_CLIENT.bat`.

Подробно — в [руководстве пользователя](docs/ru/USER_GUIDE.md).

### Структура

| Папка | Что внутри |
| --- | --- |
| `server/` | EGO Core: API, хранилище, память, аккаунты, туннель, фильтры содержания |
| `runtime/` | Адаптер llama.cpp (запуск `llama-server`, стрим, отмена) |
| `windows_ui/` | Control Center (PySide6) |
| `client/` | Клиент (HTML/JS без сборки), Core раздаёт его по адресу `/app/` |
| `scripts/`, `tools/`, `packaging/` | Проверки, утилиты, сборка `EGO.exe` |
| `tests/` | Тесты сервера; тесты клиента — в `client/tests/` |
| `docs/` | Документация на русском и английском, коды ошибок, контракт API |

### Документы

| Документ | Для кого |
| --- | --- |
| [Руководство пользователя](docs/ru/USER_GUIDE.md) | Установка, запуск, работа в клиенте, безопасность, резервные копии |
| [Архитектура](docs/ru/ARCHITECTURE.md) | Части системы, их связи, хранение данных |
| [Руководство разработчика](docs/ru/DEVELOPER_GUIDE.md) | Окружение, тесты, проверка на модели, сборка exe, правила |
| [Модели](docs/ru/MODELS.md) | Какие модели проверены и какие подходят |
| [Частые вопросы](docs/ru/FAQ.md) | Короткие ответы на типичные вопросы |
| [Дорожная карта](docs/ru/ROADMAP.md) | Что дальше |
| [Сторонние компоненты](THIRD_PARTY.md) | Лицензии зависимостей и внешних программ |

Справочники: [коды ошибок](docs/ERROR_CODES.md), [доступ из сети](docs/LAN_ACCESS.md), [известные ограничения](docs/KNOWN_LIMITATIONS.md), [API](docs/contracts/m1-openapi.json).

### Участие и безопасность

Правила участия — [CONTRIBUTING.md](CONTRIBUTING.md). Об уязвимостях сообщайте приватно — [SECURITY.md](SECURITY.md).

---

## English

EGO is a local app for long roleplay stories with a language model. The model runs on your GPU; stories are stored in an encrypted database on your computer. All actions in stories are fictional. Hard limits always apply: minors, real people, anything without consent.

**Version 1.0.0.** License: [PolyForm Noncommercial 1.0.0](LICENSE) (`PolyForm-Noncommercial-1.0.0`): the program is non-commercial, donations are voluntary support for the author, not payment for the program. Author and owner: Yahikaro (© 2026).

Screenshots will be added later.

### Features

- Stories with or without a character, group scenes, chapters, bookmarks, a scene note.
- Story memory: important facts are recorded between replies; you can edit and pin them.
- Worlds with canon and locations, procedural planets, world and character templates, the «Enchant» helper for cards.
- Live streaming, Stop, Regenerate, Continue; protection against model loops.
- Profile, client look and icon, mood themes, a phone layout.
- Five languages: Russian, English, French, Spanish, German — the interface, server messages and the model's replies.
- Sign-in from a laptop or phone: HTTPS on the home network, or a Cloudflare tunnel with a secret link; sign-in with email and password (the owner creates accounts in the server window).
- Security: encrypted database (SQLCipher), keys protected by Windows (DPAPI), a device PIN and a «do not keep stories on this device» mode.

### Quick start

1. Windows 10/11 x64 and an NVIDIA GPU (tested on an RTX 3060 12 GB).
2. Download `EGO-Setup.exe` from Releases and run it: pick a folder and components. The setup can download llama.cpp (runs the models) and cloudflared (tunnel) and install voice output for you.
3. Download a GGUF model (EGO does not include one) and put it into the `models` folder inside EGO — see [Models](docs/en/MODELS.md).
4. Open "EGO" from the Start menu: in the Launch tab choose the model and a preset, press Apply. In the Access tab add an account for other devices.
5. "EGO Client" from the Start menu opens the client in your browser.

Running from source (for developers): Python 3.12 x64, `START_EGO_SERVER.bat` and `START_EGO_CLIENT.bat`.

Details: the [user guide](docs/en/USER_GUIDE.md).

### Layout

| Folder | Contents |
| --- | --- |
| `server/` | EGO Core: API, storage, memory, accounts, tunnel, content limits |
| `runtime/` | llama.cpp adapter (starts `llama-server`, streaming, cancel) |
| `windows_ui/` | Control Center (PySide6) |
| `client/` | The client (HTML/JS, no build step), served by Core at `/app/` |
| `scripts/`, `tools/`, `packaging/` | Checks, utilities, building `EGO.exe` |
| `tests/` | Server tests; client tests are in `client/tests/` |
| `docs/` | Documentation in Russian and English, error codes, API contract |

### Documents

| Document | For whom |
| --- | --- |
| [User guide](docs/en/USER_GUIDE.md) | Installation, startup, the client, security, backups |
| [Architecture](docs/en/ARCHITECTURE.md) | The parts of the system and how they connect |
| [Developer guide](docs/en/DEVELOPER_GUIDE.md) | Environment, tests, testing on a real model, building the exe |
| [Models](docs/en/MODELS.md) | Which models are tested and which are suitable |
| [FAQ](docs/en/FAQ.md) | Short answers to common questions |
| [Roadmap](docs/en/ROADMAP.md) | What comes next |
| [Third-party components](THIRD_PARTY.md) | Licenses of dependencies and external programs |

### Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md). Report vulnerabilities privately — see [SECURITY.md](SECURITY.md).
