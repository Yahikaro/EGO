# Политика безопасности / Security policy

## Русский

**Поддерживаемые версии.** Исправления безопасности получает только последний выпуск. Публичных выпусков пока нет.

**Как сообщить об уязвимости.** Не открывайте публичный issue. Сообщите приватно через GitHub: вкладка **Security** → **Report a vulnerability** (Private vulnerability reporting). Укажите:
- краткое описание проблемы и её последствия;
- шаги для воспроизведения или proof of concept;
- версию и редакцию Windows, на которых проверяли.

Пожалуйста, дайте владельцу время на исправление, прежде чем раскрывать проблему публично.

**Что входит в область.** API Core, вход и сессии, шифрование данных, Control Center, клиент, доступ с других устройств и туннель.
**Что не входит.** Сторонние программы (llama.cpp, Cloudflare, модели), если проблема не возникает из-за того, как EGO их использует.

**Встроенные защиты.** Пароли хранятся как соленые хеши scrypt, коды входа и токены сессий — только как хеши. Блокировки после повторных неверных кодов и паролей, ограничение частоты попыток входа. База зашифрована, ключи защищены Windows (DPAPI). Доступ с других устройств — только по HTTPS; туннель выключен по умолчанию.

## English

**Supported versions.** Only the latest release receives security fixes. There is no public release yet.

**How to report a vulnerability.** Do not open a public issue. Report it privately on GitHub: the **Security** tab → **Report a vulnerability** (private vulnerability reporting). Include:
- a short description of the problem and its impact;
- steps to reproduce, or a proof of concept;
- the version and Windows edition you tested on.

Please give the owner time to fix the problem before disclosing it publicly.

**In scope.** The Core API, sign-in and sessions, data encryption, the Control Center, the client, and home-network access and the tunnel.
**Out of scope.** Third-party software (llama.cpp, Cloudflare, models), unless the problem comes from how EGO uses it.

**Built-in protections.** Passwords are stored as salted scrypt hashes; sign-in codes and session tokens only as hashes. Lockouts after repeated wrong codes or passwords and a rate limit on sign-in attempts. The database is encrypted and its keys are protected by Windows (DPAPI). Access from other devices uses HTTPS only; the tunnel is off by default.
