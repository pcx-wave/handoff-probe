#!/usr/bin/env python3
"""
handoff_validity.py — executable validity guards for the probe.

These encode the methodology invariants that have *no local error signal*
(a run can execute cleanly, exit 0, and still measure the wrong thing). Each
guard turns a "is this measurement valid?" question into a check with an oracle,
so regressions surface as a failed test rather than a plausible-looking number.

Guards
------
G1  Invocation symmetry      — every delegation CLI goes through its production
                               wrapper (*_DELEGATE), never a bare CLI call. Catches
                               the asymmetric-opencode bug (probe ran `opencode run`
                               directly, missing --dir, so it never read the files).
G2  Zero-score classification — for every C1-C5 run scored 0, say *why*:
                               HARNESS_BLIND_SUSPECT (target code present but 0 —
                               the C3-signature bug), SEED_ONLY (model wrote nothing
                               beyond the seed — the C1/C2 artifact), TIMEOUT,
                               NO_TARGET (genuine: nothing produced), OK.
G3  Uniform-zero detector    — a (signal, level) that is 0.00 across ALL runs for
                               one model while another model/channel scores >0 is
                               flagged as a likely setup/invocation artifact, not
                               capability. Catches opencode C3=0-everywhere.

CLI
---
    python3 handoff_validity.py <run_dir> [<run_dir> ...]
    python3 handoff_validity.py --symmetry-only

Exit code 1 if any FAIL-level finding (symmetry violation or HARNESS_BLIND_SUSPECT).
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
LVL = {0: 'C1', 1: 'C2', 2: 'C3', 3: 'C4', 4: 'C5'}

# A construct that, if present in the produced code, means the model DID write the
# target for that level — so a score of 0 is suspicious (harness could not see it).
TARGET_RE = {
    'C1': re.compile(r'print\s*\(.*hello', re.I | re.S),
    'C2': re.compile(r'def\s+\w*revers|\[\s*::\s*-\s*1\s*\]', re.I),
    'C3': re.compile(r'def\s+\w*(valid|check)\w*\s*\(|/users/validate', re.I),
    'C4': re.compile(r'async\s+def', re.I),
    'C5': re.compile(r'class\s+\w*cache|lru_cache|\bttl\b', re.I),
}
# Seed signatures: presence of these (and ONLY these) means the model added nothing.
SEED_MARKERS = ('def get_users', 'def get_user', 'def format_response',
                'class User(db.Model)', 'class Order(db.Model)')


# ---------------------------------------------------------------------------
# G1 — invocation symmetry
# ---------------------------------------------------------------------------
def check_invocation_symmetry(probe_source: str) -> list[str]:
    """Return a list of violations. A delegation branch must use its *_DELEGATE
    wrapper and must NOT invoke the CLI binary directly via subprocess."""
    violations = []
    # Bare CLI invocations inside the probe = bypassing the production wrapper.
    if re.search(r"\[\s*['\"]opencode['\"]\s*,\s*['\"]run['\"]", probe_source):
        violations.append("opencode invoked directly (`['opencode','run',...]`) "
                          "instead of via OPENCODE_DELEGATE wrapper")
    if re.search(r"subprocess\.\w+\(\s*\[\s*['\"]vibe['\"]", probe_source):
        violations.append("vibe invoked directly instead of via VIBE_DELEGATE wrapper")
    # Both wrappers must be referenced.
    for cli, const in (('opencode', 'OPENCODE_DELEGATE'), ('vibe', 'VIBE_DELEGATE')):
        if f"cli == '{cli}'" in probe_source and const not in probe_source:
            violations.append(f"{cli} branch present but {const} wrapper never referenced")
    return violations


# ---------------------------------------------------------------------------
# G2 — zero-score classification
# ---------------------------------------------------------------------------
def _best_source(entry: dict) -> str:
    out = entry.get('output', '') or ''
    wd = entry.get('workdir_snippet', '') or ''
    if '=== VIBE START ===' in out:
        return out if '[vibe]' in out else wd
    return out if out.strip() else wd


def classify_zero(entry: dict, rescore=True) -> str:
    """Classify a single run. Returns OK for score>0; otherwise the reason it is 0.

    The HARNESS_BLIND check uses the harness itself as the oracle: a stored 0 is
    only flagged as harness-blindness if re-scoring the *stored code* with the
    current harness recovers a non-zero score. Both the primary source (output or
    workdir, per _best_source) and the alternate source are tried — models that
    write code to files AND emit a brief text response would otherwise be missed.
    """
    if entry.get('functional_score', 0) and entry['functional_score'] > 0:
        return 'OK'
    if entry.get('exit_code') == 124:
        return 'TIMEOUT'
    level = LVL.get(entry.get('level'))
    if level is None:
        return 'OK'  # non-sweep/contract level
    code = _best_source(entry)
    alt = entry.get('workdir_snippet', '') or '' if code != (entry.get('workdir_snippet') or '') else ''
    if not code.strip():
        return 'NO_OUTPUT'
    # Oracle: try primary source first; if it fails, also try the alternate source.
    if rescore:
        try:
            from handoff_functests import run_functional_tests
            if run_functional_tests(code, level)['functional_score'] > 0:
                return 'HARNESS_BLIND_SUSPECT'   # working code that the run scored 0 -> FAIL
            if alt.strip() and run_functional_tests(alt, level)['functional_score'] > 0:
                return 'HARNESS_BLIND_SUSPECT'   # code was in the other source
        except Exception:
            pass
    has_seed = any(m in code for m in SEED_MARKERS)
    n_defs = code.count('def ')
    if has_seed and n_defs <= 3 and not TARGET_RE[level].search(code):
        return 'SEED_ONLY'                       # only the untouched seed -> measurement artifact
    if TARGET_RE[level].search(code):
        return 'CODE_NO_PASS'                    # target-shaped code present but it does not work
    return 'NO_TARGET'                            # genuine: produced something, but not the target


def audit_run_dir(run_dir: str) -> dict:
    path = run_dir if os.path.isabs(run_dir) else os.path.join(
        os.path.expanduser('~/.handoff/runs'), run_dir)
    jsonl = os.path.join(path, 'raw.jsonl')
    rows = [json.loads(l) for l in open(jsonl)] if os.path.exists(jsonl) else []
    cli = rows[0].get('cli') if rows else '?'
    model = rows[0].get('model') if rows else '?'
    counts, suspects = {}, []
    per_sig_level = {}  # (signal, level) -> [scores]
    for r in rows:
        if r.get('signal') not in ('sweep', 'contract'):
            continue
        cat = classify_zero(r)
        counts[cat] = counts.get(cat, 0) + 1
        per_sig_level.setdefault((r['signal'], r.get('level')), []).append(
            r.get('functional_score', 0))
        if cat == 'HARNESS_BLIND_SUSPECT':
            suspects.append((r['signal'], LVL.get(r.get('level')),
                             _best_source(r)[:80].replace('\n', ' ')))
    # uniform-zero per (signal, level)
    uniform_zero = [f"{s}/{LVL.get(l)}" for (s, l), sc in per_sig_level.items()
                    if sc and all(x == 0 for x in sc)]
    return {'cli': cli, 'model': model, 'counts': counts,
            'suspects': suspects, 'uniform_zero': uniform_zero}


# ---------------------------------------------------------------------------
# G3 + report
# ---------------------------------------------------------------------------
def main():
    args = sys.argv[1:]
    fail = False

    # G1 always runs.
    probe_src = open(os.path.join(HERE, 'handoff_probe.py')).read()
    viol = check_invocation_symmetry(probe_src)
    print("=" * 72)
    print("G1  INVOCATION SYMMETRY")
    if viol:
        fail = True
        for v in viol:
            print(f"  FAIL: {v}")
    else:
        print("  PASS: both CLIs delegate through their production wrapper")

    if args == ['--symmetry-only']:
        sys.exit(1 if fail else 0)

    print("\nG2/G3  PER-RUN CLASSIFICATION")
    audits = [audit_run_dir(d) for d in args]
    # cross-run uniform-zero comparison: a (sig,level) zero for one model but not all
    seen = {}
    for a in audits:
        for sl in a['uniform_zero']:
            seen.setdefault(sl, []).append(a['model'])
    for a in audits:
        print(f"\n  {a['cli']}/{a['model']}")
        print(f"    counts: {a['counts']}")
        if a['suspects']:
            fail = True
            print(f"    FAIL — HARNESS_BLIND_SUSPECT (target code present, scored 0):")
            for sig, lvl, snip in a['suspects'][:5]:
                print(f"      {sig}/{lvl}: {snip}")
        if a['uniform_zero']:
            print(f"    WARN — uniform-zero (all runs 0; verify not a setup artifact): "
                  f"{a['uniform_zero']}")

    print("\n" + "=" * 72)
    print("RESULT:", "FAIL" if fail else "PASS")
    sys.exit(1 if fail else 0)


if __name__ == '__main__':
    main()
