# История изменений / Changelog

## [0.1.7.dev1] — первый кандидат в некоммерческий выпуск / first non-commercial release candidate (2026-11-05)

Клиент / Client: 0.9.3.

### Русский
**Добавлено**
- Пресеты настроек: пресет на каждую историю, четыре пресета сервера (Баланс, Длинные сцены, Короткие ответы, Экономия VRAM).
- Память истории: факты каждые 10 сообщений, сжатие в конспект, окно памяти в клиенте.
- Вход по почте и коду, сессии, журнал входов, выход на всех устройствах.
- Доступ из домашней сети по HTTPS и необязательный туннель Cloudflare с секретной фразой.
- «Продолжить» для ответа, оборвавшегося на лимите длины.
- Помощник «Enchant your prompt» для длинных полей с откатом.
- Шаблоны миров и персонажей, случайные планеты для миров.
- Мобильный интерфейс для телефонов.
- Каталог кодов ошибок с текстом и подсказками на русском и английском.
- Проверка конфигурации при запуске (`core check`).
- Ротация журналов.

**Изменено**
- Быстрее запускаются Core и Control Center; exe собирается через PyInstaller.
- Персонажи и миры хранятся в Core и синхронизируются с клиентом.
- Модель сама выбирает короткий или развёрнутый ответ по контексту.

**Безопасность и приватность**
- Клиент не загружает шрифты и никакие сторонние ресурсы.
- Браузер не кэширует ответы API; строгая политика безопасности содержимого.
- Пароль почты шифруется Windows (DPAPI) при первом чтении.
- На устройствах, кроме ПК, локальная копия историй удаляется при выходе или отзыве доступа.

**Известные ограничения:** документ `KNOWN_LIMITATIONS` будет добавлен вместе с кодом проекта.

### English
**Added**
- Story presets: a preset per story, and four server presets (Balance, Long scenes, Short replies, VRAM saver).
- Story memory: facts every 10 messages, compression into a summary, memory window in the client.
- Sign-in by email and code, sessions, sign-in journal, sign-out on all devices.
- Home-network access over HTTPS and an optional Cloudflare tunnel with a secret phrase.
- "Continue" for a reply cut off by the length limit.
- "Enchant your prompt" helper for long fields, with revert.
- Templates for worlds and characters; random planets for worlds.
- Mobile interface for phones.
- Error catalog with text and hints in Russian and English.
- Configuration check at start (`core check`).
- Log rotation.

**Changed**
- Core and Control Center start faster; the exe is built with PyInstaller.
- Characters and worlds are stored in Core and synchronised with the client.
- The model chooses a short or detailed reply based on context.

**Security and privacy**
- The client does not load fonts or any third-party resources.
- The browser does not cache API responses; strict content security policy.
- The mail password is encrypted with Windows (DPAPI) on first read.
- On devices other than the PC, the local copy of stories is removed on sign-out or when access is revoked.

**Known limitations:** the `KNOWN_LIMITATIONS` document will be added together with the project code.
