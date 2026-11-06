# Changelog

All notable changes are listed here. Versions follow the server (core) version; the client version is listed separately.

## [0.1.7.dev1] — first non-commercial release candidate (2026-11-05)

Client 0.9.3.

### Added
- Story presets: a preset per story, four presets for the server (Balance, Long scenes, Short replies, VRAM saver).
- Story memory: facts recorded every 10 messages, compression into a summary, memory window in the client.
- Sign-in by email and code, sessions, sign-in journal, sign out on all devices.
- Home-network access over HTTPS and an optional Cloudflare tunnel with a secret phrase.
- "Continue" for a reply cut off by the length limit.
- "Enchant your prompt" helper for long text fields, with revert.
- Templates for worlds and characters, random planets for worlds.
- Mobile layout for phones.
- Error catalog with text and hints in Russian and English.
- Configuration check at start (`core check`).
- Log rotation.

### Changed
- Faster start of Core and Control Center; exe builds with PyInstaller.
- Characters and worlds are stored in Core and synchronised with the client.
- The model decides between a short or a detailed reply based on context.

### Security and privacy
- The client does not load fonts or any third-party resources.
- API responses are not cached by the browser; strict content security policy.
- Mail password is encrypted with Windows DPAPI on first read.
- On a non-PC device, the local copy of stories is removed on sign-out or when access is revoked.

### Known limitations
See `docs/KNOWN_LIMITATIONS.md`.
