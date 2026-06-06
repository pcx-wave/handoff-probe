#!/usr/bin/env python3
"""LLM impedance probe — main CLI runner."""
import sys

import argparse
import json
import os
import subprocess
import tempfile
import time
import uuid
from datetime import datetime

from handoff_signals import SIGNALS, get_signal
from handoff_behavioral import behavioral_check
from handoff_functests import run_functional_tests
from handoff_metrics import compute_h_loss, silent_failure_risk
from handoff_batch import score_batch_run

# vibe-delegate wrapper is bundled alongside this module; allow env override.
VIBE_DELEGATE = os.environ.get(
    'VIBE_DELEGATE',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vibe-delegate'),
)

OPENCODE_DELEGATE = os.environ.get(
    'OPENCODE_DELEGATE',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'opencode-delegate'),
)

# Delegate wrappers (vibe-delegate, opencode-delegate) append one JSONL entry per
# run to this log, keyed by project = basename(workdir). Each probe run uses a
# unique clean workdir, so the basename uniquely identifies the entry it produced.
DELEGATE_RUNS_LOG = os.path.expanduser(
    os.environ.get('DELEGATE_RUNS_LOG', '~/.local/share/delegate-runs.jsonl')
)


def _lookup_delegate_tokens(workdir: str):
    """Return (tokens_in, tokens_out, tokens_total) for the delegation that just
    ran in `workdir`, by matching its basename in the delegate-runs.jsonl tail.

    Returns (None, None, None) if no entry was written (e.g. timeout/crash, or a
    CLI like gemini that does not log there). The match is exact and version-proof
    because each clean-workdir run has a unique basename."""
    proj = os.path.basename(workdir.rstrip('/'))
    try:
        with open(DELEGATE_RUNS_LOG) as f:
            tail = f.readlines()[-50:]
    except OSError:
        return None, None, None
    for line in reversed(tail):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get('project') == proj:
            return e.get('tokens_in'), e.get('tokens_out'), e.get('tokens_total')
    return None, None, None


_GEMINI_QUOTA_MARKERS = ('TerminalQuotaError', 'exhausted your daily quota', 'Quota exceeded')


def _collect_workdir_files(workdir: str) -> str:
    """Return concatenated contents of files created by an agent-mode CLI in workdir."""
    parts = []
    try:
        for fname in sorted(os.listdir(workdir)):
            if fname.startswith('.'):
                continue
            fpath = os.path.join(workdir, fname)
            if os.path.isfile(fpath):
                try:
                    content = open(fpath, 'r', errors='replace').read().strip()
                    if content:
                        parts.append(f'# FILE: {fname}\n{content}')
                except Exception:
                    pass
    except Exception:
        pass
    return '\n'.join(parts)


_OPENCODE_TIMEOUT_BY_LEN = [
    (500,  45),   # short prompts (C1/C2/DIRAC) — 45s
    (1000, 90),   # medium prompts (C3/RAMP) — 90s
    (9999, 150),  # long prompts (C4/C5) — 150s
]

_VIBE_MAX_TURNS_BY_LEN = [
    (300,  3),   # C1/C2/DIRAC (≤300 chars) — plain "write X" tasks, no file reads needed
    (1000, 5),   # C3/RAMP (≤1000 chars) — endpoint/validation tasks; models often read seed files first
    (9999, 8),   # C4/C5 — complex inline-code refactors
]


def _opencode_timeout(prompt: str) -> int:
    """Pick timeout based on prompt length as proxy for task complexity."""
    n = len(prompt)
    for threshold, secs in _OPENCODE_TIMEOUT_BY_LEN:
        if n <= threshold:
            return secs
    return 150


def _vibe_max_turns(prompt: str) -> int:
    """Pick vibe max-turns based on prompt length as proxy for task complexity."""
    n = len(prompt)
    for threshold, turns in _VIBE_MAX_TURNS_BY_LEN:
        if n <= threshold:
            return turns
    return 8


def _batch_timeout(level: int) -> int:
    """Batch timeouts: 60s for B1-B2, 120s for B3."""
    return 120 if level >= 2 else 60


