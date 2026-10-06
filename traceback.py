#!/usr/bin/env python3
"""
traceback.py — TRACEBACK shared game engine.

Pure Python 3 standard library. Holds ALL game logic so traceback_cli.py (the
terminal edition) and traceback_gui.py (the 3D edition) share one code path
and cannot drift. The app's heart is a single generated bundle,
data/bundle.json, built by build_bundle.py from the three committed source
banks (CompTIA acronyms, Security+, Network+).

Responsibilities:
    - load and validate data/bundle.json (clean one-line error + exit 1 on a
      missing or malformed bundle — never a raw traceback), with a staleness
      check against the source banks
    - expose the four games (a+1 / a+2 / net / sec) and the exam->game mapping
    - select questions by game and by domain (1..5, null for the acronym bank)
    - run every game mode: daily_uplink, sector_sweep, cipher_grind,
      port_scanner, crypto_vault, recover_log, full_breach, player_file
    - grade a multiple-choice answer (a-d / 1-4) and a free-text answer
      (case/whitespace/trailing-punctuation tolerant, matching acronyms.py)
    - build drill items for the three drill tables (ports / crypto / acronyms)
      in both directions with accept-lists derived from the source table
    - merge every answer into the shared stats.json schema and evaluate
      achievements (via achievements.py)

Shared engine interface (imported by both front-ends, never an entry point):
    GAMES, GAME_NAMES, GAME_TAGS, EXAM_TO_GAME, DOMAIN_KEYS, GAME_CHOICES
    DEFAULT_BUNDLE
    load_bundle_or_error(path), load_bundle(path), bundle_stale(bundle)
    filter_game(bundle, game), filter_domain(bundle, game, domain)
    select_daily(questions, rng, n), daily_rng(day)
    make_choices(q, rng), parse_key(raw, keys)
    norm_text(s)
    build_drill_items(table, kind, direction), grade_drill(item, raw)
    DIR_MIXED + per-kind direction constants
    empty_stats(), load_stats(path), save_stats(stats, path)
    record_mc(stats, q, correct), record_drill(stats, kind, entry_id, correct)
    collect_missed(stats)
    evaluate_achievements(stats)
"""

import datetime
import json
import os
import random
import re
import sys

import achievements

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BUNDLE = os.path.join(SCRIPT_DIR, "data", "bundle.json")

# Source banks, used only to detect a stale bundle (read only).
_SOURCE_BANKS = [
    os.path.join(SCRIPT_DIR, "..", "comptia-acronyms", "questions.json"),
    os.path.join(SCRIPT_DIR, "..", "comptia-acronyms", "acronyms.json"),
    os.path.join(SCRIPT_DIR, "..", "security-quiz", "questions.json"),
    os.path.join(SCRIPT_DIR, "..", "security-quiz", "crypto.json"),
    os.path.join(SCRIPT_DIR, "..", "network-quiz", "questions.json"),
    os.path.join(SCRIPT_DIR, "..", "network-quiz", "ports.json"),
]

# ---------------------------------------------------------------- games

GAMES = ["a+1", "a+2", "net", "sec"]

GAME_NAMES = {
    "a+1": "A+ Core 1 (220-1101)",
    "a+2": "A+ Core 2 (220-1102)",
    "net": "Network+ (N10-009)",
    "sec": "Security+ (SY0-701)",
}

# Short tags used in compact UI labels.
GAME_TAGS = {"a+1": "A+1", "a+2": "A+2", "net": "NET+", "sec": "SEC+"}

# exam scope (source tag) -> game.
EXAM_TO_GAME = {"aplus1": "a+1", "aplus2": "a+2", "netplus": "net", "secplus": "sec"}

# Friendly (label, key) pairs for the callers' game selectors.
GAME_CHOICES = [(GAME_NAMES[k], k) for k in GAMES]

DOMAIN_KEYS = [1, 2, 3, 4, 5]
DOMAIN_NAMES = {
    1: "Domain 1",
    2: "Domain 2",
    3: "Domain 3",
    4: "Domain 4",
    5: "Domain 5",
}

# ---------------------------------------------------------------- bundle I/O

def load_bundle_or_error(path=DEFAULT_BUNDLE):
    """Return (bundle, error). Never raises: a missing file, invalid JSON, or a
    structurally malformed bundle is reported as a one-line error string."""
    if not os.path.exists(path):
        return None, "bundle file not found: %s (run build_bundle.py)" % path
    try:
        with open(path, encoding="utf-8") as f:
            bundle = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, "bundle is not valid JSON: %s" % path
    if not isinstance(bundle, dict):
        return None, "bundle is malformed (not an object): %s" % path
    for key in ("questions", "drill_tables"):
        if key not in bundle:
            return None, "bundle is missing %r: %s" % (key, path)
    qs = bundle.get("questions")
    if not isinstance(qs, list) or not qs:
        return None, "bundle has no questions: %s" % path
    dt = bundle.get("drill_tables")
    if not isinstance(dt, dict):
        return None, "bundle drill_tables is malformed: %s" % path
    return bundle, None


