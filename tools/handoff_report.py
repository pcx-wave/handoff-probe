#!/usr/bin/env python3
"""LLM impedance probe — profile generation and diff reporting."""
import sys

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from statistics import mean, stdev

import yaml
import handoff_metrics as hm
from handoff_batch import compute_batch_profile

PROFILES_DIR = Path.home() / '.handoff' / 'profiles'
BANDWIDTH_MAP = {'L1': 1, 'L2': 2, 'L3': 3, 'L4': 4, 'L5': 5, 'L5+': 5}


def _load_jsonl(path: str) -> list[dict]:
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def generate_profile(model: str, run_dir: str) -> None:
    log_path = os.path.join(run_dir, 'raw.jsonl')
    if not os.path.exists(log_path):
        print(f'ERROR: {log_path} not found', file=sys.stderr)
        sys.exit(1)

    runs = _load_jsonl(log_path)
    dirac_runs = [r for r in runs if r['signal'] == 'dirac']
    step_runs  = [r for r in runs if r['signal'] == 'step']
    ramp_runs  = [r for r in runs if r['signal'] == 'ramp']
    sweep_runs = [r for r in runs if r['signal'] == 'sweep']
    batch_runs = [r for r in runs if r['signal'] == 'batch']

    dirac_outputs = [r['output'] for r in dirac_runs]
    step_outputs  = [r['output'] for r in step_runs]
    ramp_by_level  = [[r['output'] for r in ramp_runs  if r['level'] == i] for i in range(5)]
    sweep_by_level = [[r['output'] for r in sweep_runs if r['level'] == i] for i in range(5)]

    dirac_ff = hm.format_fidelity(dirac_outputs, 'dirac') if dirac_outputs else 0.0
    dirac_vi = hm.verbosity_index(dirac_outputs, 1)       if dirac_outputs else 0.0
    step_nr  = hm.noise_rejection(step_outputs)           if step_outputs  else 0.0
    step_contaminated = step_nr < 1.0

    ramp_fidelity = [
        hm.format_fidelity(lvl, 'ramp') if lvl else 0.0
        for lvl in ramp_by_level
    ]
    ramp_bw = hm.bandwidth_threshold(ramp_fidelity)

    sweep_cr = [
        hm.completion_rate(lvl) if lvl else 0.0
        for lvl in sweep_by_level
    ]
    first_fail = next(
        (f'C{i+1}' for i, v in enumerate(sweep_cr) if v < 0.7), 'C5+'
    )

    # Per-run std computations
    dirac_ff_runs = hm.per_run_format_fidelity(dirac_outputs)
    dirac_ff_std  = round(stdev(dirac_ff_runs), 3) if len(dirac_ff_runs) >= 2 else 0.0
    dirac_vi_runs = [len(o.split()) for o in dirac_outputs]
    dirac_vi_std  = round(stdev(dirac_vi_runs) if len(dirac_vi_runs) >= 2 else 0.0, 3)
    step_nr_runs  = hm.per_run_noise_rejection(step_outputs)
    step_nr_std   = round(stdev(step_nr_runs), 3) if len(step_nr_runs) >= 2 else 0.0
    ramp_fidelity_std = [
        round(stdev(hm.per_run_format_fidelity(lvl)), 3) if len(lvl) >= 2 else 0.0
        for lvl in ramp_by_level
    ]
    sweep_cr_std = [
        round(stdev(hm.per_run_completion_rate(lvl)), 3) if len(lvl) >= 2 else 0.0
        for lvl in sweep_by_level
    ]
    # IPS std: compute IPS per run (approx from per-run signals)
    n_dirac = len(dirac_ff_runs)
    n_step  = len(step_nr_runs)
    min_n   = min(n_dirac, n_step) if n_dirac > 0 and n_step > 0 else 0
    ips_per_run = []
    for i in range(min_n):
        ff_i  = dirac_ff_runs[i] if i < len(dirac_ff_runs) else dirac_ff
        nr_i  = step_nr_runs[i]  if i < len(step_nr_runs) else step_nr
        cr_i  = mean(sweep_cr) if sweep_cr else 0.0
        vn_i  = min(dirac_vi / 10.0, 1.0)
        bl_i  = BANDWIDTH_MAP.get(ramp_bw, 3)
        ips_i = (ff_i * 0.25 + nr_i * 0.20 + cr_i * 0.25 + (1.0 - min(vn_i, 1.0)) * 0.15 + (bl_i / 5.0) * 0.15)
        ips_per_run.append(round(max(0.0, min(1.0, ips_i)), 3))
    ips_std = round(stdev(ips_per_run), 3) if len(ips_per_run) >= 2 else 0.0

    ips_metrics = {
        'format_fidelity':      dirac_ff,
        'noise_rejection':      step_nr,
        'completion_rate':      mean(sweep_cr) if sweep_cr else 0.0,
        'verbosity_normalized': min(dirac_vi / 10.0, 1.0),
        'bandwidth_level':      BANDWIDTH_MAP.get(ramp_bw, 3),
    }
    ips = hm.compute_ips(ips_metrics)

    arch_metrics = {
        'verbosity_index':     dirac_vi,
        'format_discipline':   dirac_ff,
        'bandwidth_threshold': ramp_bw,
        'noise_rejection':     step_nr,
    }
    archetype = hm.assign_archetype(arch_metrics)

    latency_values = [r.get('latency_ms', r.get('elapsed_s', 0) * 1000) for r in runs]
    latency_mean = round(mean(latency_values), 1) if latency_values else 0.0
    latency_std = round(stdev(latency_values), 1) if len(latency_values) >= 2 else 0.0

    profile = {
        'model':     model,
        'probed_at': datetime.now().isoformat(),
        'n_runs':    len(runs),
        'dirac': {
            'verbosity_natural': round(dirac_vi, 3),
            'format_discipline': round(dirac_ff, 3),
            'format_fidelity_std': dirac_ff_std,
            'verbosity_std': dirac_vi_std,
        },
        'step': {
            'noise_rejection':   round(step_nr, 3),
            'output_contaminated': step_contaminated,
            'noise_rejection_std': step_nr_std,
        },
        'ramp': {
            'bandwidth_threshold': ramp_bw,
            'fidelity_by_level':   [round(v, 3) for v in ramp_fidelity],
            'fidelity_std_by_level': ramp_fidelity_std,
        },
        'sweep': {
            'completion_by_complexity': [round(v, 3) for v in sweep_cr],
            'first_failure_level':      first_fail,
            'completion_std_by_complexity': sweep_cr_std,
        },
        'composite': {
            'ips':       round(ips, 3),
            'archetype': archetype,
            'latency_mean_ms': latency_mean,
            'latency_std_ms': latency_std,
            'ips_std': ips_std,
        },
    }
    if batch_runs:
        profile['batch'] = compute_batch_profile(batch_runs)

    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PROFILES_DIR / f'{model}.yaml'
    with open(out_path, 'w') as f:
        yaml.dump(profile, f, default_flow_style=False, sort_keys=False)
    print(f'Profile written to {out_path}')


