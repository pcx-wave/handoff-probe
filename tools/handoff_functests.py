import ast
import re
import os
import subprocess
import tempfile
from typing import Dict, List, Tuple, Optional, Any

TESTS: Dict[str, Dict[str, Any]] = {
    'C1': {
        'task_id': 'C1',
        'description': 'hello world',
        'test_type': 'stdout',
        'test_pattern': r'hello.?world',
        'flags': re.IGNORECASE,
    },
    'C2': {
        'task_id': 'C2',
        'description': 'reverse string',
        'test_type': 'function',
        'setup_code': '',
        'asserts': [
            ('reverse_str("abc")', '"cba"'),
            ('reverse_str("")', '""'),
            ('reverse_str("a")', '"a"'),
        ],
        'function_names': ['reverse_str', 'reverse_string', 'reverse'],
    },
    'C3': {
        'task_id': 'C3',
        'description': 'validate endpoint',
        'test_type': 'validate_endpoint',
    },
    'C4': {
        'task_id': 'C4',
        'description': 'async refactor',
        'test_type': 'async_refactor',
    },
    'C5': {
        'task_id': 'C5',
        'description': 'cache layer TTL=60',
        'test_type': 'cache_layer',
    },
}


def _strip_vibe_scaffold(output: str) -> str:
    """Extract the model's actual response from vibe or opencode scaffold output."""
    if '[vibe]' in output:
        idx = output.rfind('[vibe]')
        content = output[idx + len('[vibe]'):].strip()
        cut = content.find('\nTool calls:')
        return content[:cut].strip() if cut != -1 else content
    if '=== OPENCODE START ===' in output:
        # Collect all [opencode] text lines
        lines = []
        for line in output.splitlines():
            stripped = line.strip()
            if stripped.startswith('[opencode]'):
                lines.append(stripped[len('[opencode]'):].strip())
        return '\n'.join(lines) if lines else output
    return output


def extract_code(output: str) -> str:
    """Extract executable code from output, handling vibe scaffold, markdown fences, prose."""
    # Step 1: unwrap vibe scaffold
    src = _strip_vibe_scaffold(output)

    # Step 2: try fenced code blocks first (``` or ```python)
    fence_pattern = r'```(?:python)?\n(.*?)```'
    code_blocks = re.findall(fence_pattern, src, re.DOTALL)
    if not code_blocks:
        code_blocks = re.findall(r'```(?:python)?(.*?)```', src, re.DOTALL)
    if code_blocks:
        return '\n'.join(code_blocks).strip()

    # Step 3: no fences — strip residual ``` markers and return as-is
    cleaned = re.sub(r'^```(?:python)?\s*', '', src.strip())
    cleaned = re.sub(r'\s*```\s*$', '', cleaned)
    return cleaned


def run_test_c1(code: str) -> Tuple[int, int]:
    """Run C1 test: check for 'hello world'.

    Two strategies:
    1. Run as Python code and check stdout (handles print("hello world"))
    2. Check the text directly (handles model that outputs the text, not the code)
    """
    pattern = re.compile(r'hello.?world', re.IGNORECASE)

    # Strategy 2 first: if the extracted text itself matches, that's a valid answer
    # (model produced the expected output rather than the code that produces it)
    if pattern.search(code):
        return (1, 1)

    # Strategy 1: run as Python code
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(code)
        tmpfile = f.name
    try:
        result = subprocess.run(
            ['python3', tmpfile], timeout=5, capture_output=True, text=True,
        )
        if pattern.search(result.stdout):
            return (1, 1)
        return (0, 1)
    except subprocess.TimeoutExpired:
        return (0, 1)
    except Exception:
        return (0, 1)
    finally:
        try:
            os.unlink(tmpfile)
        except OSError:
            pass


