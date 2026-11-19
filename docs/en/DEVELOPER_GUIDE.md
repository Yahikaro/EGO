# EGO developer guide

[Русский](../ru/DEVELOPER_GUIDE.md)

How to set up the environment, run tests, build the exe and check a release. For users see `USER_GUIDE.md`; for the system design see `ARCHITECTURE.md`.

## Folder layout

```
EGO/
├── server/ego_core/   Core: API, database, model, memory, accounts, network
├── windows_ui/        Control Center (PySide6)
├── client/           web client (js/app, js/core, js/look, css, assets), tests in client/tests
├── tests/             tests: server/, gui/, acceptance/, ui/
├── tools/windows/     BAT scripts: setup, tests, diagnostics
├── scripts/           helper scripts: smoke test, contract export, timing
├── packaging/         exe build (PyInstaller)
├── docs/              documentation (ru/, en/, contracts/)
├── START_EGO_SERVER.bat, START_EGO_CLIENT.bat, FORCE_CLOSE_EGO.bat
└── data/, settings/, logs/, runtime/   installation data (not in the repository)
```

## Environment

1. Python 3.12 x64.
2. `tools\windows\01_SETUP.bat` — creates `.venv` and installs dependencies.
3. `tools\windows\02_ENV_CHECK.bat` — checks the environment.

Dependencies: `requirements.txt` (Core), `requirements-gui.txt` (Control Center), `requirements-windows.txt`.

## Tests

Full set:

```bash
tools\windows\03_RUN_ALL_TESTS.bat
```

Or directly from the root:

```bash
.venv\Scripts\python.exe -m pytest
```

Expected result as of 2026-11-06: server 503 passed, 0 failed (skips depend on the OS); client: three sets 10 + 11 + 7. Exact numbers change — check them against `CHANGELOG.md`.

Client tests are in `client/tests/` and run with Node.js from the `client` folder: `node tests/core-bridge.test.cjs` and so on.

On GitHub the tests run automatically: `.github/workflows/tests.yml` (Windows, Python 3.12, Node.js). Tests that need native DPAPI, SQLCipher or a real model are skipped there with a reason.

## Testing on a real model

`scripts\smoke_release.py` starts Core with a temporary database without touching the user's data, and checks 12 scenarios: loading, reply, streaming, stop, regenerate, "Continue", preset, limits, memory, access from another device. The user's Core must be stopped.

```bash
.venv\Scripts\python.exe scripts\smoke_release.py
```

## Configuration check

```bash
.venv\Scripts\python.exe -m server.ego_core.control check
```

## Building the exe

The exe is built as the last step before publication. A rebuild is needed after any change to Core or Control Center code.

```bash
.venv\Scripts\python.exe packaging\build_exe.py --install
```

Stop Core before building. The script builds `EGO.exe` (Control Center) and `EGO-Core.exe` (background Core) with PyInstaller in onedir mode. The package must not contain `data\`, `settings\`, `logs\`, `.venv\`, keys; the script checks this.

The client is not built as an exe: it is opened at `/app/` from Core.

### Installer

```bash
.venv\Scripts\python.exe packaging\build_installer.py
```

Builds `EGO-Setup.exe` (a setup window: folder, components, shortcuts, uninstalling through "Apps"). It holds the built program and `Uninstall.exe`; no models are included. The logic is covered by `tests/installer`. For scripts: `EGO-Setup.exe --silent --dir D:\EGO [--llama] [--cloudflared] [--tts]`.

## Languages

EGO runs in Russian, English, French, Spanish and German.

- **Client.** Texts are written in Russian in the code. Translations live in `i18n/client/*.tsv`: each line holds the Russian text and 4 translations separated by tabs (`{0}`, `{1}` are numbers and names). `client/js/core/i18n.js` replaces the page's Russian texts using `client/js/i18n/<lang>.js`; stories and user input are never translated. After changing client texts:
  `.venv\Scripts\python.exe scripts\i18n_extract.py` → add lines to the TSV → `.venv\Scripts\python.exe scripts\i18n_build.py`. The test `test_every_client_text_has_a_translation` fails on a text without a translation.
- **Server.** The client sends its language in the `X-EGO-Lang` header. Error texts (`server/ego_core/errors.py` + `errors_i18n.py`) come in that language; the model replies, chat memory and Enchant use it too.
- **Control Center.** Texts are (Russian, English) pairs; French, Spanish and German are in `windows_ui/translations.py` (keyed by the English text).
- **The content filter** (`content_guard.py`) understands Russian only. In other languages the hard limits rely on the model's prompt rules.

## API contracts

- `scripts\export_openapi.py` — exports the API contract snapshot (`docs/contracts/m1-openapi.json`).
- `scripts\export_error_codes.py` — exports the error code catalog to `docs/ERROR_CODES.md`.

Tests compare the snapshot with the code. If you change the API, regenerate the snapshot and describe the change in `CHANGELOG.md`.

## Code rules

- Changes are backward compatible: fields are added, existing ones are not removed.
- New error codes are first added to `server/ego_core/errors.py` with text in Russian and English.
- Personal data, passwords, tokens and paths from other machines are not committed. Check before every commit.
- Logs must not contain story text.
- Default values in `CoreConfig` are not changed for compatibility: new features are controlled through presets and settings.

## Documents

| Document | Purpose |
| --- | --- |
| `docs/en/ARCHITECTURE.md` | How the system is built |
| `docs/en/USER_GUIDE.md` | User guide |
| `docs/KNOWN_LIMITATIONS.md` | Known limitations |
| `docs/RELEASE_READINESS.md` | Release readiness criteria |
| `docs/ERROR_CODES.md` | Error codes |
| `docs/LAN_ACCESS.md` | Access from a laptop |
