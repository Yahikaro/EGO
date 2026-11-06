# Roadmap

[Русский](../ru/ROADMAP.md)

Open tasks after the first non-commercial release. Order is approximate.

## Before the project is published
1. Tunnel privacy: off by default, sign-in protection through the tunnel, an option for friends (Tailscale or Cloudflare Access).
2. Sign-in protection: review lockouts, password requirements, a journal of wrong attempts.
3. Deleting a story in Core together with its memory and the browser copy.
4. Client sync: keep offline edits or warn before they are replaced.
5. Check the client: `js/app/` is the main interface (the story screen is in `chat-view.js`), but its header still says "offline visual mock". Remove outdated comments and dead code.
6. Client interface: improve the awkward places.
7. Check story memory on a long game (hundreds of messages).
8. Regenerate and "Continue" for any reply, not only the last one.
9. Check the mobile layout on Android.
10. Key backup: a way to recover if the Windows user has changed.
11. Measure presets on another graphics card.
12. Super-admin: keep it in the first release or remove it.
13. Sign-in from a laptop and a phone on real devices.
14. Rebuild `EGO.exe` and `EGO-Core.exe`.
15. Choose a license and upload the code to the repository.

## After the first release
- Importing character cards from popular formats.
- Token and generation speed counter.
- One-click backup and restore of the database.
- Several users on one server (for friends, if you choose this option).
- Client tests for the story screen.
- "Regenerate with a hint" mode.

## Media (for later)
- Voice (TTS): Piper, Kokoro, Silero. The lightest on memory.
- Images: SDXL and similar, asynchronous, in turn with the LLM.
- Video: at the end or on an external service.
- Architecture: separate services managed by Core with a task queue.
- Constraint: RTX 3060 with 12 GB of memory; the model takes most of it.
