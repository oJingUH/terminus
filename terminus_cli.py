#!/usr/bin/env python3
"""
terminus_cli.py — TERMINUS terminal edition.

Pure Python 3 standard library. No dependencies, no TUI framework.

The app's heart is data/bundle.json, built by build_bundle.py from the three
committed source banks. All game logic lives in terminus.py so this CLI and
terminus_gui.py share one code path and cannot drift.

Modes:
    1) Daily Uplink    today's 10 questions, identical for everyone + share card
    2) Sector Sweep    drill one cert's questions (per-domain accuracy tracked)
    3) Cipher Grind    acronym <-> expansion drill, filterable by cert scope
    4) Port Scanner    port <-> service drill (Network+)
    5) Crypto Vault    algorithm <-> value drill (Security+)
    6) Recover Log     replay every missed question across all games
    7) Full Breach     timed exam over one cert's bank
    0) Player File     lifetime + per-domain + drill accuracy, achievements, streak
    q) Quit

Multiple-choice answers take a-d or 1-4; the drills take a free-text answer.
Enter `q` at any prompt to return to the menu. Progress persists to stats.json.

Flags:
    --selftest               scripted Daily Uplink round, fixed seed, no stdin
    --selftest --sector GAME scripted Sector Sweep for GAME (a+1|a+2|net|sec)
    --selftest --cipher      scripted Cipher Grind round (scope=all), no stdin
    --selftest --ports       scripted Port Scanner round, no stdin
    --selftest --crypto      scripted Crypto Vault round, no stdin
    --selftest --review      scripted Recover Log round, no stdin
    --selftest --breach GAME scripted Full Breach for GAME, no stdin
    --seed N                 deterministic shuffling
    --bundle PATH            explicit bundle path
    --stats PATH             explicit stats path

Colour is used when stdout is a tty and NO_COLOR is unset.
"""

import argparse
import datetime
import json
import os
import random
import sys
import tempfile
import textwrap
import time

import terminus as tb

WIDTH = 78


class Palette:
    """ANSI colour wrapper that degrades to plain text when disabled."""

    def __init__(self, enabled):
        self.enabled = enabled

    def _wrap(self, code, s):
        return "\033[%sm%s\033[0m" % (code, s) if self.enabled else s

    def green(self, s): return self._wrap("32", s)
    def red(self, s): return self._wrap("31", s)
    def yellow(self, s): return self._wrap("33", s)
    def cyan(self, s): return self._wrap("36", s)
    def bold(self, s): return self._wrap("1", s)
    def dim(self, s): return self._wrap("2", s)


def color_enabled():
    if os.environ.get("NO_COLOR") is not None:
        return False
    return sys.stdout.isatty()


# ---------------------------------------------------------------- output

def hr(pal, ch="-"):
    print(pal.dim(ch * WIDTH))


def paragraph(text, indent=""):
    for line in textwrap.wrap(str(text), WIDTH - len(indent)) or [""]:
        print(indent + line)


def print_option(key, text):
    prefix = "%s) " % key
    avail = WIDTH - len(prefix)
    lines = textwrap.wrap(str(text), avail) or [""]
    print(prefix + lines[0])
    for extra in lines[1:]:
        print("   " + extra)


def print_source(src, pal):
    label = "  source: "
    note = src["note"]
    section = src["section"]
    indent = " " * len(label)
    full = "%s \u203a %s" % (note, section)
    if len(label) + len(full) <= WIDTH:
        print(pal.dim(label + full))
    else:
        print(pal.dim("%s%s \u203a" % (label, note)))
        for line in textwrap.wrap(section, WIDTH - len(indent)) or [""]:
            print(pal.dim(indent + line))


def show_banner(pal):
    print()
    print(pal.cyan("  TERMINUS"))
    print(pal.dim("  " + "-" * 26))
    print("  A+ 220-1101 / 220-1102 \u00b7 Network+ N10-009 \u00b7 Security+ SY0-701")
    print("  one bundle, four games, three drills")
    print()


def show_menu(pal):
    print("  1) Daily Uplink   today's 10, same for everyone, shareable")
    print("  2) Sector Sweep   drill one cert's questions by domain")
    print("  3) Cipher Grind   acronym <-> expansion drill")
    print("  4) Port Scanner   port <-> service drill")
    print("  5) Crypto Vault   algorithm <-> value drill")
    print("  6) Recover Log    replay missed questions across all games")
    print("  7) Full Breach    timed exam over one cert")
    print("  0) Player File    stats, achievements, streak")
    print("  q) Quit")
    print()