def _run_single(cli: str, prompt: str, workdir: str, opencode_model: str = '',
                timeout_override: int = 0) -> tuple[str, int]:
    if cli == 'gemini':
        try:
            r = subprocess.run(
                ['gemini', '-p', prompt],
                capture_output=True, text=True, timeout=20
            )
            # gemini writes errors to stderr; surface them so quota/auth failures are visible
            output = r.stdout or ''
            if not output.strip():
                stderr = r.stderr or ''
                if any(m in stderr for m in _GEMINI_QUOTA_MARKERS):
                    return 'ERROR: gemini quota exhausted', 429
                if stderr.strip():
                    output = stderr
            return output, r.returncode
        except subprocess.TimeoutExpired:
            return '', 124
        except FileNotFoundError:
            return 'ERROR: gemini CLI not found', 1

    elif cli == 'vibe':
        tmpf = f'/tmp/task_{uuid.uuid4().hex}.txt'
        try:
            with open(tmpf, 'w') as f:
                f.write(prompt)
            t = timeout_override if timeout_override else (_opencode_timeout(prompt) + 30)
            r = subprocess.run(
                [VIBE_DELEGATE, workdir, tmpf,
                 str(_vibe_max_turns(prompt))],
                capture_output=True, text=True,
                timeout=t,
            )
            return r.stdout, r.returncode
        except subprocess.TimeoutExpired:
            return '', 124
        except FileNotFoundError:
            return 'ERROR: vibe-delegate not found', 1
        finally:
            if os.path.exists(tmpf):
                os.remove(tmpf)

    elif cli == 'opencode':
        # Delegate through the production opencode-delegate wrapper — the SAME path
        # the opencode skill uses (sets --dir <workdir> so opencode treats the
        # seeded folder as the project and reads existing files, and
        # --dangerously-skip-permissions so file writes are auto-approved in the
        # non-interactive run). A bare `opencode run` without --dir makes the agent
        # trust the prompt's CONTEXT instead of reading the real files, producing
        # spurious "already implemented, no changes needed" responses on edit tasks.
        try:
            t = timeout_override if timeout_override else _opencode_timeout(prompt)
            model = opencode_model or 'opencode/deepseek-v4-flash-free'
            r = subprocess.run(
                [OPENCODE_DELEGATE, workdir, prompt, str(t), model],
                capture_output=True, text=True,
                timeout=t + 30,
            )
            # Return the delegate stdout as output (contains [opencode] text +
            # scaffold). workdir files are collected separately by the caller
            # and stored in workdir_snippet. _best_source() picks the right one.
            return r.stdout or '', r.returncode
        except subprocess.TimeoutExpired:
            return '', 124
        except FileNotFoundError:
            return 'ERROR: opencode-delegate not found', 1

    return '', 1


_SWEEP_STUB_APP = '''\
from flask import Flask, request, jsonify
from models import User

app = Flask(__name__)

@app.route('/users', methods=['GET'])
def get_users():
    return jsonify([])

@app.route('/users/<int:user_id>', methods=['GET'])
def get_user(user_id):
    return jsonify({})

if __name__ == '__main__':
    app.run()
'''

_SWEEP_STUB_MODELS = '''\
from flask_sqlalchemy import SQLAlchemy
db = SQLAlchemy()

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80))
    email = db.Column(db.String(120))
    age = db.Column(db.Integer)

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
'''

_SWEEP_STUB_UTILS = '''\
def format_response(data, status=200):
    return {"data": data, "status": status}
'''


def _seed_sweep_stubs(workdir: str) -> None:
    """Create minimal stub files in workdir so vibe can find app.py/models.py/utils.py."""
    try:
        stubs = {'app.py': _SWEEP_STUB_APP, 'models.py': _SWEEP_STUB_MODELS, 'utils.py': _SWEEP_STUB_UTILS}
        for fname, content in stubs.items():
            with open(os.path.join(workdir, fname), 'w') as f:
                f.write(content)
    except Exception:
        pass  # fail-safe: if seeding fails, run continues without stubs


