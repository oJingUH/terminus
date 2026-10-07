# TRACEBACK

A single 3D-styled quiz game that unifies three existing CompTIA quiz apps —
CompTIA acronyms, Security+ and Network+ — into one hacker-aesthetic terminal
experience.

TRACEBACK merges the three committed source banks into one generated bundle
(`data/bundle.json`), then serves it through two front-ends (a pure-stdlib
terminal edition and a PySide6 3D edition) that share one engine module
(`traceback.py`) so they can never drift.

```
traceback/
  build_bundle.py             merge the three banks -> data/bundle.json
  traceback.py                shared engine (loader, modes, grading, stats)
  achievements.py             achievement defs + check logic
  traceback_cli.py            terminal edition (stdlib, no TUI framework)
  traceback_gui.py            3D edition (PySide6 / Qt6, QGraphicsView)
  verify_bundle.py            strict integrity checker for the bundle
  verify_negative_controls.py prove the verifier rejects mutations
  data/bundle.json            generated bundle (committed)
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

## Build

```sh
/usr/bin/python3 build_bundle.py
```

This reads the three source banks (read-only) and writes `data/bundle.json`,
tagging every question with a `game` (`a+1` | `a+2` | `net` | `sec`) and a
`domain` (1..5, null for the acronym bank). A CompTIA-acronyms question tagged
with several exams is emitted once per exam tag, so a question tagged
`[aplus1, aplus2]` appears under both games. `source` and `verify` are copied
untouched.

## Run

```sh
/usr/bin/python3 traceback_cli.py                # interactive terminal edition
/usr/bin/python3 traceback_gui.py                # 3D edition (needs a display)

# self-tests (all stdin-free)
/usr/bin/python3 traceback_cli.py --selftest
/usr/bin/python3 traceback_cli.py --selftest --sector net
/usr/bin/python3 traceback_cli.py --selftest --cipher
/usr/bin/python3 traceback_cli.py --selftest --ports
/usr/bin/python3 traceback_cli.py --selftest --crypto
/usr/bin/python3 traceback_cli.py --selftest --review
/usr/bin/python3 traceback_cli.py --selftest --breach net

# verify + negative controls
/usr/bin/python3 verify_bundle.py
/usr/bin/python3 verify_negative_controls.py

# headless GUI screenshots
QT_QPA_PLATFORM=offscreen /usr/bin/python3 traceback_gui.py \
    --selftest --screenshot /home/ojinguh/traceback/preview
```

## Verify

`verify_bundle.py` mirrors the check classes in the source projects'
`verify_bank.py`: token/cell-boundary grounding (never raw substring), answer
mirror, per-game count floors, and per-bank id uniqueness. It resolves every
citation against the Obsidian vault, remapping CompTIA-acronyms legacy paths to
the `CompTIA/Abbreviations/` notes and prefixing Network+/Security+ citations.

## Current state

Working end-to-end:

  * `build_bundle.py` merges 2,486 questions (a+1=522, a+2=500, net=586,
    sec=878) and three drill tables (ports=22, crypto=44, acronyms=614).
  * Every `traceback_cli.py` self-test exits 0.
  * `verify_negative_controls.py` exits 0 — the verifier rejects all four
    mutation shapes (answer-index flip, verify-token, source-section,
    drill-entry).
  * The GUI renders offscreen and writes non-empty PNGs (boot, question,
    ACCESS GRANTED / TRACE DETECTED, achievements).
  * The acronym bank is current with the vault notes as of the N10-009
    Document Version 6.0 / SY0-701 Document Version 7.0 revisions (drift
    corrected in comptia-acronyms 57debac).

## License

MIT — see LICENSE.
