#!/usr/bin/env python3
"""
Unit tests for handoff_functests.py

Covers extraction and scoring for every real output format encountered:
- vibe scaffold with [vibe] prose response (C1, C2)
- vibe scaffold without [vibe] — files written (C3)
- opencode plain text with markdown fences (C2, C3, C4, C5)
- opencode plain text without fences
- workdir_snippet multi-file format
- silent failure: stubs-only output that looks non-empty but contains no answer
"""
import sys

import pytest
from handoff_functests import (
    extract_code,
    _strip_vibe_scaffold,
    _parse_workdir_snippet,
    _extract_validate_fn,
    run_functional_tests,
)


# ---------------------------------------------------------------------------
# Fixtures — real output shapes from production runs
# ---------------------------------------------------------------------------

VIBE_C1_PROSE = """\
=== VIBE START ===
Workdir : /tmp/handoff_probe_abc123
Agent   : default
Model   : mistral-medium-3.5
Turns   : 3
Timeout : 180s
Prompt  : /tmp/vibe_probe_xyz.txt...
===================
  [read]  /tmp/vibe_probe_xyz.txt
  [vibe] Hello World
Tool calls: 1  |  warns: 0  |  sr_fails: 0
=== VIBE DONE (exit: 0) ===
"""

VIBE_C2_PROSE = """\
=== VIBE START ===
Workdir : /tmp/handoff_probe_def456
Agent   : default
===================
  [read]  /tmp/vibe_probe_abc.txt
  [vibe] def reverse_string(s):
    return s[::-1]
Tool calls: 1  |  warns: 0  |  sr_fails: 0
=== VIBE DONE (exit: 0) ===
"""

VIBE_C3_FILES = """\
=== VIBE START ===
Workdir : /tmp/handoff_probe_ghi789
Agent   : default
===================
  [read]  /tmp/vibe_probe_def.txt
  [read]  /tmp/handoff_probe_ghi789/app.py
  [tool]  search_replace [OK] file: /tmp/handoff_probe_ghi789/app.py
Tool calls: 5  |  warns: 0  |  sr_fails: 0
=== VIBE DONE (exit: 0) ===
"""

WORKDIR_C3_FULL = """\
# FILE: app.py
from flask import Flask, request, jsonify
import re

app = Flask(__name__)

EMAIL_PATTERN = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$')

def validate_user_data(data):
    errors = []
    email = data.get('email', '')
    username = data.get('username', '')
    if not EMAIL_PATTERN.match(email):
        errors.append('invalid email')
    if not username:
        errors.append('username required')
    return (len(errors) == 0, errors)

@app.route('/users/validate', methods=['POST'])
def validate_user():
    data = request.get_json()
    is_valid, errors = validate_user_data(data)
    return {'is_valid': is_valid, 'errors': errors}

if __name__ == '__main__':
    app.run()
# FILE: models.py
from flask_sqlalchemy import SQLAlchemy
db = SQLAlchemy()

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80))
    email = db.Column(db.String(120))
    age = db.Column(db.Integer)
# FILE: utils.py
def format_response(data, status=200):
    return {"data": data, "status": status}
"""

OPENCODE_C2_FENCED = """\
Here is a Python function to reverse a string:

```python
def reverse_string(s):
    return s[::-1]
```
"""

OPENCODE_C2_PLAIN = "def reverse_string(s):\n    return s[::-1]\n"

OPENCODE_C4_FENCED = """\
Here's the async refactored version:

```python
import asyncio
from typing import List, Dict

class DataProcessor:
    def __init__(self, name: str):
        self.name = name
        self.cache = {}

    async def process_item(self, item: Dict) -> Dict:
        result = {}
        for key, value in item.items():
            if isinstance(value, str):
                result[key] = value.upper()
            elif isinstance(value, int):
                result[key] = value * 2
            else:
                result[key] = value
        await asyncio.sleep(0.1)
        return result

    async def process_batch(self, items: List[Dict]) -> List[Dict]:
        return [await self.process_item(item) for item in items]

class APIHandler:
    def __init__(self):
        self.processor = DataProcessor("main")

    async def handle_request(self, data: Dict) -> Dict:
        return {"status": "success", "data": await self.processor.process_item(data)}
```
"""

OPENCODE_C5_FENCED = """\
```python
import time
from typing import Any, Dict, Optional
from dataclasses import dataclass

@dataclass
class CacheEntry:
    key: str
    value: Any
    expires_at: float

class SimpleCache:
    def __init__(self, max_size: int = 100, ttl: int = 60):
        self.max_size = max_size
        self.ttl = ttl
        self._cache: Dict[str, CacheEntry] = {}

    def get(self, key: str) -> Optional[Any]:
        entry = self._cache.get(key)
        if entry and entry.expires_at > time.time():
            return entry.value
        if entry:
            del self._cache[key]
        return None

    def set(self, key: str, value: Any) -> None:
        if len(self._cache) >= self.max_size:
            oldest = min(self._cache, key=lambda k: self._cache[k].expires_at)
            del self._cache[oldest]
        self._cache[key] = CacheEntry(key, value, time.time() + self.ttl)
```
"""

