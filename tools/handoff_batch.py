#!/usr/bin/env python3
"""
Batch signal scoring — LLM Impedance Probe (Signal 5).

Measures three dimensions of multi-task batching:
  1. task_completion_rate   — did the model complete each task?
  2. task_isolation_score   — did outputs stay in their own block?
  3. token_efficiency_ratio — word-count proxy vs N×single-task baseline

BATCH_SCORE = completion × 0.40 + isolation × 0.40 + efficiency × 0.20
(separate from IPS — different measurement dimension)
"""
import re
import sys

from handoff_behavioral import _strip_vibe_scaffolding

# ── Constants ────────────────────────────────────────────────────────────────
BATCH_SIZES: list[int] = [2, 4, 6]           # B1, B2, B3 (indexed by level)
ORCHESTRATOR_CONTEXT_TOKENS = 3000            # conservative overhead per sequential call
SINGLE_TASK_WORD_BASELINE   = 25             # expected words for one simple Python function

# Keywords uniquely associated with specific task indices across all batch sizes.
# Only highly distinctive terms — present in exactly one task's expected output.
# Key = 0-indexed task position; value = set of distinctive lowercase keywords.
_DISTINCTIVE_KEYWORDS: dict[int, set[str]] = {
    2: {'palindrome'},                   # B2 T3 / B3 T3
    3: {'flatten'},                      # B2 T4 / B3 T4
    4: {'decorator', 'duration', 'elapsed'},  # B3 T5 only
    5: {'word_freq', 'word frequency', 'frequency'},  # B3 T6 only
}


# ── Output parsing ────────────────────────────────────────────────────────────

def parse_batch_output(output: str, n_tasks: int) -> dict[int, str]:
    """
    Extract TASK_N labeled blocks from model output (after stripping scaffolding).

    Handles formats:
      TASK_1: <code>          plain label with colon
      TASK_1                  label on its own line
      **TASK_1**:  <code>     markdown bold
      ### TASK_1              markdown heading

    Returns {0: 'content_for_task_1', 1: 'content_for_task_2', ...}  (0-indexed).
    Missing or empty blocks are absent from the dict.
    """
    text = _strip_vibe_scaffolding(output)

    # Pattern matches any variant of a TASK_N label
    label_re = re.compile(
        r'(?:^|\n)\s*(?:\*{1,2}|#{1,3}\s*)?'
        r'TASK_(\d+)'
        r'(?:\*{1,2})?'
        r'\s*:?[ \t]*\n?',
        re.IGNORECASE,
    )

    # Collect (match_end_position, task_number) for all found labels
    found: list[tuple[int, int]] = []
    for m in label_re.finditer(text):
        try:
            num = int(m.group(1))
        except ValueError:
            continue
        if 1 <= num <= n_tasks:
            found.append((m.end(), num))

    result: dict[int, str] = {}
    for pos, (content_start, task_num) in enumerate(found):
        # Content ends at the start of the next label (or EOF)
        next_start = found[pos + 1][0] - len(label_re.search(
            text, found[pos + 1][0] - 120 if pos + 1 < len(found) else 0
        ).group(0)) if pos + 1 < len(found) else len(text)

        # Fallback: just use next content_start minus a bit
        if pos + 1 < len(found):
            # Walk back from next content_start to include the full label header
            prev_nl = text.rfind('\n', content_start, found[pos + 1][0])
            end = prev_nl if prev_nl > content_start else found[pos + 1][0]
        else:
            end = len(text)

        content = text[content_start:end].strip()
        result[task_num - 1] = content  # convert to 0-indexed

    return result


# ── Metric computation ────────────────────────────────────────────────────────

def _word_count(text: str) -> int:
    return len(text.split())


def _is_completed(content: str) -> bool:
    """Task is 'completed' when content has ≥ 10 non-whitespace characters."""
    return len(content.replace(' ', '').replace('\n', '').replace('\t', '')) >= 10


def _is_contaminated(content: str, own_idx: int, n_tasks: int) -> bool:
    """
    Returns True if the task block contains distinctive keywords
    from a *different* task (cross-contamination).
    Only checks highly distinctive terms to avoid false positives.
    """
    lower = content.lower()
    for other_idx, keywords in _DISTINCTIVE_KEYWORDS.items():
        if other_idx == own_idx or other_idx >= n_tasks:
            continue
        if any(kw in lower for kw in keywords):
            return True
    return False