def load_bundle(path=DEFAULT_BUNDLE):
    """Return the bundle, or exit(1) with a clean one-line message on error."""
    bundle, err = load_bundle_or_error(path)
    if err:
        sys.exit("traceback.py: %s" % err)
    return bundle


def bundle_stale(bundle, path=DEFAULT_BUNDLE):
    """True if the bundle predates any source bank (i.e. it should be rebuilt).

    Only a warning, never fatal: a frozen install may ship the bundle without
    the source banks on disk, and that must still run.
    """
    try:
        bundle_mtime = os.path.getmtime(path)
    except OSError:
        return False
    for src in _SOURCE_BANKS:
        if os.path.exists(src) and os.path.getmtime(src) > bundle_mtime:
            return True
    return False


# ---------------------------------------------------------------- selection

def filter_game(bundle, game):
    """Return every question whose `game` tag matches `game`."""
    return [q for q in bundle["questions"] if q.get("game") == game]


def filter_domain(bundle, game, domain):
    """Return every question in `game` whose `domain` equals `domain`."""
    return [q for q in filter_game(bundle, game) if q.get("domain") == domain]


def daily_rng(day):
    """The Daily Uplink draws, orders and shuffles with this rng. A string
    seed hashes the same on every machine, so everyone playing on the same
    date with the same bundle gets an identical round."""
    return random.Random("traceback-daily-%s" % day.isoformat())


def select_daily(questions, rng, n=10):
    """Return a deterministic sample of `n` questions (same order every call
    for the same rng)."""
    return rng.sample(sorted(questions, key=lambda q: q["id"]), min(n, len(questions)))


def select_review(bundle, stats):
    """Return the mc questions whose id is in stats['missed_ids']."""
    want = set(stats.get("missed_ids") or [])
    return [q for q in bundle["questions"] if q["id"] in want]


# ---------------------------------------------------------------- mc grading

def make_choices(q, rng):
    """Return (keys, shown, correct_key) for a multiple-choice question.

    `shown` is the shuffled list of (key, option_text); `correct_key` maps to
    the correct option. Only 'a'..'d' keys are produced; a 'tf' question keeps
    its two options under 'a'/'b'.
    """
    opts = list(q["options"])
    order = list(range(len(opts)))
    rng.shuffle(order)
    keys = "abcd"[:len(opts)]
    shown = []
    correct_key = None
    for k, oi in zip(keys, order):
        shown.append((k, opts[oi]))
        if oi == q["answer"]:
            correct_key = k
    return list(keys), shown, correct_key


def parse_key(raw, keys):
    """Map a raw answer to a choice key, 'quit', or None (invalid)."""
    a = raw.strip().lower()
    if a in ("q", "quit", "exit"):
        return "quit"
    if a in keys:
        return a
    if a.isdigit():
        idx = int(a) - 1
        if 0 <= idx < len(keys):
            return keys[idx]
    return None


# ---------------------------------------------------------------- free-text grading

def norm_text(s):
    """Normalise a free-text answer for grading (mirrors acronyms.py).

    Tolerant grading stack, cheapest first:
      1. lowercase + collapse all whitespace (case/spacing agnostic)
      2. strip trailing sentence punctuation  ("Identifier." == "Identifier")
      3. drop leading articles                ("the AES" == "AES")
      4. unify word separators                ("point to point" == "point-to-point")
    """
    t = " ".join(str(s).strip().lower().split())
    t = re.sub(r"[.!,;:]+$", "", t)
    t = re.sub(r"^(the|a|an) ", "", t)
    t = re.sub(r"[-/.]+", " ", t)
    return " ".join(t.split())


# ---------------------------------------------------------------- drill items

DIR_MIXED = "mixed"

# per-kind direction constants (mirror the source drill modules).
PORTS_DIRS = ("p2s", "s2p")
CRYPTO_DIRS = ("a2v", "v2a")
ACRONYM_DIRS = ("a2e", "e2a")

_DIRECTION_LABELS = {
    "p2s": "port -> service",
    "s2p": "service -> port",
    "a2v": "algorithm -> value",
    "v2a": "value -> algorithm",
    "a2e": "abbreviation -> expansion",
    "e2a": "expansion -> abbreviation",
}