# C3: Flask route with validation INLINE — no separate pure function extracted.
# This is the most common real-world output pattern and was previously scoring 0.
FLASK_ROUTE_C3 = """\
from flask import Flask, request, jsonify
import re

app = Flask(__name__)

EMAIL_PATTERN = re.compile(r'^[^@]+@[^@]+\\.[^@]+$')

@app.route('/users/validate', methods=['POST'])
def validate_user():
    data = request.get_json()
    errors = []
    if not EMAIL_PATTERN.match(data.get('email', '')):
        errors.append('invalid email')
    if not data.get('username'):
        errors.append('username required')
    return jsonify({"is_valid": len(errors) == 0, "errors": errors})

if __name__ == '__main__':
    app.run()
"""

# C4: async Flask routes — real model output from the C4 sync_module prompt.
# Previously scored 2/3 at best (DataProcessor check always failed).
FLASK_ASYNC_C4 = """\
import asyncio
from flask import Flask, request, jsonify
from models import db, User

app = Flask(__name__)

@app.route('/api/data', methods=['GET'])
async def get_data():
    data = db.query(User).all()
    await asyncio.sleep(0)
    return jsonify({'data': str(data)})

@app.route('/api/process', methods=['POST'])
async def process():
    payload = request.json
    result = db.query(User).filter_by(id=payload['id']).first()
    await asyncio.sleep(0)
    return jsonify({'result': str(result)})
"""

# Silent failure: opencode returns seed stubs only (no actual answer)
SILENT_FAILURE_STUBS = """\
# FILE: app.py
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
# FILE: models.py
from flask_sqlalchemy import SQLAlchemy
db = SQLAlchemy()
# FILE: utils.py
def format_response(data, status=200):
    return {"data": data, "status": status}
"""


# ---------------------------------------------------------------------------
# _strip_vibe_scaffold
# ---------------------------------------------------------------------------

class TestStripVibeScaffold:
    def test_extracts_prose_response(self):
        result = _strip_vibe_scaffold(VIBE_C1_PROSE)
        assert result == 'Hello World'

    def test_extracts_multiline_code_response(self):
        result = _strip_vibe_scaffold(VIBE_C2_PROSE)
        assert 'def reverse_string' in result
        assert 'return s[::-1]' in result

    def test_no_vibe_marker_returns_original(self):
        plain = "def foo(): return 1"
        assert _strip_vibe_scaffold(plain) == plain

    def test_scaffold_without_vibe_response(self):
        # Files-only run — no [vibe] marker
        result = _strip_vibe_scaffold(VIBE_C3_FILES)
        # Should return original (no [vibe] marker)
        assert '[vibe]' not in result or result == VIBE_C3_FILES


# ---------------------------------------------------------------------------
# _parse_workdir_snippet
# ---------------------------------------------------------------------------

class TestParseWorkdirSnippet:
    def test_parses_all_files(self):
        files = _parse_workdir_snippet(WORKDIR_C3_FULL)
        assert set(files.keys()) == {'app.py', 'models.py', 'utils.py'}

    def test_app_py_contains_validate_fn(self):
        files = _parse_workdir_snippet(WORKDIR_C3_FULL)
        assert 'validate_user_data' in files['app.py']
        assert 'EMAIL_PATTERN' in files['app.py']

    def test_empty_snippet(self):
        assert _parse_workdir_snippet('') == {}


# ---------------------------------------------------------------------------
# extract_code
# ---------------------------------------------------------------------------

class TestExtractCode:
    def test_vibe_c1_prose(self):
        code = extract_code(VIBE_C1_PROSE)
        assert 'Hello World' in code

    def test_vibe_c2_prose(self):
        code = extract_code(VIBE_C2_PROSE)
        assert 'def reverse_string' in code

    def test_opencode_fenced(self):
        code = extract_code(OPENCODE_C2_FENCED)
        assert 'def reverse_string' in code
        assert '```' not in code

    def test_opencode_plain(self):
        code = extract_code(OPENCODE_C2_PLAIN)
        assert 'def reverse_string' in code

    def test_c4_fenced_extracts_class(self):
        code = extract_code(OPENCODE_C4_FENCED)
        assert 'async def process_item' in code
        assert 'await asyncio.sleep' in code


# ---------------------------------------------------------------------------
# C1 functional test
# ---------------------------------------------------------------------------

