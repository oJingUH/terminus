#!/usr/bin/env python3
"""
verify_negative_controls.py — prove verify_bundle.py fails on mutations.

Each gate check must make verify_bundle.py exit non-zero on a COPY of
data/bundle.json carrying exactly one defect. This script builds a TEMPORARY
vault fixture (it never depends on the user's real Obsidian vault): for every
distinct resolved (note, section) in the bundle it writes a synthetic note
whose body contains each entry's verify strings, so the honest run must exit 0.
It then mutates one value at a time and requires the gate to exit non-zero for
each mutation.

Mutation shapes (one per required source shape):
    1. question-answer-index flip  -> answer mirror mismatch
    2. verify-token mutation       -> token no longer grounds in the note
    3. source-section mutation     -> section heading no longer exists
    4. drill-entry mutation        -> answer field no longer covered by verify

Usage:
    python3 verify_negative_controls.py [--bundle data/bundle.json]
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VB = os.path.join(HERE, "verify_bundle.py")
BUNDLE = os.path.join(HERE, "data", "bundle.json")

# Import the verifier's citation-resolution helpers so the fixture resolves
# notes exactly the way the gate does.
sys.path.insert(0, HERE)
from verify_bundle import (  # noqa: E402
    resolve_note, resolve_section, question_origin,
    DRILL_ANSWER,
)

PASS = 0
failures = []


def _tmp_copy(src):
    fd, dst = tempfile.mkstemp(suffix=".json", prefix="negctl-")
    os.close(fd)
    shutil.copy(src, dst)
    return dst


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save(data, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def build_fixture(bundle, vault_dir):
    """Write synthetic notes into `vault_dir` so every citation resolves and
    every verify token grounds at a whole-token boundary.

    One note file per distinct resolved `source.note`; each holds a heading per
    cited `source.section` and one body line per entry that joins the entry's
    verify strings, so grounding (token/cell boundary) passes for the honest
    run.
    """
    by_note = {}  # resolved_note -> { resolved_section -> [entry tokens list] }

    def add(note, section, verify, origin):
        rel_note = resolve_note(note, origin)
        rel_section = resolve_section(section, origin)
        by_note.setdefault(rel_note, {}).setdefault(rel_section, [])
        toks = [t for t in verify if isinstance(t, str)]
        by_note[rel_note][rel_section].append(toks)

    for q in bundle["questions"]:
        origin = question_origin(q)
        src = q.get("source") or {}
        if "note" in src and "section" in src:
            add(src["note"], src["section"], q.get("verify") or [], origin)

    drill_origins = {"ports": "net", "crypto": "sec", "acronyms": "comptia"}
    for kind, table in bundle["drill_tables"].items():
        for e in table:
            origin = drill_origins[kind]
            src = e.get("source") or {}
            if "note" in src and "section" in src:
                add(src["note"], src["section"], e.get("verify") or [], origin)

    for rel_note, sections in by_note.items():
        path = os.path.join(vault_dir, rel_note.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for section, lines in sections.items():
                f.write("# %s\n\n" % section)
                for toks in lines:
                    f.write(" ".join(toks) + "\n")
                f.write("\n")
    return vault_dir


def run_verify(vault, bundle=None):
    cmd = [sys.executable, VB, "--vault", vault]
    if bundle:
        cmd += ["--bundle", bundle]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, r.stdout


def check_pass(name, exit_code):
    ok = exit_code == PASS
    print("  [%s] %s -> exit=%d (must be 0)"
          % ("OK" if ok else "FAIL", name, exit_code))
    if not ok:
        failures.append(name)


def check_reject(name, exit_code):
    ok = exit_code != PASS
    print("  [%s] %s -> exit=%d (must be non-zero)"
          % ("OK" if ok else "FAIL", name, exit_code))
    if not ok:
        failures.append(name)


def _mutate(src, fn):
    """Apply `fn(bundle)` to a temp copy and return the copy's path."""
    path = _tmp_copy(src)
    data = _load(path)
    fn(data)
    _save(data, path)
    return path


def _find_mc(bundle):
    return next(q for q in bundle["questions"] if q["type"] == "mc")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Negative controls for verify_bundle.py.")
    ap.add_argument("--bundle", default=BUNDLE)
    args = ap.parse_args(argv)

    bundle = _load(args.bundle)

    fixture_tmp = tempfile.TemporaryDirectory(prefix="terminus-vault-")
    vault = build_fixture(bundle, fixture_tmp.name)

    print("TERMINUS — negative controls")
    print("=" * 66)
    print("vault : %s  (temporary fixture)" % vault)

    # --- HONEST RUN: no mutation, fixture in place, must exit 0 ---
    code, _ = run_verify(vault, bundle=args.bundle)
    check_pass("honest run (fixture in place, no mutation)", code)

    # --- 1. question-answer-index flip (in-range) ---
    def _m1(b):
        q = _find_mc(b)
        q["answer"] = (q["answer"] + 1) % len(q["options"])
    check_reject("1. question-answer-index flip (in-range)",
                 run_verify(vault, bundle=_mutate(args.bundle, _m1))[0])

    # --- 2. verify-token mutation ---
    def _m2(b):
        q = _find_mc(b)
        if q["verify"]:
            q["verify"][0] = "ZZZ_NOT_A_REAL_TOKEN_999"
        else:
            q["verify"] = ["ZZZ_NOT_A_REAL_TOKEN_999"]
    check_reject("2. verify-token mutation",
                 run_verify(vault, bundle=_mutate(args.bundle, _m2))[0])

    # --- 3. source-section mutation ---
    def _m3(b):
        q = _find_mc(b)
        q["source"]["section"] = "No Such Section 999"
    check_reject("3. source-section mutation",
                 run_verify(vault, bundle=_mutate(args.bundle, _m3))[0])

    # --- 4. drill-entry mutation (acronym expansion changed, verify intact) ---
    def _m4(b):
        e = b["drill_tables"]["acronyms"][0]
        e["expansion"] = "Totally Different Expansion 999"
    check_reject("4. drill-entry mutation (expansion changed, verify intact)",
                 run_verify(vault, bundle=_mutate(args.bundle, _m4))[0])

    print("=" * 66)
    if failures:
        print("NEGATIVE CONTROLS: %d FAILED (gate missed a defect): %s"
              % (len(failures), ", ".join(failures)))
        return 1
    print("NEGATIVE CONTROLS: all caught (honest run passed, every mutation "
          "rejected) — gate is strict.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
