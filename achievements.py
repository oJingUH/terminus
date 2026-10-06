#!/usr/bin/env python3
"""
achievements.py — TRACEBACK achievement definitions and check logic.

Pure Python 3 standard library. Imported by BOTH front-ends (traceback_cli.py
and traceback_gui.py) so the unlock rules can never drift between them.

An achievement is a small dictionary:

    {
      "id":      "first_breach",          # stable key stored in stats.achievements
      "name":    "FIRST BREACH",          # short uppercase display name
      "glyph":   "[+]",                    # ASCII glyph shown in the badge
      "line":    "Land your first clean access.",  # one warm, playful line
      "check":   fn(stats) -> bool,        # unlock condition on the stats dict
    }

check() sees ONLY the stats schema (lifetime, per_domain, missed_ids,
best_streak, current_streak, ports, acronyms, crypto, achievements), so an
unlock can be recomputed from any saved stats file at any time — achievements
are derived facts, never separate mutable state.

evaluate(stats) runs every not-yet-earned check and appends newly-unlocked ids
to stats["achievements"], returning the list of newly-earned ids so the caller
can announce them. ascii_badge() renders the framed glyph+name block the GUI
shows on the achievements screen.
"""


# ---------------------------------------------------------------- definitions

def _perfect(bucket, need):
    """True once a drill bucket has cleared `need` answers with zero misses."""
    return bucket["total"] >= need and bucket["correct"] >= bucket["total"]


ACHIEVEMENTS = [
    {
        "id": "first_breach",
        "name": "FIRST BREACH",
        "glyph": "[+]",
        "line": "You slipped past the first firewall. Welcome inside.",
        "check": lambda s: s["lifetime"]["total"] >= 1,
    },
    {
        "id": "streak_5",
        "name": "GHOST RUN 5",
        "glyph": "[5x]",
        "line": "Five clean answers in a row. The logs are still cold.",
        "check": lambda s: s["best_streak"] >= 5,
    },
    {
        "id": "streak_10",
        "name": "GHOST RUN 10",
        "glyph": "[10x]",
        "line": "Ten in a row without leaving a trace. Smooth operator.",
        "check": lambda s: s["best_streak"] >= 10,
    },
    {
        "id": "century",
        "name": "CENTURY MARK",
        "glyph": "[100]",
        "line": "One hundred correct answers banked. Momentum is a weapon.",
        "check": lambda s: s["lifetime"]["correct"] >= 100,
    },
    {
        "id": "port_master",
        "name": "PORT MASTER",
        "glyph": "[P]",
        "line": "Every port mapped, every service named, nothing missed.",
        "check": lambda s: _perfect(s["ports"], 22),
    },
    {
        "id": "cipher_savant",
        "name": "CIPHER SAVANT",
        "glyph": "[#]",
        "line": "Twenty acronyms cracked without a slip. The cipher is yours.",
        "check": lambda s: _perfect(s["acronyms"], 20),
    },
    {
        "id": "vault_cracked",
        "name": "VAULT CRACKED",
        "glyph": "[$]",
        "line": "Every crypto fact recalled clean. The vault swings open.",
        "check": lambda s: _perfect(s["crypto"], 44),
    },
    {
        "id": "sector_swept",
        "name": "SECTOR SWEPT",
        "glyph": "[S]",
        "line": "You have drilled every domain at least once.",
        "check": lambda s: all(s["per_domain"][d]["total"] >= 1 for d in (1, 2, 3, 4, 5)),
    },
]


def definitions():
    """Return the achievement definitions (a fresh list, safe to iterate)."""
    return list(ACHIEVEMENTS)


# ---------------------------------------------------------------- evaluation

def evaluate(stats):
    """Run every not-yet-earned check and unlock newly-earned achievements.

    Mutates stats["achievements"] in place and returns the ids unlocked on
    THIS call, so callers can print/toast the news. Never raises: a malformed
    stats dict simply earns nothing.
    """
    if not isinstance(stats, dict):
        return []
    earned = set(stats.setdefault("achievements", []))
    newly = []
    for ach in ACHIEVEMENTS:
        if ach["id"] in earned:
            continue
        try:
            ok = ach["check"](stats)
        except (KeyError, TypeError, ValueError):
            ok = False
        if ok:
            earned.add(ach["id"])
            stats["achievements"].append(ach["id"])
            newly.append(ach["id"])
    return newly


# ---------------------------------------------------------------- rendering

def ascii_badge(ach, width=26):
    """Render one achievement as a framed ASCII badge.

    The frame is drawn with box-drawing characters and stays `width` columns
    wide, so a stack of badges lines up cleanly on the achievements screen.
    """
    top = "+" + "-" * (width - 2) + "+"
    body = []
    body.append("| %s %-*s |" % (ach["glyph"], width - 5 - len(ach["glyph"]), ach["name"]))
    # wrap the warm line across the badge width
    line = ach["line"]
    words = line.split()
    cur = ""
    for w in words:
        trial = (cur + " " + w).strip()
        if len(trial) <= width - 4:
            cur = trial
        else:
            body.append("| %-*s |" % (width - 4, cur))
            cur = w
    if cur:
        body.append("| %-*s |" % (width - 4, cur))
    return "\n".join([top] + body + [top])


def ascii_badge_list(width=26):
    """Render every achievement as a joined stack of ASCII badges."""
    return "\n\n".join(ascii_badge(a, width) for a in ACHIEVEMENTS)
