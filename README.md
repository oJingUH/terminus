# TERMINUS

A single, hacker-styled quiz game that unifies three CompTIA study apps —
A+ acronyms, Security+, and Network+ — behind one dimly lit room and a
glowing terminal. Walk your pixel-art avatar up to the computer, log in with
a persistent profile, and grind your way through every cert.

TERMINUS merges the three source banks into one generated bundle
(`data/bundle.json`) and serves it through two front-ends — a pure-stdlib
terminal edition and a PySide6 room-explorer edition — that share one engine
module (`terminus.py`) so they can never drift.

```
terminus/
  terminus.py                shared engine (loader, modes, grading, stats)
  lair.py                    "The Lair" room shell + profile/login system
  achievements.py            achievement defs + check logic
  terminus_cli.py            terminal edition (stdlib, no TUI framework)
  terminus_gui.py            room-explorer edition (PySide6 / Qt6)
  build_bundle.py            merge the source banks -> data/bundle.json
  verify_bundle.py           strict integrity checker for the bundle
  verify_negative_controls.py prove the verifier rejects mutations
  data/bundle.json           generated bundle (committed)
  packaging/                 PyInstaller build script + requirements
```

## Games

  * Daily Uplink — today's 10 questions, identical for everyone, shareable
  * Sector Sweep — drill one cert's questions, per-domain accuracy tracked
  * Cipher Grind — acronym <-> expansion drill, filterable by cert scope
  * Port Scanner — port <-> service drill (Network+)
  * Crypto Vault — algorithm <-> value drill (Security+)
  * Recover Log — replay every missed question across all games
  * Full Breach — timed exam over one cert
  * Player File — lifetime + per-domain + drill accuracy, streak, achievements

## The Lair & profiles

The GUI opens into a pixel-art room: a dimly lit space with a glowing
terminal in the middle. Walk to it with `WASD`/arrow keys, press `E` (or
`Space`) when close, and log in. First time, create a profile (username +
password); afterwards the same credentials restore your progress — stats,
streaks, missed questions and per-domain accuracy.

Passwords are salted and hashed with PBKDF2-HMAC-SHA256 (140k iterations)
and are never stored as plaintext. Profiles live in a per-user data
directory: `%LOCALAPPDATA%\TERMINUS` on Windows, `~/Library/Application
Support/TERMINUS` on macOS, `~/.local/share/terminus` on Linux.

## Settings

The desktop's start menu has a **Settings** entry with a resolution picker:
800x500 up to **1920x1080**. The choice resizes the window and persists to
`<data-dir>/settings.json`, so it survives restarts. Non-3:2 sizes letterbox
the fixed scene with black bars.

## Run from source

Requires Python 3.10+. The terminal edition needs only the standard library;
the GUI needs PySide6.

```sh
pip install PySide6

python terminus_cli.py          # terminal edition
python terminus_gui.py          # room-explorer edition (needs a display)
```

### Windows (no Python)

Grab the prebuilt `Terminus.exe` from the [latest release](../../releases) —
it's a standalone executable, no Python install required. (Your profile
saves to `%LOCALAPPDATA%\TERMINUS` and survives app restarts.)

## Self-tests

```sh
python terminus_cli.py --selftest
python terminus_cli.py --selftest --sector net
python terminus_cli.py --selftest --cipher
python terminus_cli.py --selftest --ports
python terminus_cli.py --selftest --crypto
python terminus_cli.py --selftest --review
python terminus_cli.py --selftest --breach net

# GUI self-test (headless), including the Lair + login flow
QT_QPA_PLATFORM=offscreen python terminus_gui.py --selftest
```

## Development

`data/bundle.json` is generated from three source banks (comptia-acronyms,
security-quiz, network-quiz) that are read-only and live OUTSIDE this repo;
the committed bundle is self-contained, so players never need them.
`build_bundle.py` regenerates the bundle, and `verify_bundle.py` re-checks
every citation against an Obsidian vault passed with `--vault` (a developer
gate, not shipped to players):

```sh
python build_bundle.py
python verify_bundle.py --vault "/path/to/Obsidian Vault"
python verify_negative_controls.py
```

The committed bundle holds 2,486 questions (a+1=522, a+2=500, net=586,
sec=878) and three drill tables (ports=22, crypto=44, acronyms=614).

## Distribution

Standalone executables for Windows and Linux are built by the `release`
workflow whenever a `v*` tag is pushed. To build one locally:

```sh
pip install -r packaging/requirements.txt
python packaging/build.py --version v1.0.0
```

## License

MIT — see LICENSE.
