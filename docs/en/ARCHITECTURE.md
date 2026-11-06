# EGO architecture

[Русский](../ARCHITECTURE.md)

This document describes what EGO consists of and how the parts connect. State as of 2026-11-06, first non-commercial release (core 0.1.7.dev1, client 0.9.3).

## Three parts

| Part | Where in the project | What it does |
| --- | --- | --- |
| **Core** | `server/ego_core/` | Background process: API, database, model, memory, accounts, network. The single source of truth. |
| **Control Center** | `windows_ui/` | Windows window for management: model, presets, logs, settings, home-network access and tunnel. Runs on top of Core. |
| **Client** | `EGO-Client/` (not uploaded to the repository yet) | Web interface served by Core at `/app/`. Opens in a browser. |

The model is not a separate part: Core starts it through `llama-server` (llama.cpp with CUDA) and talks to it over local HTTP.

## Diagram

```
Browser (client /app/)     ──HTTP 127.0.0.1──┐
Phone / laptop             ──HTTPS (home network or tunnel)──┤
                                              ▼
                                       Core (API)
                                       ├── service.py   — stories, messages, generation, streaming
                                       ├── memory.py    — facts and summary per story
                                       ├── presets.py   — model settings presets
                                       ├── accounts.py  — sign-in by email and code, sessions
                                       ├── store.py     — encrypted database (SQLCipher)
                                       ├── content_guard.py — checks for hard limits
                                       ├── lan.py / tunnel.py — access from other devices
                                       └── vault/       — keys, encryption, TLS
                                              │
                                              ▼
                                       llama-server (GGUF model on the GPU)
```

Control Center talks to Core through the management API and keeps no data of its own.

## Where data is stored

- `data\` — encrypted database `main.db` (stories, characters, worlds, memory, account, sign-in journal). Keys are protected by Windows (DPAPI) and can only be opened by the Windows user who created them.
- `settings\` — model profiles, `mail.json` (mail password in encrypted form), `network.json`.
- `logs\` — logs without story text. Rotated on every start: at most 30 files of each kind.
- Browser — a copy of stories for offline client use. Not needed on the PC; on a laptop or phone it is removed on sign-out.

These folders never go into the repository (see `.gitignore`).

## Main Core modules

| File | Purpose |
| --- | --- |
| `api.py` | HTTP API, access checks, security headers |
| `service.py` | Generation, streaming, stop, regenerate, "Continue" |
| `sage_prompt.py` | Builds the system prompt: character, world, memory, role rules |
| `memory.py` | Extracts facts every N messages, compresses them into a summary, token budget |
| `enchant.py` | "Enchant your prompt" helper: improves field text |
| `content_guard.py` | Template-based checks of hard limits on input, output and memory |
| `presets.py` | Four presets: Balance, Long scenes, Short replies, VRAM saver |
| `preflight.py` | Configuration check at start: model, ports, VRAM, disk space |
| `accounts.py`, `mailer.py` | Account, sign-in codes, sessions, sending email |
| `lan.py`, `tunnel.py` | HTTPS access from the home network and the Cloudflare tunnel |
| `errors.py` | Error code catalog with text and hint |
| `store.py`, `keys.py`, `vault/` | Database, keys, encryption, certificates |
| `logrotate.py` | Log rotation |
| `control.py`, `daemon.py` | Commands `core start/stop/status/check/open-client` |

## Security layers

1. **Network.** The API listens on 127.0.0.1 without TLS. For other devices there is a separate HTTPS listener bound only to the PC's private addresses, with a self-signed certificate. The tunnel is off by default.
2. **Sign-in.** Email, a password of at least 10 characters and a 6-digit code from the email. Lockouts: 5 wrong codes — 15 minutes; 10 wrong passwords — 1 hour; overall limit of 30 attempts per minute.
3. **Rights.** An account on another device gets client rights only. Managing Core, issuing launch codes and stopping it are possible only from the PC itself.
4. **Data.** Encrypted database, keys held by Windows, logs without story text.
5. **Content.** Template filter on input, model output and memory. It is a safety net, not a guarantee; the ban on real people is set in the model rules and is not checked by code.

## Known limitations

Full list: `docs/KNOWN_LIMITATIONS.md` (in the project; it will enter the repository with the code). Briefly, for architecture:
- One generation at a time: a second request gets `CORE_BUSY`.
- Deleting a story in Core removes it fully, but the browser copy is removed only on sync.
- A task queue for media generation is not implemented yet.