class TestC1:
    def test_vibe_prose_hello_world(self):
        r = run_functional_tests(VIBE_C1_PROSE, 'C1')
        assert r['functional_score'] == 1.0

    def test_python_print(self):
        r = run_functional_tests('print("hello world")', 'C1')
        assert r['functional_score'] == 1.0

    def test_wrong_output_fails(self):
        r = run_functional_tests('print("goodbye")', 'C1')
        assert r['functional_score'] == 0.0

    def test_silent_failure_stubs_fail(self):
        # Stubs have no hello world output
        r = run_functional_tests(SILENT_FAILURE_STUBS, 'C1')
        assert r['functional_score'] == 0.0


# ---------------------------------------------------------------------------
# C2 functional test
# ---------------------------------------------------------------------------

class TestC2:
    def test_vibe_prose_reverse(self):
        r = run_functional_tests(VIBE_C2_PROSE, 'C2')
        assert r['functional_score'] == 1.0

    def test_opencode_fenced(self):
        r = run_functional_tests(OPENCODE_C2_FENCED, 'C2')
        assert r['functional_score'] == 1.0

    def test_opencode_plain(self):
        r = run_functional_tests(OPENCODE_C2_PLAIN, 'C2')
        assert r['functional_score'] == 1.0

    def test_broken_reverse_fails(self):
        r = run_functional_tests('def reverse_string(s): return s', 'C2')
        assert r['functional_score'] == 0.0

    def test_silent_failure_stubs_fail(self):
        r = run_functional_tests(SILENT_FAILURE_STUBS, 'C2')
        assert r['functional_score'] == 0.0


# ---------------------------------------------------------------------------
# C3 functional test
# ---------------------------------------------------------------------------

class TestC3:
    def test_vibe_files_workdir(self):
        # vibe without [vibe] → workdir_snippet is the source
        r = run_functional_tests(WORKDIR_C3_FULL, 'C3')
        assert r['functional_score'] == 1.0

    def test_silent_failure_stubs_fail(self):
        # Seed stubs have no validate function
        r = run_functional_tests(SILENT_FAILURE_STUBS, 'C3')
        assert r['functional_score'] == 0.0

    def test_plain_validate_function(self):
        code = """\
import re
def validate(data):
    if not re.match(r'^[^@]+@[^@]+\\.[^@]+$', data.get('email', '')):
        return False
    return True
"""
        r = run_functional_tests(code, 'C3')
        assert r['functional_score'] == 1.0

    def test_flask_inline_route(self):
        # Validation logic is inline in the Flask route — no pure function extracted.
        # Fallback mock-Flask path should still score 1.0.
        r = run_functional_tests(FLASK_ROUTE_C3, 'C3')
        assert r['functional_score'] == 1.0


# ---------------------------------------------------------------------------
# C4 functional test
# ---------------------------------------------------------------------------

class TestC4:
    def test_opencode_fenced_async(self):
        r = run_functional_tests(OPENCODE_C4_FENCED, 'C4')
        assert r['functional_score'] == 1.0

    def test_sync_code_fails_structural(self):
        # No async def → structural checks fail
        sync_code = """\
class DataProcessor:
    def process_item(self, item):
        return {k: v.upper() if isinstance(v, str) else v*2 for k, v in item.items()}
"""
        r = run_functional_tests(sync_code, 'C4')
        assert r['functional_score'] < 1.0

    def test_silent_failure_stubs_fail(self):
        r = run_functional_tests(SILENT_FAILURE_STUBS, 'C4')
        assert r['functional_score'] == 0.0

    def test_flask_async_routes(self):
        # Real model output: sync Flask routes refactored to async.
        # No DataProcessor — runtime check must find get_data/process coroutines.
        r = run_functional_tests(FLASK_ASYNC_C4, 'C4')
        assert r['functional_score'] == 1.0


# ---------------------------------------------------------------------------
# C5 functional test
# ---------------------------------------------------------------------------

class TestC5:
    def test_opencode_fenced_cache(self):
        r = run_functional_tests(OPENCODE_C5_FENCED, 'C5')
        assert r['functional_score'] == 1.0

    def test_no_ttl_60_fails(self):
        code = """\
class SimpleCache:
    def __init__(self):
        self._cache = {}
    def get(self, k): return self._cache.get(k)
    def set(self, k, v): self._cache[k] = v
"""
        r = run_functional_tests(code, 'C5')
        # Missing TTL=60 → first assert fails
        assert r['asserts_passed'] < r['asserts_total']

    def test_silent_failure_stubs_fail(self):
        r = run_functional_tests(SILENT_FAILURE_STUBS, 'C5')
        assert r['functional_score'] == 0.0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