def run_test_function(
    code: str,
    asserts: List[Tuple[str, str]],
    function_names: List[str],
) -> Tuple[int, int]:
    """Run function-based test by executing code in subprocess with assertions."""
    # Try to find the actual function name in the code
    found_name = None
    for name in function_names:
        if re.search(rf'\b{re.escape(name)}\b', code):
            found_name = name
            break
    
    # If no function found, try the first name as default
    if found_name is None:
        found_name = function_names[0] if function_names else 'func'
    
    # Build the test code: original code + assertion block
    test_code = code
    test_code += '\n\n# === Test assertions ===\n'
    
    for expr, expected in asserts:
        # Replace the function name in the expression with the found name
        test_expr = expr
        for name in function_names:
            test_expr = re.sub(rf'\b{re.escape(name)}\b', found_name, test_expr)
        
        safe_label = test_expr.replace('"', "'")
        assert_line = f'assert {test_expr} == {expected}, repr({test_expr})'
        test_code += assert_line + '\n'
        test_code += f'print("PASS: {safe_label}")\n'
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(test_code)
        tmpfile = f.name
    
    try:
        result = subprocess.run(
            ['python3', tmpfile],
            timeout=5,
            capture_output=True,
            text=True,
        )
        stdout = result.stdout
        stderr = result.stderr
        
        # Count PASS lines in stdout
        passed = len(re.findall(r'PASS:', stdout))
        total = len(asserts)
        
        return (passed, total)
    except subprocess.TimeoutExpired:
        return (0, len(asserts))
    except Exception:
        return (0, len(asserts))
    finally:
        try:
            os.unlink(tmpfile)
        except OSError:
            pass


def _run_in_subprocess(code: str, timeout: int = 5) -> Tuple[str, str, int]:
    """Write code to tmpfile, run it, return (stdout, stderr, returncode)."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(code)
        tmpfile = f.name
    try:
        r = subprocess.run(
            ['python3', tmpfile],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.stdout, r.stderr, r.returncode
    except subprocess.TimeoutExpired:
        return '', 'timeout', 124
    except Exception as e:
        return '', str(e), 1
    finally:
        try:
            os.unlink(tmpfile)
        except OSError:
            pass


def _parse_workdir_snippet(snippet: str) -> dict[str, str]:
    """Split '# FILE: name\\n<content>' workdir_snippet into {filename: content}."""
    files: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []
    for line in snippet.splitlines():
        if line.startswith('# FILE: '):
            if current_name is not None:
                files[current_name] = '\n'.join(current_lines)
            current_name = line[len('# FILE: '):].strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_name is not None:
        files[current_name] = '\n'.join(current_lines)
    return files


_FLASK_SYMBOLS = frozenset({
    'flask', 'sqlalchemy', 'models', 'request', 'jsonify',
    'Blueprint', 'Flask', 'app', 'db',
})

# Helper module names that models may introduce (e.g. validate fn in utils.py)
_HELPER_MODULES = ('utils', 'helpers', 'validators', 'validation', 'cache')


def _combine_workdir_sources(code: str) -> str:
    """Flatten a workdir_snippet into a single executable Python namespace.

    When a model puts helpers in utils.py and routes in app.py, running app.py
    alone fails on 'from utils import ...'. This function:
    1. Concatenates helper file contents first (defines pure functions)
    2. Appends app.py with helper-module imports stripped (already inlined)
    """
    if '# FILE: ' not in code:
        return code
    files = _parse_workdir_snippet(code)
    parts: list[str] = []
    for fname in ('utils.py', 'helpers.py', 'validators.py', 'validation.py', 'cache.py'):
        content = files.get(fname, '')
        if content:
            parts.append(content)
    app_content = files.get('app.py', '')
    if app_content:
        helper_pat = '|'.join(_HELPER_MODULES)
        app_clean = '\n'.join(
            line for line in app_content.splitlines()
            if not re.match(rf'from\s+({helper_pat})\b|import\s+({helper_pat})\b', line)
        )
        parts.append(app_clean)
    return '\n'.join(parts) if parts else code


def _uses_flask(node: ast.AST) -> bool:
    """True if the AST node references any Flask/SQLAlchemy symbol."""
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in _FLASK_SYMBOLS:
            return True
        if isinstance(child, ast.Attribute) and child.attr in _FLASK_SYMBOLS:
            return True
    return False


def _extract_validate_fn(code: str) -> str:
    """Extract pure validation logic from code, stripping Flask/SQLAlchemy context.

    Collects:
    - Safe imports (not flask/sqlalchemy)
    - Module-level assignments that don't use Flask (e.g. EMAIL_PATTERN = re.compile(...))
    - Validation functions that don't use Flask (name contains valid/check/verify)
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code

    parts: list[str] = []
    for node in tree.body:
        src = ast.get_source_segment(code, node) or ''
        if not src:
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if not any(m in src for m in ('flask', 'sqlalchemy', 'models', 'utils')):
                parts.append(src)
        elif isinstance(node, ast.Assign) and not _uses_flask(node):
            parts.append(src)
        elif isinstance(node, ast.FunctionDef):
            has_valid_name = any(
                kw in node.name.lower() for kw in ('valid', 'check', 'verify')
            )
            if has_valid_name and not _uses_flask(node):
                parts.append(src)

    return '\n'.join(parts) if parts else code


