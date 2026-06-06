#!/usr/bin/env python3
"""
handoff_runregistry.py — classify every run once, write ~/.handoff/run_registry.json.

Status values
-------------
VALID       — clean workdir, no contamination detected, harness bugs fixed
CONTAMINATED — "already done" responses detected (workdir not clean)
ARTIFACT    — all outputs are seed stubs or empty (setup failure)
UNKNOWN     — can't determine

Usage
-----
    python3 handoff_runregistry.py          # classify all runs, update registry
    python3 handoff_runregistry.py --list   # print current registry
"""
import json, os, re, sys
from datetime import datetime

RUNS_DIR    = os.path.expanduser('~/.handoff/runs')
REGISTRY    = os.path.expanduser('~/.handoff/run_registry.json')
SEED_MARKERS = ('def get_users','def get_user','def format_response',
                'class User(db.Model)','class Order(db.Model)')

# Runs before this date had harness bugs — always invalid regardless of other checks.
HARNESS_OK_FROM = '20260529'


def _already_done_ratio(rows: list[dict]) -> float:
    c3 = [r for r in rows if r.get('level') == 2
          and r.get('signal') in ('sweep','contract')]
    if not c3:
        return 0.0
    n = sum(1 for r in c3 if 'already' in (r.get('output','') or '').lower())
    return n / len(c3)


def _seed_only_ratio(rows: list[dict]) -> float:
    sc = [r for r in rows if r.get('signal') in ('sweep','contract')]
    if not sc:
        return 0.0
    def is_seed(r):
        src = (r.get('output','') or '') or (r.get('workdir_snippet','') or '')
        return any(m in src for m in SEED_MARKERS) and src.count('def ') <= 3
    return sum(1 for r in sc if is_seed(r)) / len(sc)


def classify_run(run_dir: str) -> dict:
    date_prefix = run_dir[:8]
    raw = os.path.join(RUNS_DIR, run_dir, 'raw.jsonl')
    meta_path = os.path.join(RUNS_DIR, run_dir, 'run_metadata.json')

    if not os.path.exists(raw):
        return {'status': 'UNKNOWN', 'reason': 'no raw.jsonl'}

    rows = []
    try:
        rows = [json.loads(l) for l in open(raw) if l.strip()]
    except Exception as e:
        return {'status': 'UNKNOWN', 'reason': str(e)}

    if not rows:
        return {'status': 'UNKNOWN', 'reason': 'empty'}

    cli   = rows[0].get('cli', '?')
    model = rows[0].get('model', '?')
    meta  = {}
    if os.path.exists(meta_path):
        try:
            meta = json.load(open(meta_path))
        except Exception:
            pass

    # Runs before harness fixes are always invalid
    if date_prefix < HARNESS_OK_FROM:
        return {'status': 'INVALID', 'reason': 'pre-fix harness (before 20260529)',
                'cli': cli, 'model': model}

    sc_rows = [r for r in rows if r.get('signal') in ('sweep','contract')]
    if not sc_rows:
        return {'status': 'UNKNOWN', 'reason': 'no sweep/contract rows',
                'cli': cli, 'model': model}

    seed_ratio = _seed_only_ratio(rows)
    if seed_ratio > 0.6:
        return {'status': 'ARTIFACT', 'reason': f'seed-only ratio {seed_ratio:.0%}',
                'cli': cli, 'model': model}

    already_ratio = _already_done_ratio(rows)
    if already_ratio > 0.5:
        return {'status': 'CONTAMINATED',
                'reason': f'"already done" on {already_ratio:.0%} of C3 — dirty workdir',
                'cli': cli, 'model': model}

    clean_workdir = meta.get('clean_workdir')  # True/False/None
    if clean_workdir is False:
        return {'status': 'SUSPECT',
                'reason': 'metadata says clean_workdir=False',
                'cli': cli, 'model': model}

    return {'status': 'VALID', 'reason': 'clean',
            'cli': cli, 'model': model,
            'clean_workdir': clean_workdir,
            'probe_version': meta.get('probe_version'),
            'git_sha': meta.get('git_sha')}


def build_registry() -> dict:
    registry = {}
    try:
        existing = json.load(open(REGISTRY)) if os.path.exists(REGISTRY) else {}
    except Exception:
        existing = {}

    for run_dir in sorted(os.listdir(RUNS_DIR)):
        result = classify_run(run_dir)
        result['classified_at'] = datetime.now().isoformat()
        registry[run_dir] = result

    return registry


def valid_runs(registry: dict) -> list[str]:
    return [rd for rd, info in sorted(registry.items()) if info['status'] == 'VALID']


def main():
    if '--list' in sys.argv:
        if not os.path.exists(REGISTRY):
            print("No registry yet. Run without --list to build it.")
            return
        reg = json.load(open(REGISTRY))
        for rd, info in sorted(reg.items()):
            status = info['status']
            model  = info.get('model', '?')
            cli    = info.get('cli', '?')
            reason = info.get('reason', '')
            marker = {'VALID':'✓','CONTAMINATED':'✗','ARTIFACT':'~','INVALID':'✗','SUSPECT':'?'}.get(status,'?')
            print(f"{marker} {rd:<45} {model}/{cli}  [{status}] {reason}")
        valid = valid_runs(reg)
        print(f"\n{len(valid)} valid runs out of {len(reg)} total.")
        return

    print("Classifying runs...")
    reg = build_registry()
    with open(REGISTRY, 'w') as f:
        json.dump(reg, f, indent=2)

    valid = valid_runs(reg)
    by_status = {}
    for info in reg.values():
        s = info['status']
        by_status[s] = by_status.get(s, 0) + 1

    print(f"Registry written to {REGISTRY}")
    print(f"  Total runs : {len(reg)}")
    for s, n in sorted(by_status.items()):
        print(f"  {s:<15}: {n}")
    print(f"\nValid runs ({len(valid)}):")
    for rd in valid:
        info = reg[rd]
        print(f"  {rd}  {info.get('model','?')}/{info.get('cli','?')}")


if __name__ == '__main__':
    main()