def _print_h_loss_summary(run_dir: str, model: str) -> None:
    """Read raw.jsonl, aggregate functional scores by (signal, task), print per-signal rows."""
    log_path = os.path.join(run_dir, 'raw.jsonl')
    scores_by_sig_task: dict[tuple[str, str], list[float]] = {}
    try:
        with open(log_path) as f:
            for line in f:
                entry = json.loads(line)
                sig = entry.get('signal')
                if sig not in ('sweep', 'contract'):
                    continue
                task_id = f'C{entry["level"] + 1}'
                fs = entry.get('functional_score')
                if fs is not None:
                    scores_by_sig_task.setdefault((sig, task_id), []).append(fs)
    except Exception as e:
        print(f'[H_loss] could not read log: {e}')
        return

    if not scores_by_sig_task:
        print('[H_loss] no functional scores found — run with --compare-reference and --signals sweep')
        return

    signals_present = sorted({sig for sig, _ in scores_by_sig_task})
    width = 62
    print('\n' + '=' * width)
    print(f'  H_loss report — {model}')
    print(f'  {"Signal":<9}  {"C1":>5}  {"C2":>5}  {"C3":>5}  {"C4":>5}  {"C5":>5}   H_loss')
    print('=' * width)
    for sig in signals_present:
        mean_scores = {
            f'C{i}': sum(v) / len(v)
            for i in range(1, 6)
            if (v := scores_by_sig_task.get((sig, f'C{i}')))
        }
        h_loss = compute_h_loss(mean_scores)
        row = '  '.join(
            f'{mean_scores[f"C{i}"]:>5.2f}' if f'C{i}' in mean_scores else '    -'
            for i in range(1, 6)
        )
        flag = '  ⚠' if h_loss < 0.70 else ''
        print(f'  {sig:<9}  {row}   {h_loss:.3f}{flag}')
    print('=' * width)
    if len(signals_present) > 1:
        sweep_h = compute_h_loss({
            f'C{i}': sum(v) / len(v)
            for i in range(1, 6)
            if (v := scores_by_sig_task.get(('sweep', f'C{i}')))
        })
        contract_h = compute_h_loss({
            f'C{i}': sum(v) / len(v)
            for i in range(1, 6)
            if (v := scores_by_sig_task.get(('contract', f'C{i}')))
        })
        delta = contract_h - sweep_h
        print(f'  contract vs sweep delta: {delta:+.3f}')