# Flask mock setup used by C3 and C4 harnesses so imports don't fail and
# @app.route remains an identity decorator (function stays callable).
_FLASK_MOCK_SETUP = '''\
import sys as _sys
from unittest.mock import MagicMock as _Mock
_flask_mock = _Mock()
_flask_mock.Flask.return_value.route = lambda *a, **kw: (lambda f: f)
_flask_mock.Flask.return_value.run = lambda *a, **kw: None
_flask_mock.jsonify = lambda x: x
_sys.modules['flask'] = _flask_mock
_sys.modules['flask_sqlalchemy'] = _Mock()
_sys.modules['models'] = _Mock()
_sys.modules['sqlalchemy'] = _Mock()
_sys.modules['sqlalchemy.ext'] = _Mock()
_sys.modules['sqlalchemy.ext.asyncio'] = _Mock()
_httpx_mock = _Mock()
import asyncio as _asyncio_mock_helper
class _AsyncClientMock:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): pass
    async def get(self, *a, **kw): return _Mock()
    async def post(self, *a, **kw): return _Mock()
_httpx_mock.AsyncClient = _AsyncClientMock
_sys.modules['httpx'] = _httpx_mock
_aiohttp_mock = _Mock()
class _AiohttpClientSession:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): pass
    async def get(self, *a, **kw): return _Mock()
    async def post(self, *a, **kw): return _Mock()
_aiohttp_mock.ClientSession = _AiohttpClientSession
_sys.modules['aiohttp'] = _aiohttp_mock
'''

