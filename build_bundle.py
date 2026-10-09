#!/usr/bin/env python3
"""
build_bundle.py — merge the three quiz banks into one data/bundle.json.

TERMINUS's heart is a single generated bundle. At build time this script
reads the three committed source banks — CompTIA acronyms, Security+ and
Network+ — and emits data/bundle.json:

    {
      "generated_at": "<UTC iso timestamp>",
      "questions": [ {..., "game": "a+1"|"a+2"|"net"|"sec", "domain": 1..5|null} ],
      "drill_tables": { "ports": [...], "crypto": [...], "acronyms": [...] }
    }

Merge rules (all of them mechanical, none of them hand-authored):

  * Every question carries a `game` tag. A CompTIA-acronyms question tagged
    with several `exams` is emitted ONCE PER EXAM TAG, so a question tagged
    [aplus1, aplus2] appears under both the "a+1" and the "a+2" game.
  * The per-game `id` is made globally unique by appending ":<game>", so a
    question that fans out across games can never collide, and network /
    security questions (which share the "d1-001"-style id space) never
    collide with each other either.
  * `domain` is copied verbatim from the Network+/Security+ banks (1..5) and
    is null for CompTIA-acronyms questions (that bank has no exam domains).
  * `source` and `verify` are copied UNTOUCHED — the bundle never rewrites a
    citation or a verify token; verify_bundle.py resolves them against the
    Obsidian vault at check time.
  * The drill tables are the port table (Network+), the crypto table
    (Security+) and the cross-cert acronym master table (CompTIA), copied
    verbatim so the drill modes share one source of truth with the quizzes.

The script is deterministic: re-running it reproduces the bundle exactly,
apart from the `generated_at` timestamp.

Usage:
    python3 build_bundle.py [--out data/bundle.json]
"""

import argparse
import datetime
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(SCRIPT_DIR, "data", "bundle.json")

# Source banks (read only). These are finished, committed projects.
SRC_COMPTIA = os.path.join(SCRIPT_DIR, "..", "comptia-acronyms")
SRC_SECURITY = os.path.join(SCRIPT_DIR, "..", "security-quiz")
SRC_NETWORK = os.path.join(SCRIPT_DIR, "..", "network-quiz")

# exam scope -> game tag. aplus1/aplus2/netplus/secplus collapse onto the
# four TERMINUS games.
GAME_TAG = {
    "aplus1": "a+1",
    "aplus2": "a+2",
    "netplus": "net",
    "secplus": "sec",
}


def load_json(path, label):
    """Load one JSON file, exiting cleanly (no traceback) on any problem."""
    if not os.path.exists(path):
        sys.exit("build_bundle.py: %s not found: %s" % (label, path))
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError):
        sys.exit("build_bundle.py: %s is not valid JSON: %s" % (label, path))
    if not isinstance(data, list) or not data:
        sys.exit("build_bundle.py: %s is empty or malformed: %s" % (label, path))
    return data


def _row(q, game, domain):
    """Copy one question verbatim and add the game tag + a unique id + domain.

    `dict(q)` copies every original field (source and verify included) by
    reference without modifying it, so citations and verify tokens stay
    byte-identical to the source banks.
    """
    row = dict(q)
    row["game"] = game
    row["domain"] = domain
    row["id"] = "%s::%s" % (q["id"], game)
    return row


def build_questions(comptia, security, network):
    """Return the merged, game-tagged question list."""
    questions = []
    for q in comptia:
        exams = q.get("exams") or []
        if not exams:
            sys.exit("build_bundle.py: comptia question %r has no exams tag" % q["id"])
        # One row per exam tag, so [aplus1, aplus2] appears under both games.
        for exam in exams:
            game = GAME_TAG.get(exam)
            if game is None:
                sys.exit("build_bundle.py: comptia question %r has unknown exam %r"
                         % (q["id"], exam))
            questions.append(_row(q, game, None))
    for q in security:
        questions.append(_row(q, "sec", q.get("domain")))
    for q in network:
        questions.append(_row(q, "net", q.get("domain")))
    return questions


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Merge the three quiz banks into data/bundle.json.")
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    comptia = load_json(os.path.join(SRC_COMPTIA, "questions.json"),
                        "comptia-acronyms questions.json")
    security = load_json(os.path.join(SRC_SECURITY, "questions.json"),
                         "security-quiz questions.json")
    network = load_json(os.path.join(SRC_NETWORK, "questions.json"),
                        "network-quiz questions.json")

    ports = load_json(os.path.join(SRC_NETWORK, "ports.json"),
                      "network-quiz ports.json")
    crypto = load_json(os.path.join(SRC_SECURITY, "crypto.json"),
                       "security-quiz crypto.json")
    acronyms = load_json(os.path.join(SRC_COMPTIA, "acronyms.json"),
                         "comptia-acronyms acronyms.json")

    questions = build_questions(comptia, security, network)

    bundle = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "questions": questions,
        "drill_tables": {
            "ports": ports,
            "crypto": crypto,
            "acronyms": acronyms,
        },
    }

    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, indent=2)
        f.write("\n")

    games = {}
    for q in questions:
        games[q["game"]] = games.get(q["game"], 0) + 1
    print("merged %d questions into data/bundle.json" % len(questions))
    print("  per game: " + ", ".join("%s=%d" % (g, games[g]) for g in sorted(games)))
    print("  drill tables: ports=%d crypto=%d acronyms=%d"
          % (len(ports), len(crypto), len(acronyms)))
    print("wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