# ---------------------------------------------------------------- mc round

def present_question(i, total, q, shown, pal, tag=None):
    print()
    hr(pal)
    if tag is None:
        tag = "Q%d/%d  [%s]  %s" % (i, total, tb.GAME_TAGS[q["game"]], q["difficulty"])
    print(pal.dim(tag))
    print()
    paragraph(q["question"])
    print()
    for key, text in shown:
        print_option(key, text)
    print()


def present_feedback(q, shown, correct_key, chosen_correct, pal):
    correct_text = next(t for k, t in shown if k == correct_key)
    if chosen_correct:
        print(pal.green("  ACCESS GRANTED"))
    else:
        prefix = "  TRACE DETECTED  ->  "
        avail = WIDTH - len(prefix)
        lines = textwrap.wrap(correct_text, avail) or [""]
        print(pal.red(prefix + lines[0]))
        for extra in lines[1:]:
            print(pal.red(" " * len(prefix) + extra))
    paragraph(q["explanation"], indent="  ")
    print_source(q["source"], pal)


def run_mc_round(questions, pal, rng, answer_fn, stats=None, stats_path=None,
                 record=True):
    """Run one multiple-choice round. Returns [(q, correct)] or None on quit."""
    order = list(questions)
    rng.shuffle(order)
    results = []
    for i, q in enumerate(order, 1):
        keys, shown, correct_key = tb.make_choices(q, rng)
        present_question(i, len(order), q, shown, pal)
        while True:
            raw = answer_fn(i, "> ", correct_key, keys)
            key = tb.parse_key(raw, keys)
            if key == "quit":
                print(pal.yellow("  Quit to menu."))
                _summarize(results, pal)
                return None
            if key is not None:
                break
            print(pal.yellow("  Enter a-d or 1-4 (or q to quit)."))
        ok = (key == correct_key)
        results.append((q, ok))
        if record and stats is not None:
            tb.record_mc(stats, q, ok)
            tb.save_stats(stats, stats_path)
        present_feedback(q, shown, correct_key, ok, pal)
    _summarize(results, pal)
    return results


def _summarize(results, pal):
    total = len(results)
    if total == 0:
        return
    score = sum(1 for _, ok in results if ok)
    pct = 100.0 * score / total
    print()
    hr(pal, "=")
    print(pal.bold("  Round summary"))
    print("  Score: %d/%d  (%.0f%%)" % (score, total, pct))
    missed = [q for q, ok in results if not ok]
    if missed:
        print("  Missed:")
        for q in missed:
            for n, line in enumerate(textwrap.wrap(q["question"], WIDTH - 6) or [""]):
                print(("    - " if n == 0 else "      ") + line)
    else:
        print(pal.green("  Clean round - nothing missed."))
    hr(pal, "=")
    print()


# ---------------------------------------------------------------- drill round

def present_drill_question(kind, item, i, total, pal):
    print()
    hr(pal)
    print(pal.dim("%s%d/%d  [%s]  %s"
                  % (kind[0].upper(), i, total, tb.KIND_TITLES[kind],
                     tb.direction_label(item["direction"]))))
    print()
    paragraph(item["prompt"])
    print()


def present_drill_feedback(item, correct_answer, chosen_correct, pal):
    if chosen_correct:
        print(pal.green("  ACCESS GRANTED"))
    else:
        prefix = "  TRACE DETECTED  ->  "
        avail = WIDTH - len(prefix)
        lines = textwrap.wrap(correct_answer, avail) or [""]
        print(pal.red(prefix + lines[0]))
        for extra in lines[1:]:
            print(pal.red(" " * len(prefix) + extra))
    paragraph(item["explanation"], indent="  ")
    print_source(item["source"], pal)


def run_drill_round(kind, items, pal, rng, answer_fn, stats=None, stats_path=None,
                    record=True):
    """Run one free-text drill round. Records per-kind stats when `stats` is set."""
    order = list(items)
    rng.shuffle(order)
    results = []
    for i, item in enumerate(order, 1):
        present_drill_question(kind, item, i, len(order), pal)
        while True:
            raw = answer_fn(i, "> ", item)
            raw = "" if raw is None else raw.strip()
            if raw.lower() in ("q", "quit"):
                print(pal.yellow("  Quit to menu."))
                _summarize_drill(results, pal)
                return None
            if raw:
                break
            print(pal.yellow("  Type your answer (or q to quit)."))
        correct, correct_answer, _, _ = tb.grade_drill(item, raw)
        results.append((item, correct, correct_answer))
        if record and stats is not None:
            tb.record_drill(stats, kind, item["id"], correct)
            tb.save_stats(stats, stats_path)
        present_drill_feedback(item, correct_answer, correct, pal)
    _summarize_drill(results, pal)
    return results


