#!/usr/bin/env python3
"""C3 context-pointing experiment.

Tests one isolated change to the C3 (validate-endpoint) prompt: telling the
delegate *which existing files to read for context/conventions* before writing
— e.g. "read app.py to see how routes are structured, follow its style" —
without specifying where the output should land and without writing any of
the code or decomposing the task.

This is deliberately NOT destination routing ("put your output in app.py"),
which is a different intervention (placement ambiguity) that an earlier
version of this script tested with inconclusive, possibly counter-productive
results. Pointing at sources to *ground generation in* is the more literal
reading of "context engineering": shaping what populates the model's working
set before it acts, not where its output is filed.

Runs baseline vs context-pointed C3 back-to-back, same model, same
clean-workdir/seed setup, through the production VIBE_DELEGATE wrapper (G1
invocation symmetry), and scores both with the existing run_test_c3 oracle so
results are directly comparable to the canonical SWEEP/CONTRACT C3 numbers.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from handoff_probe import _run_single, _seed_sweep_stubs, _collect_workdir_files  # noqa: E402
from handoff_signals import get_signal  # noqa: E402
from handoff_functests import run_functional_tests  # noqa: E402

CONTEXT_HINTS = {
    'sweep': (
        ' Before writing it, read the existing app.py to see how routes are '
        'structured here (the Flask app instance, @app.route + jsonify '
        'conventions) and follow the same style.'
    ),
    'contract': (
        ' Before writing, read the existing app.py and utils.py to see how '
        'routes and helpers are structured here, and follow the same '
        'conventions.'
    ),
}


def _build_variants(style: str) -> dict:
    """Return {'baseline': <C3 prompt>, 'context_pointed': <C3 prompt + read-first hint>}."""
    c3 = get_signal(style).get_prompts()[2]
    return {'baseline': c3, 'context_pointed': c3 + CONTEXT_HINTS[style]}


def _score(output: str, workdir_content: str) -> dict:
    """Same primary/fallback source selection as handoff_probe._run for SWEEP/CONTRACT."""
    is_vibe_scaffold = '=== VIBE START ===' in output
    if is_vibe_scaffold and '[vibe]' not in output:
        primary, fallback = workdir_content, output
    elif output.strip():
        primary, fallback = output, workdir_content
    else:
        primary, fallback = workdir_content, ''
    fr = run_functional_tests(primary, 'C3')
    if fr['functional_score'] == 0 and fallback.strip():
        fr_fb = run_functional_tests(fallback, 'C3')
        if fr_fb['functional_score'] > fr['functional_score']:
            fr = fr_fb
    return fr


def _run_one(model: str, prompt: str) -> dict:
    tmp_workdir = tempfile.mkdtemp(prefix='routing_')
    try:
        _seed_sweep_stubs(tmp_workdir)  # C3 needs app.py/models.py/utils.py present
        t0 = time.time()
        output, exit_code = _run_single('vibe', prompt, tmp_workdir)
        elapsed = round(time.time() - t0, 2)
        workdir_content = _collect_workdir_files(tmp_workdir)
    finally:
        shutil.rmtree(tmp_workdir, ignore_errors=True)
    fr = _score(output, workdir_content)
    return {
        'exit_code': exit_code,
        'elapsed_s': elapsed,
        'functional_score': fr['functional_score'],
        'asserts_passed': fr['asserts_passed'],
        'asserts_total': fr['asserts_total'],
        'code_extracted': fr['code_extracted'],
    }


def main():
    ap = argparse.ArgumentParser(description='C3 context-pointing A/B experiment (baseline vs read-existing-files-first)')
    ap.add_argument('--model', required=True)
    ap.add_argument('--styles', default='sweep,contract',
                    help='comma-separated: sweep, contract, or both (default: both)')
    ap.add_argument('--runs', type=int, default=5, help='trials per arm (default: 5)')
    ap.add_argument('--out', default='', help='optional path to write raw JSONL results')
    args = ap.parse_args()

    styles = [s.strip() for s in args.styles.split(',') if s.strip()]
    out_f = open(args.out, 'a') if args.out else None
    results = {style: {'baseline': [], 'context_pointed': []} for style in styles}

    for style in styles:
        variants = _build_variants(style)
        for arm in ('baseline', 'context_pointed'):
            prompt = variants[arm]
            print(f'\n[{style}/{arm}] {args.runs} runs, model={args.model}')
            for i in range(args.runs):
                r = _run_one(args.model, prompt)
                results[style][arm].append(r)
                print(f'  run {i+1}: func={r["functional_score"]:.2f} '
                      f'({r["asserts_passed"]}/{r["asserts_total"]}) '
                      f'exit={r["exit_code"]} {r["elapsed_s"]}s')
                if out_f:
                    entry = {'ts': datetime.now().isoformat(), 'model': args.model,
                             'style': style, 'arm': arm, 'run': i + 1, **r}
                    out_f.write(json.dumps(entry) + '\n')
                    out_f.flush()

    if out_f:
        out_f.close()

    print('\n=== C3 context-pointing — baseline vs read-existing-files-first ===')
    for style in styles:
        for arm in ('baseline', 'context_pointed'):
            scores = [r['functional_score'] for r in results[style][arm]]
            avg = sum(scores) / len(scores) if scores else 0.0
            print(f'  {style:9s} {arm:9s}  avg_func={avg:.2f}  n={len(scores)}  '
                  f'scores={[round(s, 2) for s in scores]}')
        b = [r['functional_score'] for r in results[style]['baseline']]
        rt = [r['functional_score'] for r in results[style]['context_pointed']]
        if b and rt:
            delta = (sum(rt) / len(rt)) - (sum(b) / len(b))
            print(f'  {style:9s} delta (context_pointed - baseline) = {delta:+.2f}')


if __name__ == '__main__':
    main()
