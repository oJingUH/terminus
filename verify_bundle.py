#!/usr/bin/env python3
"""
verify_bundle.py — integrity checker for data/bundle.json.

Mirrors the check classes in the source projects' verify_bank.py: the same
token-boundary grounding, section extraction, answer parsing and answer-mirror
gate, applied to the single merged bundle. Checks:

  questions:
    - every required field present; `id` unique across the whole bank
    - `type` is mc or tf; mc has exactly 4 unique options, tf has exactly
      ['True', 'False']; the answer index is in range; ANSWER MIRROR
      (options[answer] == answer_text) so a flipped answer index fails
    - every citation's note file and section heading exist in the vault
    - every string in `verify` appears in the cited section at a token/cell
      boundary (NEVER raw substring — "LDAP" can't be satisfied by "LDAPS")
    - per-game count minimums: a+1>=240, a+2>=230, net>=180, sec>=180

  drill tables (ports / crypto / acronyms):
    - required fields; ids unique; `display` values unique
    - citations resolve and every `verify` string grounds (token boundary)
    - ANSWER-BEARING GROUNDING: each answer field is covered by the entry's own
      verify list, so a content field changed while its verify stays intact is
      caught (steward C/D/E)

Vault resolution: the bundle stores each source bank's citation UNTOUCHED, so
this gate maps them to the single Obsidian vault root. Network+ and Security+
citations are relative to their own subfolders ("Network+/...", "Security+/...");
CompTIA-acronyms citations carry legacy paths that are remapped to the
CompTIA/Abbreviations notes (and their section "Common Acronyms" -> "Acronym
List").

Grounding is CELL/TOKEN BOUNDARY based, never raw substring.

Prints a PASS/FAIL summary and exits non-zero on any failure.

Usage:
    python3 verify_bundle.py [--bundle data/bundle.json]
                             [--vault "/path/to/Obsidian Vault"]
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BUNDLE = os.path.join(HERE, "data", "bundle.json")
DEFAULT_VAULT = os.path.expanduser("~/Documents/Obsidian Vault")

# Per-game question-count floors (from the task spec).
GAME_MIN = {"a+1": 240, "a+2": 230, "net": 180, "sec": 180}
GAMES = ["a+1", "a+2", "net", "sec"]

VALID_DIFFICULTY = {"easy", "medium", "hard"}
VALID_TYPES = {"mc", "tf"}

QUESTION_REQUIRED = ["id", "type", "question", "options", "answer",
                     "answer_text", "explanation", "difficulty", "source",
                     "verify", "game", "domain"]
SOURCE_REQUIRED = ["note", "section"]

# Drill-table schemas (kind -> required fields, content fields, answer fields).
DRILL_REQUIRED = {
    "ports": ["id", "display", "protocol", "service", "source", "verify"],
    "crypto": ["id", "display", "algorithm", "property", "value", "source", "verify"],
    "acronyms": ["id", "display", "acronym", "expansion", "source", "verify"],
}
DRILL_CONTENT = {
    "ports": ("protocol", "service"),
    "crypto": ("algorithm", "property", "value"),
    "acronyms": ("acronym", "expansion"),
}
DRILL_ANSWER = {
    "ports": ("display", "service"),
    "crypto": ("algorithm", "value"),
    "acronyms": ("acronym", "expansion"),
}

# CompTIA-acronyms legacy citations (only for that origin) remap to the
# unified vault notes; their section "Common Acronyms" becomes "Acronym List".
LEGACY_NOTE_MAP = {
    "A+ Core 1/Common Acronyms.md": "CompTIA/Abbreviations/A+ 220-1101 Acronyms.md",
    "A+ Core 2/Common Acronyms.md": "CompTIA/Abbreviations/A+ 220-1102 Acronyms.md",
    "Network+/Common Acronyms.md": "CompTIA/Abbreviations/Network+ N10-009 Acronyms.md",
    "Security+/Common Acronyms.md": "CompTIA/Abbreviations/Security+ SY0-701 Acronyms.md",
}
LEGACY_SECTION_MAP = {"Common Acronyms": "Acronym List"}

# ---------------------------------------------------------------- check classes

def parse_answer(answer, num_options, qtype):
    """Return the 0-based index the answer refers to, or None if invalid."""
    if isinstance(answer, int) and not isinstance(answer, bool):
        if 0 <= answer < num_options:
            return answer
        return None
    if isinstance(answer, str):
        a = answer.strip().lower()
        if qtype == "tf" and a in ("t", "true", "f", "false"):
            return 0 if a in ("t", "true") else 1
        if qtype == "mc":
            if a in ("a", "b", "c", "d"):
                return ord(a) - ord("a")
            if a in ("1", "2", "3", "4"):
                return int(a) - 1
        return None
    return None


def section_body(lines, section):
    """Return the body text of a named section (list of file lines -> str),
    or None if the heading does not exist."""
    target = section.strip()
    start = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading == target:
                start = i
                break
    if start is None:
        return None
    body = []
    for line in lines[start + 1:]:
        if line.strip().startswith("#"):
            break
        body.append(line)
    return "\n".join(body)


# A "token" is a maximal run of characters that legitimately appear INSIDE a
# value or identifier: letters, digits, and '/', '+', '&', '-'. Everything else
# is a token boundary. This keeps "AES-256", "SHA-1", "TACACS+", "20/21" as
# single tokens while "LDAP" and "LDAPS" stay distinct tokens.
_TOKEN_RE = re.compile(r"[A-Za-z0-9/+&\-]+")


def _tokenize(text):
    return _TOKEN_RE.findall(text)


def grounded(body_text, value):
    """True if `value` appears in `body_text` as a whole token, or a contiguous
    run of whole tokens (cell/token boundary) — never as a raw substring."""
    needle = _tokenize(value)
    if not needle:
        return False
    hay = _tokenize(body_text)
    n = len(needle)
    for i in range(len(hay) - n + 1):
        if hay[i:i + n] == needle:
            return True
    return False


def contains_ci(text, phrase):
    """Case-insensitive, token-boundary 'phrase appears in text' check."""
    needle = [t.lower() for t in _tokenize(phrase)]
    if not needle:
        return False
    hay = [t.lower() for t in _tokenize(text)]
    n = len(needle)
    for i in range(len(hay) - n + 1):
        if hay[i:i + n] == needle:
            return True
    return False


# ---------------------------------------------------------------- vault resolution

def question_origin(q):
    """'comptia' if the question came from the acronym bank (it carries an
    `exams` tag), else its game's vault prefix ('net' -> Network+, 'sec' ->
    Security+)."""
    if "exams" in q:
        return "comptia"
    return q.get("game")


def resolve_note(note, origin):
    """Map a stored citation note to a vault-root-relative path."""
    if origin == "comptia":
        return LEGACY_NOTE_MAP.get(note, note)
    if origin == "net":
        return "Network+/" + note
    if origin == "sec":
        return "Security+/" + note
    return note


def resolve_section(section, origin):
    if origin == "comptia":
        return LEGACY_SECTION_MAP.get(section, section)
    return section


def _make_note_loader(vault):
    cache = {}

    def get_note_lines(rel_note):
        if rel_note in cache:
            return cache[rel_note]
        path = os.path.join(vault, rel_note.replace("/", os.sep))
        if not os.path.exists(path):
            cache[rel_note] = None
            return None
        with open(path, encoding="utf-8") as f:
            cache[rel_note] = f.read().splitlines()
        return cache[rel_note]

    return get_note_lines


# ---------------------------------------------------------------- questions

def verify_questions(bundle, vault):
    print("=" * 66)
    print("TERMINUS Question Bank Verification")
    print("=" * 66)
    print("vault : %s" % vault)

    errors = []
    questions = bundle["questions"]
    n = len(questions)
    ids = set()
    game_counts = {g: 0 for g in GAMES}
    type_counts = {}
    verify_checks = 0

    get_note_lines = _make_note_loader(vault)

    for idx, q in enumerate(questions):
        loc = "question #%d (id=%r)" % (idx + 1, q.get("id", "?"))

        if not isinstance(q, dict):
            errors.append("%s: not a JSON object" % loc)
            continue
        for field in QUESTION_REQUIRED:
            if field not in q:
                errors.append("%s: missing required field %r" % (loc, field))

        qid = q.get("id")
        if not isinstance(qid, str) or not qid.strip():
            errors.append("%s: 'id' must be a non-empty string" % loc)
        elif qid in ids:
            errors.append("%s: duplicate id %r" % (loc, qid))
        else:
            ids.add(qid)

        game = q.get("game")
        if game not in GAMES:
            errors.append("%s: 'game' must be one of %s" % (loc, "|".join(GAMES)))
        else:
            game_counts[game] += 1

        domain = q.get("domain")
        if domain is not None and (isinstance(domain, bool)
                                   or not isinstance(domain, int)
                                   or domain not in (1, 2, 3, 4, 5)):
            errors.append("%s: 'domain' must be an integer 1..5 or null" % loc)

        qtype = q.get("type")
        if qtype not in VALID_TYPES:
            errors.append("%s: 'type' must be 'mc' or 'tf'" % loc)
        else:
            type_counts[qtype] = type_counts.get(qtype, 0) + 1

        if q.get("difficulty") not in VALID_DIFFICULTY:
            errors.append("%s: 'difficulty' must be easy|medium|hard" % loc)

        options = q.get("options")
        if not isinstance(options, list) or len(options) == 0:
            errors.append("%s: 'options' must be a non-empty list" % loc)
        elif qtype == "mc" and len(options) != 4:
            errors.append("%s: multiple-choice must have exactly 4 options (got %d)"
                          % (loc, len(options)))
        elif qtype == "tf" and (len(options) != 2
                                or [str(o).strip().lower() for o in options] != ["true", "false"]):
            errors.append("%s: true/false options must be ['True','False']" % loc)
        else:
            # exactly-one correct option: options must be unique
            norm = [str(o).strip() for o in options]
            if len(set(norm)) != len(norm):
                errors.append("%s: options contain duplicates" % loc)

        answer = q.get("answer")
        num_options = len(options) if isinstance(options, list) else 0
        ans_idx = None
        if num_options:
            ans_idx = parse_answer(answer, num_options, qtype)
            if ans_idx is None:
                errors.append("%s: answer %r out of range for %s (options=%d)"
                              % (loc, answer, qtype, num_options))

        # ANSWER MIRROR: options[answer] must equal answer_text, so a flipped
        # in-range answer index fails the gate.
        answer_text = q.get("answer_text")
        if not isinstance(answer_text, str) or not answer_text.strip():
            errors.append("%s: 'answer_text' must be a non-empty string" % loc)
        elif ans_idx is not None and isinstance(options, list) \
                and 0 <= ans_idx < len(options):
            if str(options[ans_idx]).strip() != answer_text.strip():
                errors.append("%s: answer mirror mismatch — options[answer]=%r "
                              "but answer_text=%r"
                              % (loc, str(options[ans_idx]).strip(), answer_text.strip()))

        # citation: note file + section heading exist (after origin mapping)
        src = q.get("source")
        if not isinstance(src, dict):
            errors.append("%s: 'source' must be an object" % loc)
        else:
            for field in SOURCE_REQUIRED:
                if field not in src:
                    errors.append("%s: source missing field %r" % (loc, field))

        if isinstance(src, dict) and "note" in src and "section" in src:
            note = src["note"]
            section = src["section"]
            if not isinstance(note, str) or not isinstance(section, str):
                errors.append("%s: source note/section must be strings" % loc)
                continue
            origin = question_origin(q)
            rel_note = resolve_note(note, origin)
            rel_section = resolve_section(section, origin)
            lines = get_note_lines(rel_note)
            if lines is None:
                errors.append("%s: cited note file not found: %r"
                              % (loc, rel_note))
            else:
                body = section_body(lines, rel_section)
                if body is None:
                    errors.append("%s: section heading %r not found in %r"
                                  % (loc, rel_section, rel_note))
                else:
                    verify = q.get("verify")
                    if not isinstance(verify, list):
                        errors.append("%s: 'verify' must be a list of strings" % loc)
                    else:
                        for token in verify:
                            verify_checks += 1
                            if not isinstance(token, str) or not grounded(body, token):
                                errors.append(
                                    "%s: expected value %r not found in %r > %r"
                                    % (loc, token, rel_note, rel_section))

    # per-game floors
    for g in GAMES:
        have = game_counts[g]
        need = GAME_MIN[g]
        if have < need:
            errors.append("game %r: only %d questions (need >= %d)" % (g, have, need))

    print("total : %d questions" % n)
    for g in GAMES:
        have = game_counts[g]
        need = GAME_MIN[g]
        print("  game %-4s : %4d   (target >= %d)  %s"
              % (g, have, need, "OK" if have >= need else "LOW"))
    print("  types   : " + ", ".join("%s=%d" % (k, v) for k, v in sorted(type_counts.items())))
    print("  facts verified against vault : %d" % verify_checks)

    if errors:
        print("-" * 66)
        print("FAIL — %d problem(s) found:" % len(errors))
        for e in errors:
            print("  * %s" % e)
        print("=" * 66)
        return 1
    print("-" * 66)
    print("PASS — all %d questions valid, all citations resolve, all facts "
          "grounded." % n)
    print("=" * 66)
    return 0


# ---------------------------------------------------------------- drill tables

def verify_drill_table(table, kind, vault):
    print("=" * 66)
    print("TERMINUS %s Drill Table Verification" % kind.title())
    print("=" * 66)
    print("vault : %s" % vault)

    errors = []
    n = len(table)
    ids = set()
    displays = set()
    verify_checks = 0
    required = DRILL_REQUIRED[kind]
    content = DRILL_CONTENT[kind]
    answer_fields = DRILL_ANSWER[kind]
    get_note_lines = _make_note_loader(vault)

    for idx, e in enumerate(table):
        loc = "%s #%d (id=%r)" % (kind, idx + 1, e.get("id", "?"))
        if not isinstance(e, dict):
            errors.append("%s: not a JSON object" % loc)
            continue
        for field in required:
            if field not in e:
                errors.append("%s: missing required field %r" % (loc, field))

        eid = e.get("id")
        if not isinstance(eid, str) or not eid.strip():
            errors.append("%s: 'id' must be a non-empty string" % loc)
        elif eid in ids:
            errors.append("%s: duplicate id %r" % (loc, eid))
        else:
            ids.add(eid)

        disp = e.get("display")
        if not isinstance(disp, str) or not disp.strip():
            errors.append("%s: 'display' must be a non-empty string" % loc)
        elif disp in displays:
            errors.append("%s: duplicate display value %r" % (loc, disp))
        else:
            displays.add(disp)

        for field in content:
            v = e.get(field)
            if not isinstance(v, str) or not v.strip():
                errors.append("%s: '%s' must be a non-empty string" % (loc, field))

        src = e.get("source")
        if not isinstance(src, dict):
            errors.append("%s: 'source' must be an object" % loc)
        else:
            for field in SOURCE_REQUIRED:
                if field not in src:
                    errors.append("%s: source missing field %r" % (loc, field))

        if isinstance(src, dict) and "note" in src and "section" in src:
            note = src["note"]
            section = src["section"]
            if not isinstance(note, str) or not isinstance(section, str):
                errors.append("%s: source note/section must be strings" % loc)
                continue
            # drill-table origins are fixed per kind
            origin = {"ports": "net", "crypto": "sec", "acronyms": "comptia"}[kind]
            rel_note = resolve_note(note, origin)
            rel_section = resolve_section(section, origin)
            lines = get_note_lines(rel_note)
            if lines is None:
                errors.append("%s: cited note file not found: %r" % (loc, rel_note))
            else:
                body = section_body(lines, rel_section)
                if body is None:
                    errors.append("%s: section heading %r not found in %r"
                                  % (loc, rel_section, rel_note))
                else:
                    verify = e.get("verify")
                    if not isinstance(verify, list):
                        errors.append("%s: 'verify' must be a list of strings" % loc)
                    else:
                        for token in verify:
                            verify_checks += 1
                            if not isinstance(token, str) or not grounded(body, token):
                                errors.append(
                                    "%s: expected value %r not found in %r > %r"
                                    % (loc, token, rel_note, rel_section))
                        # ANSWER-BEARING GROUNDING: each answer field must be
                        # covered by the entry's own verify list.
                        joined = " ".join(str(t) for t in verify)
                        for f in answer_fields:
                            v = e.get(f)
                            if isinstance(v, str) and v.strip() and not grounded(joined, v):
                                errors.append(
                                    "%s: answer field %r (%r) not covered by "
                                    "verify %r" % (loc, f, v, verify))

    print("total : %d %s entries" % (n, kind))
    print("  %s facts verified against vault : %d" % (kind, verify_checks))

    if errors:
        print("-" * 66)
        print("FAIL — %d problem(s) found:" % len(errors))
        for e in errors:
            print("  * %s" % e)
        print("=" * 66)
        return 1
    print("-" * 66)
    print("PASS — all %d %s entries valid, citations resolve, all facts "
          "grounded." % (n, kind))
    print("=" * 66)
    return 0


# ---------------------------------------------------------------- entrypoint

def main(argv=None):
    ap = argparse.ArgumentParser(description="Verify data/bundle.json integrity.")
    ap.add_argument("--bundle", default=DEFAULT_BUNDLE)
    ap.add_argument("--vault", default=None)
    args = ap.parse_args(argv)

    vault = args.vault or os.environ.get("TERMINUS_VAULT") or DEFAULT_VAULT
    if not os.path.isdir(vault):
        print("vault not found: %s" % vault)
        print("pass --vault /path/to/Obsidian Vault  (or set TERMINUS_VAULT)")
        return 1
    args.vault = vault

    if not os.path.exists(args.bundle):
        print("FAIL: bundle file not found: %s" % args.bundle)
        return 1
    try:
        with open(args.bundle, encoding="utf-8") as f:
            bundle = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print("FAIL: bundle does not parse as JSON: %s" % e)
        return 1

    if not isinstance(bundle, dict):
        print("FAIL: bundle must be a JSON object")
        return 1
    questions = bundle.get("questions")
    if not isinstance(questions, list) or not questions:
        print("FAIL: bundle must contain a non-empty questions list")
        return 1
    drill_tables = bundle.get("drill_tables")
    if not isinstance(drill_tables, dict):
        print("FAIL: bundle must contain a drill_tables object")
        return 1

    questions_ok = (verify_questions(bundle, vault) == 0)
    print()

    drill_ok = True
    for kind in ("ports", "crypto", "acronyms"):
        table = drill_tables.get(kind)
        if not isinstance(table, list) or not table:
            print("FAIL: drill table %r is missing or empty" % kind)
            drill_ok = False
            print()
            continue
        drill_ok = (verify_drill_table(table, kind, vault) == 0) and drill_ok
        print()

    ok = questions_ok and drill_ok
    print("OVERALL: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