def main():
    parser = argparse.ArgumentParser(
        description='LLM impedance probe — measure model transfer function H(f)'
    )
    parser.add_argument('--model',   required=True, help='Model name (e.g. gemini, vibe)')
    parser.add_argument('--cli',     required=True, choices=['gemini', 'vibe', 'opencode'],
                        help='Which CLI to call')
    parser.add_argument('--compare-reference', action='store_true', default=False,
                        help='Run functional tests on SWEEP C1-C3 outputs and compute H_loss vs claude-direct')
    parser.add_argument('--runs',    type=int, default=5,
                        help='Runs per signal/level (default 5; applies to all signals including ramp/sweep)')
    parser.add_argument('--signals', default='all',
                        help='Comma-separated signals to run: dirac,step,ramp,sweep')
    parser.add_argument('--level-start', type=int, default=0,
                        help='Skip levels below this index (0-based, e.g. 3 = start at C4)')
    parser.add_argument('--level-end', type=int, default=999,
                        help='Skip levels above this index (0-based, e.g. 4 = stop after C5)')
    parser.add_argument('--workdir', default=os.getcwd(),
                        help='Workdir for vibe-delegate calls (default: cwd)')
    parser.add_argument('--clean-workdir', action='store_true', default=False,
                        help='Use a fresh tempdir per run (avoids codebase contamination)')
    parser.add_argument('--opencode-model', default='',
                        help='Model for opencode CLI (e.g. mistral/mistral-medium-latest)')
    parser.add_argument('--mistral-api-key', default='',
                        help='Mistral API key (sinon lit MISTRAL_API_KEY dans env)')
    args = parser.parse_args()

    # Inject Mistral API key into env for opencode
    mistral_key = getattr(args, 'mistral_api_key', '') or os.environ.get('MISTRAL_API_KEY', '')
    if mistral_key:
        os.environ['MISTRAL_API_KEY'] = mistral_key

    if args.signals == 'all':
        selected = SIGNALS
    else:
        names = [s.strip() for s in args.signals.split(',')]
        selected = [get_signal(n) for n in names]

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    run_dir = os.path.expanduser(f'~/.handoff/runs/{ts}_{args.model}')
    os.makedirs(run_dir, exist_ok=True)
    log_path = os.path.join(run_dir, 'raw.jsonl')

    # Write run metadata so any future reader can reconstruct test conditions
    # without relying on memory or dates.
    try:
        git_sha = subprocess.check_output(
            ['git', 'rev-parse', '--short', 'HEAD'],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except Exception:
        git_sha = 'unknown'
    import json as _json
    with open(os.path.join(run_dir, 'run_metadata.json'), 'w') as _mf:
        _json.dump({
            'probe_version': '1.7',
            'git_sha': git_sha,
            'timestamp': datetime.now().isoformat(),
            'cli': args.cli,
            'model': args.model,
            'signals': args.signals,
            'runs_per_level': args.runs,
            'clean_workdir': args.clean_workdir,
            'opencode_model': args.opencode_model or None,
            'level_start': args.level_start,
            'level_end': args.level_end if args.level_end != 999 else None,
        }, _mf, indent=2)

    total = 0
    with open(log_path, 'a') as logf:
        for signal in selected:
            prompts = signal.get_prompts()
            for level_idx, prompt in enumerate(prompts):
                if level_idx < args.level_start or level_idx > args.level_end:
                    continue
                n_runs = args.runs
                for run_idx in range(n_runs):
                    t0 = time.time()
                    run_workdir = args.workdir
                    tmp_workdir = None
                    if args.clean_workdir:
                        tmp_workdir = tempfile.mkdtemp(prefix='project_')
                        run_workdir = tmp_workdir
                    workdir_content = ''
                    try:
                        # For SWEEP C1-C3 with clean workdir: seed stub files so agent-mode
                        # CLIs don't fail on missing app.py. C4/C5 include code inline —
                        # stubs confuse vibe (it reads stub instead of using inline code).
                        if args.clean_workdir and signal.name in ('sweep', 'contract') and tmp_workdir and level_idx < 3:
                            _seed_sweep_stubs(tmp_workdir)
                        to = _batch_timeout(level_idx) if signal.name == 'batch' else 0
                        output, exit_code = _run_single(args.cli, prompt, run_workdir, args.opencode_model, timeout_override=to)
                        # Collect workdir files BEFORE cleanup — needed for agent channels
                        # (vibe writes code to files; text output is prose)
                        workdir_content = _collect_workdir_files(run_workdir)
                    finally:
                        if tmp_workdir and os.path.exists(tmp_workdir):
                            import shutil
                            shutil.rmtree(tmp_workdir, ignore_errors=True)
                    # Token usage for THIS run, recovered from the delegate log the
                    # wrapper just wrote (keyed by the unique clean-workdir basename).
                    # None for CLIs that don't log (gemini) or runs that didn't write
                    # (timeout/crash) — kept distinct from a real 0.
                    tok_in, tok_out, tok_total = _lookup_delegate_tokens(run_workdir)
                    elapsed = round(time.time() - t0, 2)
                    latency_ms = round(elapsed * 1000)
                    if signal.name == 'batch':
                        batch_metrics = score_batch_run(output, level_idx)
                        bscore = batch_metrics['batch_score']
                    else:
                        batch_metrics = {}
                        bscore = behavioral_check(output, signal.name, level_idx,
                                                  workdir_content=workdir_content)
                    entry = {
                        'ts': datetime.now().isoformat(),
                        'model': args.model,
                        'cli': args.cli,
                        'signal': signal.name,
                        'level': level_idx,
                        'run': run_idx + 1,
                        'prompt': prompt[:200],
                        'output': output[:12000],
                        'exit_code': exit_code,
                        'elapsed_s': elapsed,
                        'latency_ms': latency_ms,
                        'behavioral_score': bscore,
                        'tokens_in': tok_in,
                        'tokens_out': tok_out,
                        'tokens_total': tok_total,
                        'files_created': bool(workdir_content.strip()),
                        'workdir_snippet': workdir_content[:12000] if workdir_content.strip() else '',
                    }
                    entry.update(batch_metrics)
                    # Functional tests on SWEEP C1-C3 before writing log entry
                    fscore_str = ''
                    if args.compare_reference and signal.name in ('sweep', 'contract') and level_idx < 5:
                        task_id = f'C{level_idx + 1}'  # C1-C5
                        # Source selection: prefer the source that actually has the code.
                        # Primary: output when model responded inline; workdir when model
                        # wrote files only (no [vibe] text response in scaffold).
                        # Fallback: if primary scores 0 and there is a non-empty alternative
                        # source, try it — models that write code to files AND emit a brief
                        # [vibe] text response would otherwise be scored from the prose.
                        is_vibe_scaffold = '=== VIBE START ===' in output
                        if is_vibe_scaffold and '[vibe]' not in output:
                            primary, fallback = workdir_content, output
                        elif output.strip():
                            primary, fallback = output, workdir_content
                        else:
                            primary, fallback = workdir_content, ''
                        fr = run_functional_tests(primary, task_id)
                        if fr['functional_score'] == 0 and fallback.strip():
                            fr_fb = run_functional_tests(fallback, task_id)
                            if fr_fb['functional_score'] > fr['functional_score']:
                                fr = fr_fb
                        entry['functional_score'] = fr['functional_score']
                        entry['functional_asserts_passed'] = fr['asserts_passed']
                        entry['functional_asserts_total'] = fr['asserts_total']
                        fscore_str = f' func={fr["functional_score"]:.2f}'
                    logf.write(json.dumps(entry) + '\n')
                    logf.flush()
                    total += 1
                    if signal.name == 'ramp':
                        label = f'L{level_idx}'
                    elif signal.name in ('sweep', 'contract'):
                        label = f'C{level_idx + 1}'
                    elif signal.name == 'batch':
                        label = f'B{level_idx + 1}'
                    else:
                        label = ''
                    print(f'  [{signal.name}] {label} run {run_idx+1}: exit={exit_code} {elapsed}s{fscore_str}')

    # H_loss summary when --compare-reference
    if args.compare_reference:
        _print_h_loss_summary(run_dir, args.model)

    print(f'\nRun complete — {total} runs saved to {run_dir}')

    # Validity self-report: flag zeros that the harness can actually recover
    # (HARNESS_BLIND), untouched-seed artifacts (SEED_ONLY), and uniform-zero
    # levels — so a clean-exit-but-invalid measurement is loud, not silent.
    if args.compare_reference:
        try:
            from handoff_validity import audit_run_dir
            a = audit_run_dir(run_dir)
            blind = a['counts'].get('HARNESS_BLIND_SUSPECT', 0)
            if blind:
                print(f'  ⚠ VALIDITY: {blind} run(s) scored 0 but the harness recovers '
                      f'them on re-score — stored scores stale or harness-blind.')
            if a['counts'].get('SEED_ONLY'):
                print(f"  ⚠ VALIDITY: {a['counts']['SEED_ONLY']} run(s) wrote nothing "
                      f"beyond the seed (likely a scoring/seeding artifact, not capability).")
            if a['uniform_zero']:
                print(f"  ⚠ VALIDITY: uniform-zero levels {a['uniform_zero']} — verify "
                      f"this is real capability, not a setup/invocation artifact.")
        except Exception:
            pass

    print(f'Generate profile: python3 ~/tools/handoff_report.py --profile {args.model} {run_dir}')

    # Keep registry current so --valid-only is always usable after a run.
    try:
        import subprocess as _sp
        _reg = os.path.join(os.path.dirname(__file__), 'handoff_runregistry.py')
        _sp.run(['python3', _reg], capture_output=True, timeout=30)
    except Exception:
        pass


if __name__ == '__main__':
    main()