def _winner(v1, v2, lower_better=False) -> str:
    if v1 == v2:
        return '='
    if lower_better:
        return '<' if v1 < v2 else '>'
    return '>' if v1 > v2 else '<'


def diff_profiles(model1: str, model2: str) -> None:
    p1_path = PROFILES_DIR / f'{model1}.yaml'
    p2_path = PROFILES_DIR / f'{model2}.yaml'
    for path, name in [(p1_path, model1), (p2_path, model2)]:
        if not path.exists():
            print(f'ERROR: profile not found: {path}', file=sys.stderr)
            sys.exit(1)

    p1 = yaml.safe_load(p1_path.read_text())
    p2 = yaml.safe_load(p2_path.read_text())

    W = 16
    h1 = model1[:W].ljust(W)
    h2 = model2[:W].ljust(W)

    print('=' * 65)
    print('  HANDOFF PROBE REPORT')
    print(f'  Models: {model1} vs {model2}')
    print(f'  Date:   {datetime.now().strftime("%Y-%m-%d")}')
    print('=' * 65)
    print(f'{"SIGNAL":<22} | {h1} | {h2} | winner')
    print('-' * 65)

    rows = [
        ('Dirac format_disc',  p1['dirac']['format_discipline'],  p2['dirac']['format_discipline'],  False),
        ('Dirac verbosity',    p1['dirac']['verbosity_natural'],   p2['dirac']['verbosity_natural'],   True),
        ('Step noise_rej',     p1['step']['noise_rejection'],      p2['step']['noise_rejection'],      False),
        ('Ramp bandwidth',     BANDWIDTH_MAP.get(p1['ramp']['bandwidth_threshold'], 3),
                               BANDWIDTH_MAP.get(p2['ramp']['bandwidth_threshold'], 3), False),
        ('Sweep completion',   mean(p1['sweep']['completion_by_complexity']),
                               mean(p2['sweep']['completion_by_complexity']),   False),
        ('IPS composite',      p1['composite']['ips'],             p2['composite']['ips'],             False),
    ]

    for label, v1, v2, lower_better in rows:
        w = _winner(v1, v2, lower_better)
        winner_label = model1 if w == '>' else (model2 if w == '<' else '=')
        print(f'  {label:<20} | {str(v1):<{W}} | {str(v2):<{W}} | {winner_label}')

    print('-' * 65)
    print(f'  {"ARCHETYPE":<20} | {p1["composite"]["archetype"]:<{W}} | {p2["composite"]["archetype"]:<{W}} |')
    print('=' * 65)

    ff1 = p1['sweep']['first_failure_level']
    ff2 = p2['sweep']['first_failure_level']
    bw1 = p1['ramp']['bandwidth_threshold']
    bw2 = p2['ramp']['bandwidth_threshold']
    print('\nRECOMMENDATION:')
    print(f'  -> Use {model1} for: complex tasks (up to {ff1}), noisy context, context up to {bw1}')
    print(f'  -> Use {model2} for: tasks up to {ff2}, clean context, context up to {bw2}')
    print()


