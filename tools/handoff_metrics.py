"""
Handoff metrics and scoring functions.
Stdlib only: subprocess, tempfile, re, os, statistics.
"""

import os
import re
import statistics
import subprocess
import tempfile


def format_fidelity(outputs: list[str], signal_name: str) -> float:
    """Return fraction of outputs that are code-only (no prose lines)."""
    prose_indicators = [
        'Here', 'Sure', 'This function', 'The',
        'I ', 'Note:', 'Explanation:', 'Let me', 'Below', 'Above'
    ]
    code_only_count = 0
    for output in outputs:
        lines = output.split('\n')
        has_prose = False
        for line in lines:
            stripped = line.strip()
            if any(stripped.startswith(indicator) for indicator in prose_indicators):
                has_prose = True
                break
        if not has_prose:
            code_only_count += 1
    total = len(outputs)
    return code_only_count / total if total > 0 else 0.0


def per_run_format_fidelity(outputs: list[str]) -> list[float]:
    """Return [0.0 or 1.0] for each output: 1.0 if no prose indicators found."""
    prose_indicators = [
        'Here', 'Sure', 'This function', 'The',
        'I ', 'Note:', 'Explanation:', 'Let me', 'Below', 'Above'
    ]
    results = []
    for output in outputs:
        lines = output.split('\n')
        has_prose = any(
            line.strip().startswith(ind)
            for line in lines
            for ind in prose_indicators
        )
        results.append(0.0 if has_prose else 1.0)
    return results


def per_run_noise_rejection(outputs: list[str]) -> list[float]:
    """Return [0.0 or 1.0] for each output: 1.0 if no noise keywords found."""
    noise_keywords = [
        'meeting', 'coffee', 'plant', 'parking', 'standup',
        'lunch', 'roadmap', 'alice', 'budget', 'watering'
    ]
    results = []
    for output in outputs:
        lower = output.lower()
        contaminated = any(kw in lower for kw in noise_keywords)
        results.append(0.0 if contaminated else 1.0)
    return results


def per_run_completion_rate(outputs: list[str], min_chars: int = 10) -> list[float]:
    """Return [0.0 or 1.0] for each output: 1.0 if output has >= min_chars non-whitespace.

    Uses char count (not line count) so single-line correct answers (e.g. "hello world"
    for C1 trivial tasks) are not penalised. min_chars=10 filters empty/near-empty only.
    """
    results = []
    for output in outputs:
        results.append(1.0 if len(output.strip()) >= min_chars else 0.0)
    return results


def noise_rejection(outputs: list[str]) -> float:
    """Return 1.0 - (contaminated / total) where contaminated has noise keywords."""
    noise_keywords = [
        'meeting', 'coffee', 'plant', 'parking', 'standup',
        'lunch', 'roadmap', 'alice', 'budget', 'watering'
    ]
    contaminated = 0
    for output in outputs:
        lower_output = output.lower()
        if any(keyword in lower_output for keyword in noise_keywords):
            contaminated += 1
    total = len(outputs)
    return 1.0 - (contaminated / total) if total > 0 else 1.0


def syntax_pass(outputs: list[str]) -> list[bool]:
    """Return list of bool indicating if each output is syntactically valid Python."""
    results = []
    for output in outputs:
        # Strip markdown fences
        cleaned_lines = []
        for line in output.split('\n'):
            if not line.strip().startswith('```'):
                cleaned_lines.append(line)
        cleaned = '\n'.join(cleaned_lines)
        
        # Write to temp file
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.py',
            delete=False
        ) as tmp:
            tmp.write(cleaned)
            tmp_path = tmp.name
        
        try:
            result = subprocess.run(
                ['python3', '-m', 'py_compile', tmp_path],
                capture_output=True,
                text=True
            )
            results.append(result.returncode == 0)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    return results


def completion_rate(outputs: list[str], min_chars: int = 10) -> float:
    """Return fraction of outputs with >= min_chars non-whitespace characters.

    Uses char count (not line count) so single-line correct answers (e.g. "hello world"
    for C1 trivial tasks) are not penalised. min_chars=10 filters empty/near-empty only.
    """
    count = sum(1 for o in outputs if len(o.strip()) >= min_chars)
    total = len(outputs)
    return count / total if total > 0 else 0.0


def verbosity_index(outputs: list[str], task_complexity: int) -> float:
    """Return avg_tokens / task_complexity."""
    token_counts = [len(o.split()) for o in outputs]
    avg_tokens = statistics.mean(token_counts) if token_counts else 0.0
    return avg_tokens / task_complexity if task_complexity > 0 else 0.0


def bandwidth_threshold(fidelity_by_level: list[float]) -> str:
    """Return 'L{i+1}' for first index where value < 0.7, or 'L5+'."""
    for i, value in enumerate(fidelity_by_level):
        if value < 0.7:
            return f'L{i + 1}'
    return 'L5+'


def compute_ips(metrics: dict) -> float:
    """Compute IPS score from metrics dict with weighted formula."""
    ff = metrics.get('format_fidelity', 0.0)
    nr = metrics.get('noise_rejection', 0.0)
    cr = metrics.get('completion_rate', 0.0)
    vn = metrics.get('verbosity_normalized', 0.0)
    bl = metrics.get('bandwidth_level', 0)
    
    ips = (
        ff * 0.25
        + nr * 0.20
        + cr * 0.25
        + (1.0 - min(vn, 1.0)) * 0.15
        + (bl / 5.0) * 0.15
    )
    return max(0.0, min(1.0, ips))


def assign_archetype(metrics: dict) -> str:
    """Assign archetype based on metrics."""
    bandwidth_map = {'L1': 1, 'L2': 2, 'L3': 3, 'L4': 4, 'L5': 5, 'L5+': 5}
    
    vi = metrics.get('verbosity_index', 0.0)
    fd = metrics.get('format_discipline', 0.0)
    bt = metrics.get('bandwidth_threshold', 'L1')
    nr = metrics.get('noise_rejection', 0.0)
    
    if vi < 0.4 and fd > 0.85:
        return 'dense'
    elif bandwidth_map.get(bt, 0) >= 4 and nr > 0.75:
        return 'tolerant'
    else:
        return 'structured'


# ---------------------------------------------------------------------------
# H_loss — functional fidelity relative to claude-direct reference
# ---------------------------------------------------------------------------

_REFERENCE_SCORES: dict[str, float] = {'C1': 1.0, 'C2': 1.0, 'C3': 1.0, 'C4': 1.0, 'C5': 1.0}


def compute_h_loss(functional_scores: dict[str, float],
                   reference_scores: dict[str, float] | None = None) -> float:
    """Return H_loss = mean(delegated / reference) across C1-C3.

    H_loss = 1.0  → no functional degradation vs claude-direct
    H_loss < 1.0  → delegation loses functional correctness
    H_loss > 1.0  → delegation somehow beats reference (flag it)
    """
    ref = reference_scores if reference_scores is not None else _REFERENCE_SCORES
    ratios = []
    for task_id, ref_score in ref.items():
        del_score = functional_scores.get(task_id, 0.0)
        if ref_score > 0:
            ratios.append(del_score / ref_score)
        else:
            ratios.append(1.0 if del_score > 0 else 0.0)
    return round(statistics.mean(ratios), 4) if ratios else 0.0


def silent_failure_risk(ips: float, h_loss: float) -> bool:
    """True when channel proxy (IPS) looks good but functional output is degraded."""
    return ips > 0.80 and h_loss < 0.70
