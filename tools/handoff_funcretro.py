#!/usr/bin/env python3
"""
Retro-score existing raw.jsonl runs with functional tests (C1-C5).
Skips runs with truncated outputs (max_output_len <= 500).
Produces a per-model H_loss summary across all usable runs.
"""
import sys

import json
import os
import statistics
from collections import defaultdict

REGISTRY = os.path.expanduser('~/.handoff/run_registry.json')

from handoff_functests import run_functional_tests
from handoff_metrics import compute_h_loss, silent_failure_risk

RUNS_DIR = os.path.expanduser('~/.handoff/runs')
MIN_OUTPUT_LEN = 501  # below this → truncated, skip

_SEED_MARKERS = ('def get_users', 'def get_user', 'def format_response',
                 'class User(db.Model)', 'class Order(db.Model)')


def _is_seed_only(text: str) -> bool:
    return any(m in text for m in _SEED_MARKERS) and text.count('def ') <= 3


def _best_source(entry: dict) -> str:
    """Return the primary code source for functional testing.

    Priority logic:
    - Vibe scaffold, no [vibe] marker   → workdir_snippet (model wrote files without text response)
    - Vibe with [vibe] marker           → output (model responded inline; workdir may be seed-only)
    - Opencode + real workdir           → workdir_snippet (model used edit/write tools)
    - Opencode + seed-only workdir      → output ([opencode] text lines, model answered inline)
    - Opencode, no workdir              → output
    - Non-scaffold with output          → output
    - Empty output                      → workdir_snippet

    Callers should also try the alternate source as a fallback when this returns
    a source that scores 0, since models that write files AND emit a brief text
    response will have code only in workdir while [vibe] causes this to pick output.
    """
    out = entry.get('output', '')
    wd  = entry.get('workdir_snippet', '')
    if '=== VIBE START ===' in out:
        return out if '[vibe]' in out else wd
    if '=== OPENCODE START ===' in out:
        if wd.strip() and not _is_seed_only(wd):
            return wd
        return out
    if out.strip():
        return out
    return wd


def score_run_dir(run_dir: str) -> dict | None:
    """
    Score one run directory. Returns per-task functional scores or None if skipped.
    Structure: {task_id -> [score, score, ...]}
    """
    jsonl = os.path.join(RUNS_DIR, run_dir, 'raw.jsonl')
    if not os.path.exists(jsonl):
        return None

    entries = []
    with open(jsonl) as f:
        for line in f:
            try:
                e = json.loads(line)
                if e.get('signal') in ('sweep', 'contract'):
                    entries.append(e)
            except json.JSONDecodeError:
                pass

    if not entries:
        return None

    # Skip if outputs are truncated
    max_out = max(len(e.get('output', '')) for e in entries)
    if max_out <= MIN_OUTPUT_LEN:
        return None

    # Group scores by (signal, task_id)
    scores_by_sig_task: dict[tuple[str, str], list[float]] = defaultdict(list)
    for entry in entries:
        level = entry.get('level', -1)
        if level < 0 or level > 4:
            continue
        task_id = f'C{level + 1}'
        sig = entry.get('signal', 'sweep')
        src = _best_source(entry)
        alt = entry.get('workdir_snippet', '') if src != entry.get('workdir_snippet', '') else entry.get('output', '')
        if not src.strip():
            scores_by_sig_task[(sig, task_id)].append(0.0)
            continue
        result = run_functional_tests(src, task_id)
        if result['functional_score'] == 0 and alt.strip():
            alt_result = run_functional_tests(alt, task_id)
            if alt_result['functional_score'] > 0:
                result = alt_result
        scores_by_sig_task[(sig, task_id)].append(result['functional_score'])

    return dict(scores_by_sig_task)


def aggregate_model_scores(all_run_scores: list[dict]) -> dict[tuple[str, str], float]:
    """Merge multiple run dicts into per-(signal, task_id) mean scores."""
    combined: dict[tuple[str, str], list[float]] = defaultdict(list)
    for run_scores in all_run_scores:
        for key, scores in run_scores.items():
            combined[key].extend(scores)
    return {k: statistics.mean(v) for k, v in combined.items()}


def _row(sig_task_scores: dict[tuple[str, str], float], sig: str) -> dict[str, float]:
    """Extract per-task scores for a given signal name."""
    return {
        f'C{i}': sig_task_scores.get((sig, f'C{i}'), float('nan'))
        for i in range(1, 6)
    }


def main():
    valid_only = '--valid-only' in sys.argv

    valid_set: set[str] = set()
    if valid_only:
        if not os.path.exists(REGISTRY):
            print("Registry not found — run handoff_runregistry.py first.", file=sys.stderr)
            sys.exit(1)
        reg = json.load(open(REGISTRY))
        valid_set = {rd for rd, info in reg.items() if info['status'] == 'VALID'}

    # Group run dirs by model name (last segment after timestamp)
    model_runs: dict[str, list[str]] = defaultdict(list)
    for run_dir in sorted(os.listdir(RUNS_DIR)):
        if valid_only and run_dir not in valid_set:
            continue
        parts = run_dir.split('_', 2)  # 20260527_154906_model-name
        if len(parts) < 3:
            continue
        model_name = parts[2]
        model_runs[model_name].append(run_dir)

    print('\n' + '=' * 76)
    print('  Functional Retro-Score — H_loss vs claude-direct (all C1-C5)')
    print('=' * 76)
    print(f"  {'Model':<28} {'Signal':<9} {'C1':>5} {'C2':>5} {'C3':>5} {'C4':>5} {'C5':>5}  {'H_loss':>7}  SFR")
    print('-' * 76)

    for model, run_dirs in sorted(model_runs.items()):
        run_scores_list = []
        for run_dir in run_dirs:
            scores = score_run_dir(run_dir)
            if scores is not None:
                run_scores_list.append(scores)

        if not run_scores_list:
            continue

        sig_task_scores = aggregate_model_scores(run_scores_list)
        signals_present = sorted({sig for sig, _ in sig_task_scores})

        first = True
        for sig in signals_present:
            task_scores = _row(sig_task_scores, sig)
            h = compute_h_loss(task_scores)
            sfr = silent_failure_risk(0.85, h)
            row = [task_scores.get(f'C{i}', float('nan')) for i in range(1, 6)]
            row_str = '  '.join(f'{v:>5.2f}' if v == v else '   --' for v in row)
            sfr_str = ' ⚠' if sfr else ''
            label = model if first else ''
            print(f"  {label:<28} {sig:<9} {row_str}  {h:>7.3f}{sfr_str}")
            first = False

    print('=' * 76)
    print(f'  Reference (claude-direct):           sweep  1.00  1.00  1.00  1.00  1.00    1.000')
    print('=' * 76)
    print('\n  SFR = Silent Failure Risk (H_loss < 0.70)')


if __name__ == '__main__':
    main()