def compare_profiles(yaml_paths: list[str]) -> None:
    """Multi-model comparison table from YAML paths (L1 metrics)."""
    profiles = []
    labels = []
    for p in yaml_paths:
        path = Path(p).expanduser()
        if not path.exists():
            print(f'WARNING: profile not found: {path}', file=sys.stderr)
            continue
        data = yaml.safe_load(path.read_text())
        profiles.append(data)
        # Short label: last 2 segments of stem, max 18 chars
        stem = path.stem
        label = stem[-18:] if len(stem) > 18 else stem
        labels.append(label)

    if not profiles:
        print('ERROR: no valid profiles found', file=sys.stderr)
        return

    N  = len(profiles)
    CW = 10  # column width per model

    # Header
    sep = '═' * (24 + (CW + 3) * N)
    print(sep)
    print('  L1 — IMPEDANCE COMPARISON')
    print(f'  Date: {datetime.now().strftime("%Y-%m-%d")}')
    print(sep)
    header = f'  {"METRIC":<22}'
    for lbl in labels:
        header += f'  {lbl[:CW]:<{CW}}'
    print(header)
    print('─' * len(sep))

    def _get(p, *keys, default=0.0):
        v = p
        for k in keys:
            v = v.get(k, default) if isinstance(v, dict) else default
        return v

    def _fmt(v) -> str:
        if isinstance(v, float):
            return f'{v:.3f}'
        return str(v)

    def _sweep_avg(p) -> float:
        vals = p.get('sweep', {}).get('completion_by_complexity', [0.0] * 5)
        if not isinstance(vals, list) or not vals:
            return 0.0
        return mean(vals)

    def _ramp_bw(p) -> int:
        bw = p.get('ramp', {}).get('bandwidth_threshold', 'L3')
        return BANDWIDTH_MAP.get(bw, 3)

    metrics = [
        ('IPS composite',       False, [_get(p, 'composite', 'ips')               for p in profiles]),
        ('Dirac format_disc',   False, [_get(p, 'dirac', 'format_discipline')      for p in profiles]),
        ('Dirac verbosity',     True,  [_get(p, 'dirac', 'verbosity_natural')      for p in profiles]),
        ('Step noise_rej',      False, [_get(p, 'step',  'noise_rejection')        for p in profiles]),
        ('Ramp bandwidth',      False, [_ramp_bw(p)                                for p in profiles]),
        ('Sweep avg compl.',    False, [_sweep_avg(p)                              for p in profiles]),
        ('First failure',       False, [p.get('sweep', {}).get('first_failure_level', '?') for p in profiles]),
        ('Latency mean ms',     True,  [_get(p, 'composite', 'latency_mean_ms')   for p in profiles]),
        ('Archetype',           False, [p.get('composite', {}).get('archetype', '?') for p in profiles]),
    ]

    for label, lower_better, values in metrics:
        # Find best value (skip non-numeric rows like archetype/first_failure)
        numeric = [v for v in values if isinstance(v, (int, float))]
        if numeric:
            best = min(numeric) if lower_better else max(numeric)
        else:
            best = None

        row = f'  {label:<22}'
        for v in values:
            cell = _fmt(v)
            mark = ' ★' if (best is not None and v == best) else '  '
            row += f'  {cell:<{CW}}{mark[0]}'
        print(row)

    print('─' * len(sep))
    # IPS ranking
    ips_pairs = sorted(zip([_get(p,'composite','ips') for p in profiles], labels), reverse=True)
    print('  RANKING (IPS):  ' + '  >  '.join(f'{lbl}({v:.3f})' for v, lbl in ips_pairs))
    print(sep)
    print()