def score_batch_run(output: str, level: int) -> dict:
    """
    Compute all batch metrics for a single probe run.

    Args:
        output:  raw CLI output (vibe scaffolding stripped internally)
        level:   0 = B1 (2 tasks), 1 = B2 (4 tasks), 2 = B3 (6 tasks)

    Returns dict with fields matching the JSONL spec:
        batch_size, tasks_requested, tasks_completed,
        label_compliance, task_completion_rate,
        task_isolation_score, token_efficiency_ratio,
        estimated_orchestrator_savings_tokens, batch_score
    """
    if not (0 <= level < len(BATCH_SIZES)):
        level = 0

    n_tasks = BATCH_SIZES[level]
    tasks   = parse_batch_output(output, n_tasks)

    # 1. Label compliance
    labels_found    = sum(1 for i in range(n_tasks) if i in tasks)
    label_compliance = round(labels_found / n_tasks, 3)

    # 2. Task completion
    tasks_completed     = sum(1 for i in range(n_tasks) if _is_completed(tasks.get(i, '')))
    task_completion_rate = round(tasks_completed / n_tasks, 3)

    # 3. Task isolation
    contaminated = sum(
        1 for i in range(n_tasks)
        if _is_contaminated(tasks.get(i, ''), i, n_tasks)
    )
    task_isolation_score = round((n_tasks - contaminated) / n_tasks, 3)

    # 4. Token efficiency (word-count proxy)
    total_words           = sum(_word_count(tasks.get(i, '')) for i in range(n_tasks))
    expected_words        = n_tasks * SINGLE_TASK_WORD_BASELINE
    token_efficiency_ratio = round(total_words / expected_words, 3) if expected_words else 1.0

    # 5. Orchestrator savings
    estimated_orchestrator_savings_tokens = (n_tasks - 1) * ORCHESTRATOR_CONTEXT_TOKENS

    # 6. BATCH_SCORE
    # Guard: nothing completed → score 0 (isolation/efficiency are meaningless on empty output)
    if tasks_completed == 0:
        batch_score = 0.0
    else:
        # token_efficiency_score: 1.0 at ratio=0.5, 0.5 at ratio=1.0, 0.0 at ratio≥2.0
        token_efficiency_score = max(0.0, min(1.0, 2.0 - token_efficiency_ratio))
        batch_score = round(
            task_completion_rate  * 0.40
            + task_isolation_score  * 0.40
            + token_efficiency_score * 0.20,
            3,
        )

    return {
        'batch_size':                            n_tasks,
        'tasks_requested':                       n_tasks,
        'tasks_completed':                       tasks_completed,
        'label_compliance':                      label_compliance,
        'task_completion_rate':                  task_completion_rate,
        'task_isolation_score':                  task_isolation_score,
        'token_efficiency_ratio':                token_efficiency_ratio,
        'estimated_orchestrator_savings_tokens': estimated_orchestrator_savings_tokens,
        'batch_score':                           batch_score,
    }


# ── Profile aggregation ───────────────────────────────────────────────────────

def compute_batch_profile(batch_runs: list[dict]) -> dict:
    """
    Aggregate batch metrics across runs into the YAML profile section.

    batch_runs: list of raw.jsonl entries where signal == 'batch'
    """
    from statistics import mean

    size_labels = {0: 'B1', 1: 'B2', 2: 'B3'}
    completion_by_size: dict[str, float] = {}
    isolation_by_size:  dict[str, float] = {}
    all_efficiency: list[float] = []
    all_savings:    list[float] = []
    all_scores:     list[float] = []

    for level, label in size_labels.items():
        lvl_runs = [r for r in batch_runs if r.get('level') == level]
        if not lvl_runs:
            continue
        compl = mean(r.get('task_completion_rate', 0.0) for r in lvl_runs)
        isol  = mean(r.get('task_isolation_score',  0.0) for r in lvl_runs)
        eff   = mean(r.get('token_efficiency_ratio', 1.0) for r in lvl_runs)
        sav   = mean(r.get('estimated_orchestrator_savings_tokens', 0) for r in lvl_runs)
        completion_by_size[label] = round(compl, 3)
        isolation_by_size[label]  = round(isol,  3)
        all_efficiency.append(eff)
        all_savings.append(sav)
        all_scores.extend(r.get('batch_score', 0.0) for r in lvl_runs)

    # Max reliable batch size: last size where completion >= 0.8 (in order B1→B3)
    max_reliable = 'none'
    for label in ['B1', 'B2', 'B3']:
        if completion_by_size.get(label, 0.0) >= 0.8:
            max_reliable = label

    return {
        'completion_by_size':   completion_by_size,
        'isolation_by_size':    isolation_by_size,
        'max_reliable_batch_size': max_reliable,
        'token_efficiency_ratio':  round(mean(all_efficiency), 3) if all_efficiency else 1.0,
        'estimated_orchestrator_savings_tokens': round(mean(all_savings)) if all_savings else 0,
        'batch_score':          round(mean(all_scores), 3) if all_scores else 0.0,
    }