def _summarize_drill(results, pal):
    total = len(results)
    if total == 0:
        return
    score = sum(1 for _, ok, _ in results if ok)
    print()
    hr(pal, "=")
    print(pal.bold("  Round summary"))
    print("  Score: %d/%d  (%.0f%%)" % (score, total, 100.0 * score / total))
    missed = [(it, ca) for it, ok, ca in results if not ok]
    if missed:
        print("  Missed:")
        for it, ca in missed:
            print("    - %s  ->  %s" % (it["prompt"], ca))
    else:
        print(pal.green("  Clean round - nothing missed."))
    hr(pal, "=")
    print()


# ---------------------------------------------------------------- full breach

BREACH_QUESTIONS = 50
BREACH_SECONDS = 20 * 60


def run_breach(questions, pal, rng, answer_fn, stats=None, stats_path=None,
               clock=time.monotonic, seconds=BREACH_SECONDS):
    """Full Breach: a timed exam over one cert. No feedback until the end;
    unanswered questions count as wrong."""
    order = list(questions)
    rng.shuffle(order)
    print()
    print(pal.bold("  Full Breach: %d questions, %d minutes."
                   % (len(order), seconds // 60)))
    print("  No feedback until the end. Enter q to end the breach early.")

    deadline = clock() + seconds
    results = []
    for i, q in enumerate(order, 1):
        left = deadline - clock()
        if left <= 0:
            print(pal.yellow("  Time is up."))
            break
        keys, shown, correct_key = tb.make_choices(q, rng)
        present_question(i, len(order), q, shown, pal,
                         tag="Q%d/%d  %d:%02d left"
                         % ((i, len(order)) + divmod(int(left), 60)))
        while True:
            key = tb.parse_key(answer_fn(i, "> ", correct_key, keys), keys)
            if key is not None:
                break
            print(pal.yellow("  Enter a-d or 1-4 (or q to end the breach)."))
        if key == "quit":
            print(pal.yellow("  Breach ended early."))
            break
        if clock() > deadline:
            print(pal.yellow("  Time is up. That answer came too late to count."))
            break
        ok = (key == correct_key)
        results.append((q, ok))
        if stats is not None:
            tb.record_mc(stats, q, ok)
            tb.save_stats(stats, stats_path)

    answered = len(results)
    results += [(q, False) for q in order[answered:]]
    _summarize_breach(results, answered, pal)
    return results


def _summarize_breach(results, answered, pal):
    total = len(results)
    score = sum(1 for _, ok in results if ok)
    print()
    hr(pal, "=")
    print(pal.bold("  Breach results"))
    print("  Score: %d/%d  (%.0f%%)   answered %d/%d"
          % (score, total, 100.0 * score / total if total else 0.0, answered, total))
    missed = [q for q, ok in results if not ok]
    if missed:
        print("  Missed or unanswered (correct answer below each):")
        for q in missed:
            for n, line in enumerate(textwrap.wrap(q["question"], WIDTH - 6) or [""]):
                print(("    - " if n == 0 else "      ") + line)
            for line in textwrap.wrap("-> " + q["answer_text"], WIDTH - 6):
                print(pal.green("      " + line))
    hr(pal, "=")
    print()


# ---------------------------------------------------------------- player file

def show_player_file(stats, pal):
    lt = stats["lifetime"]
    tot, corr = lt["total"], lt["correct"]
    acc = (100.0 * corr / tot) if tot else 0.0
    print()
    hr(pal, "=")
    print(pal.bold("  Player File"))
    print("  Lifetime accuracy   : %d/%d  (%.1f%%)" % (corr, tot, acc))
    print("  Current streak      : %d" % stats["current_streak"])
    print("  Best streak         : %d" % stats["best_streak"])
    print("  Missed questions    : %d" % len(stats["missed_ids"]))
    print("  Per-domain accuracy :")
    for d in tb.DOMAIN_KEYS:
        b = stats["per_domain"][d]
        print("    %-8s  %d/%d" % (tb.DOMAIN_NAMES[d], b["correct"], b["total"]))
    for key, label in (("ports", "Port Scanner"), ("acronyms", "Cipher Grind"),
                       ("crypto", "Crypto Vault")):
        b = stats[key]
        print("  %-20s: %d/%d" % (label, b["correct"], b["total"]))
    print("  Achievements        : %d unlocked" % len(stats["achievements"]))
    for aid in stats["achievements"]:
        for a in tb.achievements.ACHIEVEMENTS if hasattr(tb, "achievements") else []:
            if a["id"] == aid:
                print("    %s %s" % (a["glyph"], a["name"]))
    hr(pal, "=")
    print()


# ---------------------------------------------------------------- interactive

def interactive_answer(i, prompt, correct_key, keys):
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        return "q"


def interactive_drill_answer(i, prompt, item):
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        return "q"


def pick_game(pal, default=None):
    print("  Game (sector):")
    for n, (label, key) in enumerate(tb.GAME_CHOICES, 1):
        print("    %d) %s" % (n, label))
    raw = input("  > ").strip().lower()
    if raw in ("q", "quit"):
        return "quit"
    if raw.isdigit():
        n = int(raw)
        if 1 <= n <= len(tb.GAME_CHOICES):
            return tb.GAME_CHOICES[n - 1][1]
    for label, key in tb.GAME_CHOICES:
        if raw == key or raw == label.split(" ")[0].lower().replace("+", ""):
            return key
    return None


def run_menu(bundle, stats, pal, rng, args):
    while True:
        show_banner(pal)
        show_menu(pal)
        try:
            choice = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = "quit"

        if choice in ("1", "daily"):
            day = datetime.date.today()
            drng = tb.daily_rng(day)
            qs = tb.select_daily(bundle["questions"], drng, 10)
            results = run_mc_round(qs, pal, drng, interactive_answer, stats, args.stats)
            if results is not None:
                _print_share(day, results, pal)
        elif choice in ("2", "sector"):
            game = pick_game(pal)
            if game in ("quit", None):
                continue
            qs = tb.filter_game(bundle, game)
            results = run_mc_round(tb.select_drill_items(qs, rng, 10) if False
                                   else rng.sample(qs, min(10, len(qs))),
                                   pal, rng, interactive_answer, stats, args.stats)
        elif choice in ("3", "cipher"):
            _cipher_menu_entry(bundle, pal, rng, stats, args)
        elif choice in ("4", "ports"):
            items = tb.build_drill_items(bundle["drill_tables"]["ports"], "ports")
            run_drill_round("ports", items, pal, rng, interactive_drill_answer,
                            stats, args.stats)
        elif choice in ("5", "crypto"):
            items = tb.build_drill_items(bundle["drill_tables"]["crypto"], "crypto")
            run_drill_round("crypto", items, pal, rng, interactive_drill_answer,
                            stats, args.stats)
        elif choice in ("6", "review"):
            _recover_log_entry(bundle, pal, rng, stats, args)
        elif choice in ("7", "breach"):
            game = pick_game(pal)
            if game in ("quit", None):
                continue
            qs = tb.filter_game(bundle, game)
            run_breach(rng.sample(qs, min(BREACH_QUESTIONS, len(qs))),
                       pal, rng, interactive_answer, stats, args.stats)
        elif choice in ("0", "stats", "player"):
            show_player_file(stats, pal)
        elif choice in ("q", "quit", "exit"):
            print("  Good luck on the exam.")
            break
        else:
            print("  Unknown choice. Enter 0-7 or q.")


def _cipher_menu_entry(bundle, pal, rng, stats, args):
    game = pick_game(pal) if False else None  # scope=all by default (menu keeps it simple)
    table = bundle["drill_tables"]["acronyms"]
    entries = tb.filter_scope(table, None)
    items = tb.build_drill_items(entries, "acronyms")
    items = tb.select_drill_items(items, rng, 10)
    run_drill_round("acronyms", items, pal, rng, interactive_drill_answer,
                    stats, args.stats)


def _recover_log_entry(bundle, pal, rng, stats, args):
    qs = tb.select_review(bundle, stats)
    if not qs:
        print("  Nothing to recover - you have no missed questions yet.")
        print()
        return
    run_mc_round(qs, pal, rng, interactive_answer, stats, args.stats)


def _print_share(day, results, pal):
    score = sum(1 for _, ok in results if ok)
    grid = "".join("\U0001F7E9" if ok else "\U0001F7E5" for _, ok in results)
    print(pal.bold("  Share your result (copy the lines below):"))
    print()
    print("TERMINUS Daily Uplink \u00b7 %s \u00b7 %d/%d" % (day.isoformat(), score, len(results)))
    print(grid)
    print()


# ---------------------------------------------------------------- selftests

def _selftest_stats(args, needs_write=False):
    if args.stats is not None:
        return tb.load_stats(args.stats), args.stats
    if needs_write:
        fd, path = tempfile.mkstemp(prefix="terminus-selftest-", suffix=".json")
        os.close(fd)
        return tb.empty_stats(), path
    return None, None


def _scripted_mc(i, prompt, correct_key, keys):
    chosen = correct_key if i % 2 == 1 else next(k for k in keys if k != correct_key)
    print(prompt + chosen, flush=True)
    return chosen


def _scripted_drill(i, prompt, item):
    chosen = item["answer"] if i % 2 == 1 else "zzz-not-a-real-answer"
    print(prompt + chosen, flush=True)
    return chosen


def run_daily_selftest(bundle, pal):
    day = datetime.date(2026, 1, 1)
    first = [q["id"] for q in tb.select_daily(bundle["questions"], tb.daily_rng(day), 10)]
    again = [q["id"] for q in tb.select_daily(bundle["questions"], tb.daily_rng(day), 10)]
    other = [q["id"] for q in tb.select_daily(
        bundle["questions"], tb.daily_rng(day + datetime.timedelta(days=1)), 10)]
    if first != again or first == other:
        print(pal.red("  DAILY SELF-TEST FAIL: daily draw is not stable per date"))
        return 1

    show_banner(pal)
    print(pal.cyan("  DAILY UPLINK SELF-TEST  (scripted round, fixed seed, no stdin)"))
    qs = tb.select_daily(bundle["questions"], tb.daily_rng(day), 10)
    run_mc_round(qs, pal, tb.daily_rng(day), _scripted_mc)
    print(pal.green("DAILY UPLINK SELF-TEST COMPLETE"))
    return 0


def run_sector_selftest(bundle, pal, rng, game):
    show_banner(pal)
    print(pal.cyan("  SECTOR SWEEP SELF-TEST  (game=%s, no stdin)" % game))
    qs = tb.filter_game(bundle, game)
    if not qs:
        print(pal.red("  SECTOR SELF-TEST FAIL: no questions for game %r" % game))
        return 1
    order = list(qs)
    rng.shuffle(order)
    sampled = order[:10]
    bad = [q["id"] for q in sampled if q["game"] != game]
    if bad:
        print(pal.red("  SECTOR SELF-TEST FAIL: game leak %r" % bad))
        return 1
    run_mc_round(sampled, pal, rng, _scripted_mc)
    print(pal.green("SECTOR SWEEP SELF-TEST COMPLETE"))
    return 0


def run_drill_selftest(bundle, pal, rng, kind, scope=None):
    show_banner(pal)
    print(pal.cyan("  %s SELF-TEST  (scripted round, no stdin)"
                   % tb.KIND_TITLES[kind]))
    table = bundle["drill_tables"][kind]
    if kind == "acronyms":
        table = tb.filter_scope(table, scope)
    items = tb.build_drill_items(table, kind)
    items = tb.select_drill_items(items, rng, 10)
    if not items:
        print(pal.red("  %s SELF-TEST FAIL: no drill items" % tb.KIND_TITLES[kind]))
        return 1
    run_drill_round(kind, items, pal, rng, _scripted_drill)
    print(pal.green("%s SELF-TEST COMPLETE" % tb.KIND_TITLES[kind]))
    return 0


def run_review_selftest(bundle, pal, rng, stats, stats_path):
    show_banner(pal)
    print(pal.cyan("  RECOVER LOG SELF-TEST  (scripted round, no stdin)"))
    if not stats["missed_ids"]:
        stats["missed_ids"] = [q["id"] for q in bundle["questions"][:3]]
    # also seed one missed drill id per kind so the "all games" review covers them
    for kind in ("ports", "acronyms", "crypto"):
        table = bundle["drill_tables"][kind]
        if not stats[kind]["missed_ids"] and table:
            stats[kind]["missed_ids"] = [table[0]["id"]]
    tb.save_stats(stats, stats_path)

    qs = tb.select_review(bundle, stats)
    if not qs:
        print(pal.red("  REVIEW SELF-TEST FAIL: no review questions selected"))
        return 1

    def scripted(i, prompt, correct_key, keys):
        chosen = correct_key
        print(prompt + chosen, flush=True)
        return chosen

    run_mc_round(qs, pal, rng, scripted, stats, stats_path)

    remaining = set(stats["missed_ids"]) & {q["id"] for q in qs}
    if remaining:
        print(pal.red("  REVIEW SELF-TEST FAIL: still missed: %s"
                      % ", ".join(sorted(remaining))))
        return 1
    print(pal.green("RECOVER LOG SELF-TEST COMPLETE"))
    return 0


def run_breach_selftest(bundle, pal, rng, game):
    show_banner(pal)
    print(pal.cyan("  FULL BREACH SELF-TEST  (game=%s, no stdin)" % game))
    qs = tb.filter_game(bundle, game)
    if not qs:
        print(pal.red("  BREACH SELF-TEST FAIL: no questions for game %r" % game))
        return 1
    order = list(qs)
    rng.shuffle(order)
    sampled = order[:10]

    def scripted(i, prompt, correct_key, keys):
        chosen = correct_key if i % 3 else next(k for k in keys if k != correct_key)
        print(prompt + chosen, flush=True)
        return chosen

    run_breach(sampled, pal, rng, scripted)
    print(pal.green("FULL BREACH SELF-TEST COMPLETE"))
    return 0


# ---------------------------------------------------------------- entrypoint

def main(argv=None):
    ap = argparse.ArgumentParser(description="TERMINUS terminal quiz game")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--sector", default=None, metavar="GAME",
                    help="with --selftest: scripted Sector Sweep for a+1|a+2|net|sec")
    ap.add_argument("--cipher", action="store_true",
                    help="with --selftest: scripted Cipher Grind round")
    ap.add_argument("--ports", action="store_true",
                    help="with --selftest: scripted Port Scanner round")
    ap.add_argument("--crypto", action="store_true",
                    help="with --selftest: scripted Crypto Vault round")
    ap.add_argument("--review", action="store_true",
                    help="with --selftest: scripted Recover Log round")
    ap.add_argument("--breach", default=None, metavar="GAME",
                    help="with --selftest: scripted Full Breach for a+1|a+2|net|sec")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--bundle", default=tb.DEFAULT_BUNDLE)
    ap.add_argument("--stats", default=None)
    args = ap.parse_args(argv)

    pal = Palette(color_enabled())
    bundle = tb.load_bundle(args.bundle)
    if tb.bundle_stale(bundle, args.bundle):
        print(pal.yellow("  note: bundle is older than a source bank - "
                         "consider re-running build_bundle.py"), file=sys.stderr)

    if args.stats:
        stats_path = args.stats
    elif getattr(sys, "frozen", False):
        # Frozen onefile: the script dir is a temp extraction dir, so persist
        # stats to the per-user data dir instead.
        stats_path = os.path.join(tb.default_data_dir(), "stats.json")
    else:
        stats_path = os.path.join(
            os.path.dirname(os.path.abspath(args.bundle)), "..", "stats.json")

    if args.selftest:
        rng = random.Random(args.seed if args.seed is not None else 42)
        if args.sector is not None:
            if args.sector not in tb.GAMES:
                print("terminus_cli.py: unknown game %r (expected %s)"
                      % (args.sector, "|".join(tb.GAMES)))
                return 2
            return run_sector_selftest(bundle, pal, rng, args.sector)
        if args.cipher:
            return run_drill_selftest(bundle, pal, rng, "acronyms")
        if args.ports:
            return run_drill_selftest(bundle, pal, rng, "ports")
        if args.crypto:
            return run_drill_selftest(bundle, pal, rng, "crypto")
        if args.review:
            stats, sp = _selftest_stats(args, needs_write=True)
            return run_review_selftest(bundle, pal, rng, stats, sp)
        if args.breach is not None:
            if args.breach not in tb.GAMES:
                print("terminus_cli.py: unknown game %r (expected %s)"
                      % (args.breach, "|".join(tb.GAMES)))
                return 2
            return run_breach_selftest(bundle, pal, rng, args.breach)
        return run_daily_selftest(bundle, pal)

    rng = random.Random(args.seed)
    stats = tb.load_stats(stats_path)
    args.stats = stats_path
    run_menu(bundle, stats, pal, rng, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