def _bar(value: float, width: int = 20) -> str:
    filled = round(value * width)
    return '█' * filled + '░' * (width - filled)


def print_report(model: str) -> None:
    path = PROFILES_DIR / f'{model}.yaml'
    if not path.exists():
        print(f'ERROR: profile not found: {path}', file=sys.stderr)
        sys.exit(1)
    p = yaml.safe_load(path.read_text())

    W = 60
    print('═' * W)
    print(f'  IMPEDANCE PROBE REPORT — {model}')
    print(f'  Probed: {p["probed_at"][:19]}   n_runs: {p["n_runs"]}')
    print('═' * W)

    # ── Composite ──────────────────────────────────────────────────
    ips = p['composite']['ips']
    arch = p['composite']['archetype']
    ips_std = p['composite'].get('ips_std', 0)
    print(f'\n  IPS   {_bar(ips)}  {ips:.3f}  (+/-{ips_std:.3f})   archetype: {arch}')
    
    # IPS instability warnings
    unstable = []
    if p['dirac'].get('format_fidelity_std', 0) > 0.15:
        unstable.append(f"format_fidelity std={p['dirac']['format_fidelity_std']:.2f}")
    if p['step'].get('noise_rejection_std', 0) > 0.15:
        unstable.append(f"noise_rejection std={p['step']['noise_rejection_std']:.2f}")
    if p['composite'].get('ips_std', 0) > 0.15:
        unstable.append(f"IPS std={p['composite']['ips_std']:.2f}")
    for msg in unstable:
        print(f'  WARNING UNSTABLE: {msg}')

    # ── Dirac ──────────────────────────────────────────────────────
    print('\n  DIRAC — Natural response (no guidance)')
    ff  = p['dirac']['format_discipline']
    vi  = p['dirac']['verbosity_natural']
    vi_norm = min(vi / 20.0, 1.0)
    ff_std = p['dirac'].get('format_fidelity_std', 0)
    vi_std = p['dirac'].get('verbosity_std', 0)
    print(f'  format_discipline  {_bar(ff)}  {ff:.2f}  (+/-{ff_std:.2f})')
    print(f'  verbosity_index    {_bar(vi_norm)}  {vi:.1f} words/task  (+/-{vi_std:.2f})')

    # ── Step ───────────────────────────────────────────────────────
    print('\n  STEP — Noise rejection (40 % context noise)')
    nr = p['step']['noise_rejection']
    nr_std = p['step'].get('noise_rejection_std', 0)
    contaminated = p['step']['output_contaminated']
    print(f'  noise_rejection    {_bar(nr)}  {nr:.2f}  (+/-{nr_std:.2f})   contaminated: {contaminated}')

    # ── Ramp ───────────────────────────────────────────────────────
    bw = p['ramp']['bandwidth_threshold']
    fidelities = p['ramp']['fidelity_by_level']
    print(f'\n  RAMP — Format fidelity vs context size  (bandwidth: {bw})')
    for i, v in enumerate(fidelities):
        label = f'L{i+1}'
        print(f'  {label}  {_bar(v)}  {v:.2f}')

    # ── Sweep ──────────────────────────────────────────────────────
    ff_level = p['sweep']['first_failure_level']
    completions = p['sweep']['completion_by_complexity']
    print(f'\n  SWEEP — Completion rate vs task complexity  (first failure: {ff_level})')
    labels = ['C1 (trivial)', 'C2 (function)', 'C3 (REST endpoint)', 'C4 (refactor async)', 'C5 (cache design)']
    for i, (label, v) in enumerate(zip(labels, completions)):
        print(f'  {label:<20}  {_bar(v)}  {v:.2f}')

    if 'batch' in p:
        b = p['batch']
        print(f'\n  BATCH — Multi-task isolation & efficiency')
        sizes = ['B1', 'B2', 'B3']
        for sz in sizes:
            cr = b['completion_by_size'].get(sz)
            iso = b['isolation_by_size'].get(sz)
            if cr is None:
                continue
            print(f'  {sz} completion  {_bar(cr)}  {cr:.2f}')
            print(f'  {sz} isolation   {_bar(iso)}  {iso:.2f}')
        print(f'  max_reliable: {b["max_reliable_batch_size"]}   '
              f'token_eff: {b["token_efficiency_ratio"]:.2f}   '
              f'BATCH_SCORE: {b["batch_score"]:.3f}')

    # ── Latency ────────────────────────────────────────────────────
    lat_mean = p['composite'].get('latency_mean_ms', 0)
    lat_std  = p['composite'].get('latency_std_ms', 0)
    n        = p.get('n_runs', '?')
    print(f'\n  LATENCY -- Wall-clock time per run')
    print(f'  mean: {lat_mean:.0f} ms   std: {lat_std:.0f} ms   (n={n})')

    print('\n' + '═' * W)


def main():
    parser = argparse.ArgumentParser(
        description='Generate or compare LLM impedance profiles'
    )
    parser.add_argument('--profile', nargs=2, metavar=('MODEL', 'RUN_DIR'),
                        help='Generate YAML profile from a run directory')
    parser.add_argument('--diff', nargs=2, metavar=('MODEL1', 'MODEL2'),
                        help='Compare two saved profiles (side-by-side)')
    parser.add_argument('--report', metavar='MODEL',
                        help='Print ASCII impedance report for a model')
    parser.add_argument('--compare', nargs='+', metavar='YAML_PATH',
                        help='Multi-model comparison table (2-N yaml paths)')
    args = parser.parse_args()

    if args.profile:
        generate_profile(args.profile[0], args.profile[1])
    elif args.diff:
        diff_profiles(args.diff[0], args.diff[1])
    elif args.report:
        print_report(args.report)
    elif args.compare:
        compare_profiles(args.compare)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