# Shared find-and-call block for C3; references _flask_mock from _FLASK_MOCK_SETUP.
_C3_FIND_AND_CALL = '''
import inspect as _inspect
def _truthy(r):
    # Interpret many valid-result shapes:
    #   bool                -> itself
    #   (bool, errors)      -> the bool
    #   (errors, ...)       -> errors empty == valid
    #   {"is_valid"/"valid"}-> that flag
    #   {"errors": [...]}   -> errors empty == valid
    #   [errors]            -> empty list == valid (errors-list convention)
    if isinstance(r, bool): return r
    if isinstance(r, tuple):
        if r and isinstance(r[0], bool): return r[0]
        if r and isinstance(r[0], (list, tuple)): return len(r[0]) == 0
        return bool(r[0]) if r else False
    if isinstance(r, dict):
        if "is_valid" in r: return bool(r["is_valid"])
        if "valid" in r: return bool(r["valid"])
        if "errors" in r: return len(r["errors"]) == 0
        return bool(r)
    if isinstance(r, list): return len(r) == 0
    return bool(r)

def _call_any(_fn, _data):
    """Try several calling conventions; return result of the first that does not
    raise TypeError. Handles validate(data) / validate(username, age, email) /
    validate(**fields) / validate() route-style — so a functionally-correct
    solution is not scored 0 merely for choosing a different signature."""
    try:
        _names = [p.name for p in _inspect.signature(_fn).parameters.values()
                  if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY, p.POSITIONAL_ONLY)]
    except (ValueError, TypeError):
        _names = []
    _attempts = [lambda: _fn(_data)]                                  # single dict
    if _names and all(n in _data for n in _names):
        _attempts.append(lambda: _fn(*[_data[n] for n in _names]))   # positional, matched by name
    _kw = {n: _data[n] for n in _names if n in _data}
    if _kw:
        _attempts.append(lambda: _fn(**_kw))                         # filtered kwargs
    _attempts.append(lambda: _fn())                                  # route-style (request mock)
    _last = None
    for _a in _attempts:
        try:
            return _a()
        except TypeError as _e:
            _last = _e
    raise _last if _last else RuntimeError("uncallable")

_fn = None
for _name in ["validate", "validate_user", "is_valid", "validate_user_data",
              "validate_input", "validate_data", "check_user"]:
    _fn = globals().get(_name)
    if callable(_fn): break
if _fn is None:
    for _name, _obj in list(globals().items()):
        if callable(_obj) and any(k in _name.lower() for k in ("valid", "check")) \\
                and not _name.startswith("_"):
            _fn = _obj; break

if _fn is None:
    print("NO_FN_FOUND")
else:
    for _data, _label, _expect_valid in [
        ({"email": "user@example.com", "username": "alice", "age": 30,
          "name": "Alice", "password": "secret123"}, "valid_accepted", True),
        ({"email": "notanemail", "username": "alice", "age": 30,
          "name": "Alice", "password": "secret123"}, "invalid_rejected", False),
    ]:
        _flask_mock.request.get_json.return_value = _data
        _flask_mock.request.json = _data
        try:
            r = _call_any(_fn, _data)
            is_ok = _truthy(r)
            if is_ok == _expect_valid:
                print(f"PASS: {_label}")
            else:
                print(f"FAIL: {_label} got {r!r}")
        except Exception as e:
            print(f"FAIL: {_label} {e}")
'''


def run_test_c3(code: str) -> Tuple[int, int]:
    """C3: validate endpoint. 2 assertions: valid input accepted, invalid email rejected.

    Primary path: extract pure validation function (no Flask symbols) and call directly.
    Fallback: mock Flask + exec full source — handles models that put validation inline
    inside the Flask route handler (the most common real-world output pattern).
    """
    passed = 0
    total = 2

    # Combine workdir files into a single namespace (utils.py + app.py merged).
    # This handles contract-style outputs where validate fn lives in utils.py.
    combined = _combine_workdir_sources(code)
    fn_code = _extract_validate_fn(combined)

    # Primary: pure extracted function + Flask mocks
    harness = _FLASK_MOCK_SETUP + fn_code + _C3_FIND_AND_CALL
    stdout, _, _ = _run_in_subprocess(harness)
    passed = stdout.count('PASS:')

    if passed == 0 and 'NO_FN_FOUND' in stdout:
        # Fallback: mock Flask, exec combined source — @app.route becomes identity
        # decorator so route functions are directly callable with request mocked.
        harness = _FLASK_MOCK_SETUP + combined + _C3_FIND_AND_CALL
        stdout, _, _ = _run_in_subprocess(harness)
        passed = stdout.count('PASS:')

    return (passed, total)


