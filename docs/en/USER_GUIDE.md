# EGO user guide

[Русский](../ru/USER_GUIDE.md)

How to install, start and use EGO on your computer. Technical details are in `ARCHITECTURE.md`, limitations in `KNOWN_LIMITATIONS.md`.

## What you need

- Windows 10 or 11, 64-bit.
- An NVIDIA graphics card. Tested on an RTX 3060 with 12 GB of memory.
- `llama-server.exe` (llama.cpp for Windows with CUDA). Tested on build b11724, CUDA 12.4.
- A model file in GGUF format. Model A (12B) Q5_K_M is recommended; three more were tested — see [Models](MODELS.md).

The model and `llama-server` are not downloaded automatically: you choose them yourself.

## First start

1. Unpack the `EGO` folder (the client is already inside, in `EGO\client`).
2. Run `START_EGO_SERVER.bat`. On the first run it creates a Python environment (a few minutes) and opens the Control Center.
3. Control Center → **Launch**: set the path to `llama-server.exe` and to the model file.
4. Choose a preset, click "Fill from preset", then "Save profile" and "Apply".
5. Run `START_EGO_CLIENT.bat`. The client opens in the browser.

## Presets

| Preset | Context | Reply length | Speed (RTX 3060) | VRAM |
| --- | --- | --- | --- | --- |
| Balance (recommended) | 8192 | up to 512 tokens | 34 tokens/s | 8.9 GB |
| Long scenes | 16384 | up to 1024 tokens | 31 tokens/s | 9.7 GB |
| Short replies | 4096 | up to 256 tokens | 35.5 tokens/s | 8.2 GB |
| VRAM saver | 4096 | up to 384 tokens | 10.5 tokens/s | 7.2 GB |

A preset can also be changed for a single story: right panel → "Story preset".

## Daily use

- `START_EGO_CLIENT.bat` — starts Core if it is not running and opens the client. No sign-in is needed on this PC.
- `START_EGO_SERVER.bat` — Control Center: model, presets, logs, settings.
- `FORCE_CLOSE_EGO.bat` — emergency stop of EGO if the window stops responding.

## Working in the client

- **Stories.** Created with a character or without one (then the "Narrator" leads). A reply can be stopped, regenerated, or continued if it was cut off by the length limit.
- **Characters and worlds.** A character card holds name, personality, greeting and avatar. A world holds locations and a description. Everything is stored in Core.
- **Enchant.** The ✦ button next to long fields improves the text or writes it from scratch. "Revert" restores the previous text.
- **Story memory.** Every 10 messages the model records important facts. You can correct them, pin them with ★ or delete them. The memory of one story does not leak into another.
- **Profile and settings.** Name, avatar, colour, client look, badge, backup.

## Signing in from another device

Only needed for access from a laptop or phone.

1. In the server window open Access → Accounts, enter an email and a password (at least 10 characters) and press Add. Friends are added the same way: each has their own login. No email is sent.
2. All accounts see the same stories and characters of this installation.
3. In Control Center, turn on Access → Home network and restart Core.
4. On the other device, open `https://<PC address>:18444/app/`. The browser will warn about the certificate; this is normal for a home network.

Details: `LAN_ACCESS.md`.

## Access outside home (tunnel)

The tunnel gives a link that opens the client from anywhere. It is off by default.

- Turned on in Control Center → Access → Tunnel.
- The address is new each time. When Core stops, the tunnel closes.
- Without the secret words in the address the page does not open; after that, sign-in with the account is required.
- Do not forward the link, and keep the tunnel off when you do not need it.

## Security

- On a phone or laptop you can turn on a device PIN and the mode "Do not keep stories on this device".
- BitLocker is recommended for the drive with EGO.

## Backup

1. Stop Core.
2. Copy the `data\` and `settings\` folders in full.

They can only be restored under the same Windows user, because the keys are protected by DPAPI. A copy of the browser history ("Download full copy" in the client settings) does not replace a backup of the database.

## If something goes wrong

- **A reply was cut off.** Click "Continue" or choose a preset with a larger limit.
- **The model does not load.** Check the paths to the model and `llama-server`, busy ports and the amount of VRAM. More: Control Center → Logs and diagnostics.
- **Configuration check.** The command `.venv\Scripts\python.exe -m server.ego_core.control check` shows the model, ports, VRAM and disk space.
- **An error in the client.** The error text and hint are shown in the interface. Full list of codes: `ERROR_CODES.md`.

## More in the client

- **Templates.** «Start from a template» when creating a world (4 worlds) or a character (4 characters).
- **Planets.** «Random planet» builds a world picture from layers: 11 surface types, clouds, caps, storms, rings, moons. You choose the glow.
- **The card sets the character.** The model plays the character as described. If a character should be open to romance, say so in the personality and give an age (of legal age).
- **Phone.** Below 760 px the client becomes a phone app: tabs at the bottom, the story list over the screen, a «⋯» menu with scene tools, full-screen windows.

## Device protection (phone and laptop)

- **Device PIN** (Settings): the client locks when opened and after 5–60 minutes idle. 5 wrong PINs sign the device out and erase the browser copy of stories. Works over `https://` (home network or tunnel).
- **«Do not keep stories on this device»**: the browser copy of stories, characters and worlds is erased when the tab closes and when the client locks; everything loads from Core next time.
- **Sessions** on other devices last 7 days from the last activity.
