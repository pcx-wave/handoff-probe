"""
Handoff-probe Level 2 — behavioral verification.
Checks whether LLM output actually performs the requested task.
Stdlib only. All checks are fail-safe: any exception returns 0.0.
"""

import ast
import os
import re
import subprocess
import tempfile


def _strip_fences(code: str) -> str:
    lines = code.split('\n')
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('```'):
            continue
        cleaned.append(line)
    return '\n'.join(cleaned).strip()


def _strip_vibe_scaffolding(output: str) -> str:
    """Extract model response from vibe-delegate scaffolding output.

    Vibe wraps model output in:
      === VIBE START === ... ===================
      [read] ...
      [vibe] <actual model output>
      Tool calls: N | ...
    This strips the wrapper so behavioral checks see only the code/text.
    """
    lines = output.split('\n')
    # Find the closing === of the header block
    header_end = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if i > 0 and stripped.startswith('===') and stripped.endswith('==='):
            header_end = i + 1
            break
    if header_end == 0:
        return output  # not vibe scaffolding, return as-is
    content_lines = []
    for line in lines[header_end:]:
        stripped = line.strip()
        if stripped.startswith('Tool calls:') or stripped.startswith('=== VIBE') or stripped.startswith('Model '):
            break
        if stripped.startswith('[vibe]'):
            line = stripped[len('[vibe]'):].lstrip()
        elif any(stripped.startswith(p) for p in ('[tool]', '[read]', '[warn]', '[WARN]', '[log]', '=== SYNTAX')):
            continue
        content_lines.append(line)
    result = '\n'.join(content_lines).strip()
    return result if result else output


def _check_c1(output: str) -> float:
    if 'hello' in output.lower():
        return 1.0
    code = _strip_fences(_strip_vibe_scaffolding(output))
    if not code:
        return 0.0
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            fpath = f.name
        r = subprocess.run(
            ['python3', fpath],
            capture_output=True, text=True, timeout=5
        )
        return 1.0 if 'hello' in r.stdout.lower() else 0.0
    except Exception:
        return 0.0
    finally:
        try:
            os.unlink(fpath)
        except Exception:
            pass


def _check_c2(output: str) -> float:
    code = _strip_fences(_strip_vibe_scaffolding(output))
    if not code:
        return 0.0
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            fpath = f.name
        r = subprocess.run(
            ['python3', '-c', f'exec(open({repr(fpath)}).read()); print(reverse_string("abcde"))'],
            capture_output=True, text=True, timeout=5
        )
        return 1.0 if r.stdout.strip() == 'edcba' else 0.0
    except Exception:
        return 0.0
    finally:
        try:
            os.unlink(fpath)
        except Exception:
            pass


def _check_c3(output: str) -> float:
    # Match quoted (', ", `) or plain prose references to the route/endpoint
    if re.search(r'["\'\`]/?users/validate["\'\`]', output):
        return 1.0
    # Plain prose: "POST /users/validate" or "/users/validate"
    if re.search(r'/?users/validate', output):
        return 1.0
    code = _strip_fences(_strip_vibe_scaffolding(output))
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if 'validate' in node.value and 'users' in node.value:
                    return 1.0
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if 'validate' in node.name:
                    return 1.0
    except SyntaxError:
        pass
    return 0.0


def _check_c4(output: str) -> float:
    code = _strip_fences(_strip_vibe_scaffolding(output))
    try:
        tree = ast.parse(code)
        has_async = any(isinstance(n, ast.AsyncFunctionDef) for n in ast.walk(tree))
        has_await = any(isinstance(n, ast.Await) for n in ast.walk(tree))
        return 1.0 if has_async and has_await else 0.0
    except SyntaxError:
        has_async = bool(re.search(r'async def', code))
        has_await = bool(re.search(r'\bawait\b', code))
        return 1.0 if has_async and has_await else 0.0
    except Exception:
        return 0.0


def _check_c5(output: str) -> float:
    src = _strip_vibe_scaffolding(output)
    has_cache = bool(re.search(r'cache|_cache|ttl_cache|lru', src, re.IGNORECASE))
    has_ttl = bool(re.search(r'\b60\b|ttl', src, re.IGNORECASE))
    return 1.0 if has_cache and has_ttl else 0.0


_SWEEP_CHECKS = [_check_c1, _check_c2, _check_c3, _check_c4, _check_c5]


def behavioral_check(output: str, signal_name: str, level: int,
                     workdir_content: str = '') -> float:
    """
    Returns 0.0 or 1.0 — behavioral score for the given output.
    signal_name: 'dirac', 'step', 'ramp', 'sweep'
    level: 0-based index (only meaningful for 'sweep')
    workdir_content: concatenated content of files created by agent-mode CLIs
                     (vibe writes code to disk; text output is prose).
                     If provided, both sources are checked and max score is returned.
    All checks are fail-safe: any exception returns 0.0.
    """
    try:
        if signal_name != 'sweep':
            return 1.0
        if level < 0 or level >= len(_SWEEP_CHECKS):
            return 0.0
        check = _SWEEP_CHECKS[level]
        score_output = check(output)
        if score_output == 1.0:
            return 1.0
        # Agent channel fallback: check files written to workdir
        if workdir_content.strip():
            score_workdir = check(workdir_content)
            return max(score_output, score_workdir)
        return score_output
    except Exception:
        return 0.0