# C4 async test: mock deps + find and run any async function.
# Tries DataProcessor first (some models introduce it), then functions from the
# original sync_module names (get_data, process), then any coroutine in globals.
_C4_ASYNC_TEST = '''
async def _find_and_run():
    import asyncio as _asyncio
    _dp_cls = globals().get('DataProcessor')
    if _dp_cls is not None:
        try:
            _r = await _dp_cls("test").process_item({"name": "x", "val": 2})
            if isinstance(_r, dict):
                print("PASS: async_runs_ok")
                return
        except Exception:
            pass
    for _fname in ['get_data', 'process', 'process_data', 'handle_request']:
        _fn = globals().get(_fname)
        if _asyncio.iscoroutinefunction(_fn):
            try:
                await _fn()
                print("PASS: async_runs_ok")
                return
            except Exception:
                pass
    for _n, _obj in list(globals().items()):
        if not _n.startswith('_') and _asyncio.iscoroutinefunction(_obj):
            try:
                await _obj()
                print("PASS: async_runs_ok")
                return
            except Exception:
                pass

import asyncio
asyncio.run(_find_and_run())
'''


def run_test_c4(code: str) -> Tuple[int, int]:
    """C4: async refactor. 3 assertions: has async def, has await, runs with asyncio.

    Structural checks (AST) catch the common case where models produce async-looking
    but non-runnable Flask code. Runtime check mocks all dependencies and finds any
    coroutine function — DataProcessor, Flask route names, or any async callable.
    """
    passed = 0
    total = 3

    # Clean the source first: vibe wraps responses in a console scaffold
    # (=== VIBE START === / Workdir: / Agent:) and models fence code in ```blocks.
    # Feeding that raw to ast.parse fails on the header -> both structural asserts
    # wrongly fail. extract_code() unwraps scaffold + fences.
    clean = extract_code(code)

    # Structural asserts: prefer AST, but fall back to regex when ast.parse fails
    # (model output is frequently truncated mid-statement at the capture cap, which
    # is not evidence the async refactor is absent).
    has_async = has_await = False
    try:
        tree = ast.parse(clean)
        has_async = any(isinstance(n, ast.AsyncFunctionDef) for n in ast.walk(tree))
        has_await = any(isinstance(n, ast.Await) for n in ast.walk(tree))
    except SyntaxError:
        pass
    if not has_async:
        has_async = re.search(r'async\s+def\s+\w+\s*\(', clean) is not None
    if not has_await:
        has_await = re.search(r'\bawait\s+\w', clean) is not None
    passed += int(has_async) + int(has_await)

    harness = _FLASK_MOCK_SETUP + clean + _C4_ASYNC_TEST
    stdout, _, _ = _run_in_subprocess(harness)
    if 'PASS: async_runs_ok' in stdout:
        passed += 1

    return (passed, total)