DRILL_KINDS = ("ports", "crypto", "acronyms")

# Per-kind metadata used to build + grade drill items generically.
_KIND_DIRS = {
    "ports": PORTS_DIRS,
    "crypto": CRYPTO_DIRS,
    "acronyms": ACRONYM_DIRS,
}

KIND_TITLES = {
    "ports": "PORT SCANNER",
    "crypto": "CRYPTO VAULT",
    "acronyms": "CIPHER GRIND",
}

KIND_STATS_KEY = {"ports": "ports", "crypto": "crypto", "acronyms": "acronyms"}


def _accept_display(answer, accept):
    """Human display of a correct answer that may have acceptable alternates."""
    others = [a for a in accept if norm_text(a) != norm_text(answer)]
    if not others:
        return answer
    return "%s (also: %s)" % (answer, ", ".join(others))


# --- ports ----------------------------------------------------------------

def _norm_port(s):
    """Normalise a typed port answer: '20/21', '20-21', '20,21', '20 21' all
    compare equal to the canonical '20/21'."""
    s = str(s).strip().lower()
    for sep in (",", "-", " ", "\t", ";"):
        s = s.replace(sep, "/")
    while "//" in s:
        s = s.replace("//", "/")
    return s.strip("/")


def _service_answer_keys(service):
    """Accepted normalised answers for a service (parenthetical-stripped, and
    each slash-separated component)."""
    keys = set()
    full = norm_text(service)
    keys.add(full)
    core = re.sub(r"\([^)]*\)", "", full)
    core = norm_text(core)
    if core:
        keys.add(core)
        for part in core.split("/"):
            part = part.strip()
            if part:
                keys.add(part)
    return keys


def _port_item(p, direction):
    display = p["display"]
    protocol = p["protocol"]
    service = p["service"]
    alt = p.get("alt_ports", "")
    if direction == "p2s":
        prompt = "Port %s (%s) -> service?" % (display, protocol)
        answer = service
        explanation = "%s %s is %s." % (protocol, display, service)
    else:
        prompt = "Service %s -> port?" % service
        answer = display
        if alt:
            explanation = "%s uses %s port %s (or %s)." % (service, protocol, display, alt)
        else:
            explanation = "%s uses %s port %s." % (service, protocol, display)
    return {
        "id": p["id"], "kind": "ports", "direction": direction,
        "prompt": prompt, "answer": answer, "display": display,
        "service": service, "protocol": protocol, "alt_ports": alt,
        "explanation": explanation, "source": p["source"],
    }


# --- crypto ----------------------------------------------------------------

def _crypto_item(e, direction, accept):
    algorithm = e["algorithm"]
    prop = e["property"]
    value = e["value"]
    is_control = prop in ("control category", "control type")
    if direction == "a2v":
        prompt = "%s - %s?" % (algorithm, prop)
        answer = value
    elif is_control:
        prompt = "Which %s means '%s'?" % (prop, value)
        answer = algorithm
    else:
        prompt = "Which algorithm has a %s %s?" % (value, prop)
        answer = algorithm
    if is_control:
        if direction == "a2v":
            explanation = "%s is a %s meaning '%s'." % (algorithm, prop, value)
        else:
            explanation = "%s is the %s meaning '%s'." % (algorithm, prop, value)
    elif direction == "a2v":
        explanation = "%s has a %s of %s." % (algorithm, prop, _accept_display(value, accept))
    elif len(accept) > 1:
        explanation = "%s each have a %s of %s." % (" and ".join(accept), prop, value)
    else:
        explanation = "%s has a %s of %s." % (algorithm, prop, value)
    return {
        "id": e["id"], "kind": "crypto", "direction": direction,
        "prompt": prompt, "answer": answer, "accept": list(accept),
        "display": e["display"], "algorithm": algorithm, "property": prop,
        "value": value, "explanation": explanation, "source": e["source"],
    }


def _key_size_variants(value):
    m = re.fullmatch(r"\s*(\d+)\s*,\s*(\d+)\s*,\s*and\s*(\d+)\s*bits\s*", value)
    if m:
        return ["%s-bit" % n for n in m.groups()]
    m = re.fullmatch(r"\s*(\d+)\s*bits?\s*", value)
    if m:
        return ["%s-bit" % m.group(1)]
    return [value]


# --- acronyms ----------------------------------------------------------------

def _acronym_item(e, direction, accept):
    acronym = e["acronym"]
    expansion = e["expansion"]
    if direction == "a2e":
        prompt = "What does %s stand for?" % acronym
        answer = expansion
    else:
        prompt = "Which abbreviation stands for '%s'?" % expansion
        answer = acronym
    if len(accept) > 1 and direction == "a2e":
        explanation = "%s stands for %s." % (acronym, _accept_display(expansion, accept))
    elif len(accept) > 1:
        explanation = "%s both stand for %s." % (" and ".join(accept), expansion)
    else:
        explanation = "%s stands for %s." % (acronym, expansion)
    return {
        "id": e["id"], "kind": "acronyms", "direction": direction,
        "prompt": prompt, "answer": answer, "accept": list(accept),
        "display": e["display"], "acronym": acronym, "expansion": expansion,
        "exams": list(e.get("exams") or []), "explanation": explanation,
        "source": e["source"],
    }


def build_drill_items(table, kind, direction=DIR_MIXED):
    """Return drill items for a drill table in the requested direction(s).

    'mixed' yields two items per entry (one each way). Accept-lists are derived
    from the source table, so a prompt never has two different expected answers
    unless every one of them is acceptable (mirrors acronyms.py / crypto.py).
    """
    dirs = _KIND_DIRS[kind]
    items = []
    if kind == "ports":
        for p in table:
            if direction in (DIR_MIXED, "p2s"):
                items.append(_port_item(p, "p2s"))
            if direction in (DIR_MIXED, "s2p"):
                items.append(_port_item(p, "s2p"))
        return items

    if kind == "crypto":
        a2v_accept, v2a_accept = {}, {}
        for e in table:
            k = (e["algorithm"], e["property"])
            a2v_accept.setdefault(k, [])
            if e["value"] not in a2v_accept[k]:
                a2v_accept[k].append(e["value"])
            k2 = (e["property"], e["value"])
            v2a_accept.setdefault(k2, [])
            if e["algorithm"] not in v2a_accept[k2]:
                v2a_accept[k2].append(e["algorithm"])
            if e["property"] == "key size":
                for variant in _key_size_variants(e["value"]):
                    if variant == e["value"]:
                        continue
                    v2a_accept.setdefault((e["property"], variant), [])
                    if e["algorithm"] not in v2a_accept[(e["property"], variant)]:
                        v2a_accept[(e["property"], variant)].append(e["algorithm"])
        for e in table:
            if direction in (DIR_MIXED, "a2v"):
                items.append(_crypto_item(e, "a2v", a2v_accept[(e["algorithm"], e["property"])]))
            if direction in (DIR_MIXED, "v2a"):
                items.append(_crypto_item(e, "v2a", v2a_accept[(e["property"], e["value"])]))
        return items

    # acronyms
    a2e_accept, e2a_accept = {}, {}
    for e in table:
        a2e_accept.setdefault(e["acronym"], [])
        if e["expansion"] not in a2e_accept[e["acronym"]]:
            a2e_accept[e["acronym"]].append(e["expansion"])
        e2a_accept.setdefault(e["expansion"], [])
        if e["acronym"] not in e2a_accept[e["expansion"]]:
            e2a_accept[e["expansion"]].append(e["acronym"])
    for e in table:
        if direction in (DIR_MIXED, "a2e"):
            items.append(_acronym_item(e, "a2e", a2e_accept[e["acronym"]]))
        if direction in (DIR_MIXED, "e2a"):
            items.append(_acronym_item(e, "e2a", e2a_accept[e["expansion"]]))
    return items


def select_drill_items(items, rng, length=None):
    """Return the round pool: all items, or a random sample of `length`
    (sampling is without replacement, so a question never repeats)."""
    if length is None:
        return list(items)
    return rng.sample(items, min(length, len(items)))


def filter_scope(table, game):
    """Filter the acronym master table to a cert scope.

    The acronym table's `exams` list uses source exam tags (aplus1..secplus);
    `game` is the TRACEBACK game. Returns entries whose exams include the
    matching exam tag, or every entry when game is None.
    """
    if game is None:
        return list(table)
    exams = [exam for exam, g in EXAM_TO_GAME.items() if g == game]
    return [e for e in table if any(x in (e.get("exams") or []) for x in exams)]


def grade_drill(item, raw):
    """Return (correct, correct_answer, explanation, source).

    Free-text matching is case-insensitive and tolerant of whitespace /
    trailing punctuation; ports additionally tolerate separator variants and
    parenthetical service notes. The accept list holds every valid answer.
    """
    kind = item["kind"]
    if kind == "ports":
        if item["direction"] == "p2s":
            correct = norm_text(raw) in _service_answer_keys(item["service"])
            correct_answer = item["service"]
        else:
            got = _norm_port(raw)
            want = _norm_port(item["display"])
            alt = _norm_port(item.get("alt_ports", ""))
            correct = got == want or (bool(alt) and got == alt)
            correct_answer = item["display"]
            if item.get("alt_ports"):
                correct_answer = "%s (or %s)" % (item["display"], item["alt_ports"])
    else:
        got = norm_text(raw)
        accept = item.get("accept") or [item["answer"]]
        correct = any(norm_text(a) == got for a in accept)
        correct_answer = _accept_display(item["answer"], accept)
    return correct, correct_answer, item["explanation"], item["source"]