def run_test_c5(code: str) -> Tuple[int, int]:
    """C5: cache layer TTL=60. 3 assertions: TTL=60 present, cache keyword, cache runs.

    Handles workdir_snippet format: SimpleCache may live in utils.py rather than app.py.
    _combine_workdir_sources flattens all files into a single executable namespace.
    """
    passed = 0
    total = 3

    # Flatten workdir_snippet so regex checks and harness see all file contents
    combined = _combine_workdir_sources(code)

    if re.search(r'\b60\b', combined):
        passed += 1

    if re.search(r'cache|ttl|lru', combined, re.IGNORECASE):
        passed += 1

    harness = (
        _FLASK_MOCK_SETUP + '\n' + combined
        + '''
import time as _time
import functools as _functools
_tested = False

def _try_cache_class(_cls):
    """Instantiate a cache class and verify set/get round-trips."""
    if not isinstance(_cls, type):
        return False
    for _kw in ({'ttl': 60}, {}):
        try:
            _c = _cls(**_kw)
        except Exception:
            continue
        try:
            _c.set('k', 'v')
            return _c.get('k') == 'v'
        except Exception:
            return False
    return False

# 1. Known cache class names, then ANY class exposing get + set
_candidates = []
for _name in ['SimpleCache', 'Cache', 'TTLCache', 'InMemoryCache', 'LRUCache']:
    _obj = globals().get(_name)
    if isinstance(_obj, type):
        _candidates.append(_obj)
for _n, _obj in list(globals().items()):
    if _n.startswith('_'):
        continue
    if isinstance(_obj, type) and hasattr(_obj, 'get') and hasattr(_obj, 'set') \
            and _obj not in _candidates:
        _candidates.append(_obj)
for _cls in _candidates:
    if _try_cache_class(_cls):
        print("PASS: cache_runs_ok")
        _tested = True
        break

# 2. Fallback: a functools.lru_cache-decorated function that actually calls
if not _tested:
    for _n, _obj in list(globals().items()):
        if _n.startswith('_'):
            continue
        if callable(_obj) and hasattr(_obj, 'cache_info') and hasattr(_obj, 'cache_clear'):
            try:
                _info = _obj.cache_info()
                if not isinstance(_info.hits, int):  # MagicMock auto-creates .hits but it's not int
                    continue
            except Exception:
                continue
            for _args in ((), (1,), ('k',)):
                try:
                    _obj(*_args)
                    print("PASS: cache_runs_ok")
                    _tested = True
                    break
                except TypeError:
                    continue
                except Exception:
                    break
            if _tested:
                break
# No working cache found -> assertion fails (no PASS printed)
'''
    )
    stdout, _, _ = _run_in_subprocess(harness)
    if 'PASS: cache_runs_ok' in stdout:
        passed += 1

    return (passed, total)


def run_functional_tests(output: str, task_id: str) -> Dict[str, Any]:
    """Extract code from output and run the appropriate test for the task."""
    task_config = TESTS.get(task_id)
    if task_config is None:
        return {
            'task_id': task_id,
            'code_extracted': False,
            'asserts_passed': 0,
            'asserts_total': 0,
            'functional_score': 0.0,
            'error': f'Unknown task_id: {task_id}',
        }
    
    code = extract_code(output)
    code_extracted = bool(code and code.strip())
    
    if not code_extracted:
        return {
            'task_id': task_id,
            'code_extracted': False,
            'asserts_passed': 0,
            'asserts_total': 0,
            'functional_score': 0.0,
            'error': 'No code extracted from output',
        }
    
    test_type = task_config.get('test_type', '')
    error: Optional[str] = None
    
    try:
        if test_type == 'stdout':
            passed, total = run_test_c1(code)
        elif test_type == 'function':
            asserts_list = task_config.get('asserts', [])
            function_names = task_config.get('function_names', [])
            passed, total = run_test_function(code, asserts_list, function_names)
        elif test_type == 'validate_endpoint':
            passed, total = run_test_c3(code)
        elif test_type == 'async_refactor':
            passed, total = run_test_c4(code)
        elif test_type == 'cache_layer':
            passed, total = run_test_c5(code)
        else:
            passed, total = 0, 0
            error = f'Unknown test_type: {test_type}'
    except Exception as e:
        passed, total = 0, 0
        error = str(e)
    
    functional_score = passed / total if total > 0 else 0.0
    
    return {
        'task_id': task_id,
        'code_extracted': code_extracted,
        'asserts_passed': passed,
        'asserts_total': total,
        'functional_score': functional_score,
        'error': error,
    }


def score_outputs(output_ref: str, output_del: str, task_id: str) -> Dict[str, Any]:
    """Score reference and delivery outputs, compute functional fidelity."""
    ref_result = run_functional_tests(output_ref, task_id)
    del_result = run_functional_tests(output_del, task_id)
    
    ref_score = ref_result.get('functional_score', 0.0)
    del_score = del_result.get('functional_score', 0.0)
    
    if ref_score > 0:
        functional_fidelity = del_score / ref_score
    else:
        functional_fidelity = 1.0 if del_score > 0 else 0.0
    
    return {
        'task_id': task_id,
        'ref_score': ref_score,
        'del_score': del_score,
        'functional_fidelity': functional_fidelity,
    }