def direction_label(direction):
    return _DIRECTION_LABELS.get(direction, direction)


# ---------------------------------------------------------------- stats

def empty_bucket():
    return {"correct": 0, "total": 0, "missed_ids": []}


def empty_stats():
    """Return the canonical stats.json schema (see module docstring)."""
    return {
        "lifetime": {"correct": 0, "total": 0},
        "per_domain": {d: empty_bucket() for d in DOMAIN_KEYS},
        "missed_ids": [],
        "best_streak": 0,
        "current_streak": 0,
        "ports": empty_bucket(),
        "acronyms": empty_bucket(),
        "crypto": empty_bucket(),
        "achievements": [],
    }


def _coerce_int(value):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return 0


def _coerce_bucket(data, bucket):
    """Copy a {correct,total,missed_ids} bucket out of raw JSON, tolerating
    every malformed/missing field (never raises)."""
    if not isinstance(data, dict):
        return
    bucket["correct"] = _coerce_int(data.get("correct", 0))
    bucket["total"] = _coerce_int(data.get("total", 0))
    mids = data.get("missed_ids")
    if isinstance(mids, list):
        bucket["missed_ids"] = [m for m in mids if isinstance(m, str)]


def load_stats(path):
    """Load stats.json, merging it over a fresh empty schema so older files
    (missing a key) keep working. Never raises."""
    stats = empty_stats()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return stats
    if not isinstance(data, dict):
        return stats

    _coerce_bucket(data.get("lifetime"), stats["lifetime"])
    pd = data.get("per_domain")
    if isinstance(pd, dict):
        for d in DOMAIN_KEYS:
            _coerce_bucket(pd.get(d), stats["per_domain"][d])
    mids = data.get("missed_ids")
    if isinstance(mids, list):
        stats["missed_ids"] = [m for m in mids if isinstance(m, str)]
    stats["best_streak"] = _coerce_int(data.get("best_streak", 0))
    stats["current_streak"] = _coerce_int(data.get("current_streak", 0))
    for key in ("ports", "acronyms", "crypto"):
        _coerce_bucket(data.get(key), stats[key])
    ach = data.get("achievements")
    if isinstance(ach, list):
        stats["achievements"] = [a for a in ach if isinstance(a, str)]
    return stats


def save_stats(stats, path):
    """Write stats.json. Never lets a stats write crash the game."""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def record_mc(stats, q, correct):
    """Record one multiple-choice answer into lifetime / streak / missed_ids,
    plus the per-domain bucket when the question carries a domain."""
    lt = stats["lifetime"]
    lt["total"] += 1
    if correct:
        lt["correct"] += 1
        stats["current_streak"] += 1
        stats["best_streak"] = max(stats["best_streak"], stats["current_streak"])
        if q["id"] in stats["missed_ids"]:
            stats["missed_ids"].remove(q["id"])
    else:
        stats["current_streak"] = 0
        if q["id"] not in stats["missed_ids"]:
            stats["missed_ids"].append(q["id"])
    domain = q.get("domain")
    if domain in DOMAIN_KEYS:
        b = stats["per_domain"][domain]
        b["total"] += 1
        if correct:
            b["correct"] += 1


def record_drill(stats, kind, entry_id, correct):
    """Merge one drill answer into its kind bucket (ports / acronyms / crypto),
    keeping drill accuracy OUT of lifetime so grinding one mode never distorts
    another."""
    bucket = stats[KIND_STATS_KEY[kind]]
    bucket["total"] += 1
    if correct:
        bucket["correct"] += 1
        if entry_id in bucket["missed_ids"]:
            bucket["missed_ids"].remove(entry_id)
    else:
        if entry_id not in bucket["missed_ids"]:
            bucket["missed_ids"].append(entry_id)


def collect_missed(stats):
    """Return the union of missed ids across the mc bank and all three drills."""
    ids = set(stats.get("missed_ids") or [])
    for key in ("ports", "acronyms", "crypto"):
        ids.update(stats.get(key, {}).get("missed_ids") or [])
    return ids


def evaluate_achievements(stats):
    """Run achievement checks and return the ids newly unlocked."""
    return achievements.evaluate(stats)
