#!/usr/bin/env python3
"""
Unit tests for Signal 5 BATCH — handoff_batch.py + integration points.

Coverage:
  - parse_batch_output: label formats, edge cases, vibe scaffolding stripping
  - score_batch_run: B1/B2/B3, completion, isolation, efficiency, batch_score
  - compute_batch_profile: aggregation, max_reliable_batch_size
  - handoff_signals: BatchSignal registered, get_prompts returns 3 items
  - handoff_probe: _batch_timeout, import smoke-test
  - handoff_retroscore: import smoke-test, batch branch logic
"""
import sys
import os
import unittest


from handoff_batch import (
    parse_batch_output,
    score_batch_run,
    compute_batch_profile,
    BATCH_SIZES,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

VIBE_HEADER = (
    "=== VIBE START ===\n"
    "Workdir : /tmp/probe_xyz\n"
    "Agent   : default\n"
    "===================\n"
)

def with_vibe(text: str) -> str:
    return VIBE_HEADER + text


# ─── parse_batch_output ────────────────────────────────────────────────────────

class TestParseBatchOutput(unittest.TestCase):

    def test_simple_colon_labels(self):
        out = "TASK_1:\ndef add(a, b): return a + b\n\nTASK_2:\ndef reverse(s): return s[::-1]"
        result = parse_batch_output(out, 2)
        self.assertIn(0, result, "TASK_1 should be parsed as index 0")
        self.assertIn(1, result, "TASK_2 should be parsed as index 1")
        self.assertIn('add', result[0])
        self.assertIn('reverse', result[1])

    def test_markdown_bold_labels(self):
        out = "**TASK_1**:\ndef add(a, b): return a + b\n\n**TASK_2**:\ndef reverse(s): return s[::-1]"
        result = parse_batch_output(out, 2)
        self.assertEqual(len(result), 2)

    def test_markdown_heading_labels(self):
        out = "### TASK_1\ndef add(a, b): return a + b\n\n### TASK_2\ndef reverse(s): return s[::-1]"
        result = parse_batch_output(out, 2)
        self.assertEqual(len(result), 2)

    def test_vibe_scaffolding_stripped(self):
        out = with_vibe("TASK_1:\ndef add(a, b): return a + b\n\nTASK_2:\ndef reverse(s): return s[::-1]")
        result = parse_batch_output(out, 2)
        self.assertIn(0, result)
        self.assertIn(1, result)
        # Scaffolding text should NOT appear in content
        self.assertNotIn('VIBE START', result.get(0, ''))

    def test_empty_output_returns_empty(self):
        result = parse_batch_output('', 2)
        self.assertEqual(result, {})

    def test_missing_second_label(self):
        out = "TASK_1:\ndef add(a, b): return a + b\n"
        result = parse_batch_output(out, 2)
        self.assertIn(0, result)
        self.assertNotIn(1, result)

    def test_b2_four_labels(self):
        out = (
            "TASK_1:\ndef add(a, b): return a + b\n"
            "TASK_2:\ndef reverse(s): return s[::-1]\n"
            "TASK_3:\ndef is_palindrome(s): return s == s[::-1]\n"
            "TASK_4:\nflat = lambda lst: [x for sub in lst for x in sub]\n"
        )
        result = parse_batch_output(out, 4)
        self.assertEqual(len(result), 4)
        self.assertIn('palindrome', result[2])

    def test_case_insensitive(self):
        out = "task_1:\ndef add(a, b): return a + b\n\ntask_2:\ndef reverse(s): return s[::-1]"
        result = parse_batch_output(out, 2)
        self.assertIn(0, result)

    def test_labels_beyond_n_ignored(self):
        out = "TASK_1:\ndef add(a, b): return a + b\n\nTASK_2:\ndef reverse(s): return s[::-1]\n\nTASK_3:\nextra stuff\n"
        result = parse_batch_output(out, 2)
        # n_tasks=2 so TASK_3 should be ignored
        self.assertNotIn(2, result)


# ─── score_batch_run ──────────────────────────────────────────────────────────

class TestScoreBatchRun(unittest.TestCase):

    def _perfect_b1(self):
        return "TASK_1:\ndef add(a, b): return a + b\n\nTASK_2:\ndef reverse(s): return s[::-1]"

    def _perfect_b2(self):
        return (
            "TASK_1:\ndef add(a, b):\n    '''Add two numbers.'''\n    return a + b\n"
            "TASK_2:\ndef reverse(s: str) -> str: return s[::-1]\n"
            "TASK_3:\ndef is_palindrome(s): return s == s[::-1]\n"
            "TASK_4:\nflat = lambda lst: [x for sub in lst for x in sub]\n"
        )

    def test_b1_perfect_score(self):
        result = score_batch_run(self._perfect_b1(), level=0)
        self.assertEqual(result['batch_size'], 2)
        self.assertEqual(result['tasks_requested'], 2)
        self.assertEqual(result['tasks_completed'], 2)
        self.assertEqual(result['label_compliance'], 1.0)
        self.assertEqual(result['task_completion_rate'], 1.0)
        self.assertEqual(result['task_isolation_score'], 1.0)
        self.assertGreater(result['batch_score'], 0.8)

    def test_b2_perfect_score(self):
        result = score_batch_run(self._perfect_b2(), level=1)
        self.assertEqual(result['batch_size'], 4)
        self.assertEqual(result['tasks_completed'], 4)
        self.assertEqual(result['task_completion_rate'], 1.0)
        self.assertGreater(result['batch_score'], 0.8)

    def test_empty_output_zero_score(self):
        result = score_batch_run('', level=0)
        self.assertEqual(result['task_completion_rate'], 0.0)
        self.assertEqual(result['tasks_completed'], 0)
        self.assertEqual(result['batch_score'], 0.0)

    def test_partial_completion(self):
        # Only TASK_1 present
        out = "TASK_1:\ndef add(a, b): return a + b\n"
        result = score_batch_run(out, level=0)
        self.assertEqual(result['tasks_completed'], 1)
        self.assertEqual(result['task_completion_rate'], 0.5)
        # label_compliance: only 1 of 2 labels found
        self.assertEqual(result['label_compliance'], 0.5)

    def test_b3_level(self):
        result = score_batch_run('', level=2)
        self.assertEqual(result['batch_size'], 6)
        self.assertEqual(result['tasks_requested'], 6)

    def test_level_out_of_range_clamps_to_0(self):
        result = score_batch_run('TASK_1:\ndef f(): pass', level=99)
        self.assertEqual(result['batch_size'], 2)

    def test_orchestrator_savings_b1(self):
        result = score_batch_run(self._perfect_b1(), level=0)
        # B1: 2 tasks → (2-1) * 3000 = 3000
        self.assertEqual(result['estimated_orchestrator_savings_tokens'], 3000)

    def test_orchestrator_savings_b2(self):
        result = score_batch_run(self._perfect_b2(), level=1)
        # B2: 4 tasks → (4-1) * 3000 = 9000
        self.assertEqual(result['estimated_orchestrator_savings_tokens'], 9000)

    def test_batch_score_formula(self):
        """batch_score = completion*0.40 + isolation*0.40 + efficiency*0.20"""
        result = score_batch_run(self._perfect_b1(), level=0)
        expected = (
            result['task_completion_rate'] * 0.40
            + result['task_isolation_score'] * 0.40
            + max(0.0, min(1.0, 2.0 - result['token_efficiency_ratio'])) * 0.20
        )
        self.assertAlmostEqual(result['batch_score'], round(expected, 3), places=3)

    def test_return_keys_complete(self):
        result = score_batch_run(self._perfect_b1(), level=0)
        expected_keys = {
            'batch_size', 'tasks_requested', 'tasks_completed',
            'label_compliance', 'task_completion_rate', 'task_isolation_score',
            'token_efficiency_ratio', 'estimated_orchestrator_savings_tokens',
            'batch_score',
        }
        self.assertEqual(set(result.keys()), expected_keys)

    def test_vibe_scaffolding_doesnt_break_score(self):
        out = with_vibe(self._perfect_b1())
        result = score_batch_run(out, level=0)
        self.assertEqual(result['tasks_completed'], 2)

    def test_contamination_detection(self):
        # TASK_1 block contains 'palindrome' which is TASK_3's distinctive keyword
        # contaminated = 1, isolation = (2-1)/2 = 0.5 for B1
        out = "TASK_1:\ndef add(a, b): return a + b  # palindrome check\n\nTASK_2:\ndef reverse(s): return s[::-1]"
        result = score_batch_run(out, level=0)
        # task 0 vs task 2 — but task 2 doesn't exist in B1 (n_tasks=2, own_idx check)
        # palindrome is index 2, B1 only has tasks 0 and 1, so other_idx=2 >= n_tasks=2 → skipped
        self.assertEqual(result['task_isolation_score'], 1.0, "Contamination from non-existent task should be ignored")

    def test_contamination_detected_in_b2(self):
        # In B2 (4 tasks), task 0 block containing 'palindrome' (task 2's keyword) = contaminated
        out = (
            "TASK_1:\ndef add(a, b): return a + b  # palindrome trick\n"
            "TASK_2:\ndef reverse(s: str) -> str: return s[::-1]\n"
            "TASK_3:\ndef is_palindrome(s): return s == s[::-1]\n"
            "TASK_4:\nflat = lambda lst: [x for sub in lst for x in sub]\n"
        )
        result = score_batch_run(out, level=1)
        # task_isolation_score < 1.0 due to contamination in task 0
        self.assertLess(result['task_isolation_score'], 1.0)


# ─── compute_batch_profile ────────────────────────────────────────────────────

class TestComputeBatchProfile(unittest.TestCase):

    def _make_run(self, level, completion=1.0, isolation=1.0, efficiency=0.8, score=1.0):
        return {
            'signal': 'batch',
            'level': level,
            'task_completion_rate': completion,
            'task_isolation_score': isolation,
            'token_efficiency_ratio': efficiency,
            'estimated_orchestrator_savings_tokens': (BATCH_SIZES[level] - 1) * 3000,
            'batch_score': score,
        }

    def test_all_levels_present(self):
        runs = [
            self._make_run(0), self._make_run(0),
            self._make_run(1), self._make_run(1),
            self._make_run(2), self._make_run(2),
        ]
        profile = compute_batch_profile(runs)
        self.assertIn('B1', profile['completion_by_size'])
        self.assertIn('B2', profile['completion_by_size'])
        self.assertIn('B3', profile['completion_by_size'])

    def test_max_reliable_b3_when_all_pass(self):
        runs = [
            self._make_run(0, completion=1.0),
            self._make_run(1, completion=1.0),
            self._make_run(2, completion=1.0),
        ]
        profile = compute_batch_profile(runs)
        self.assertEqual(profile['max_reliable_batch_size'], 'B3')

    def test_max_reliable_b1_only(self):
        runs = [
            self._make_run(0, completion=1.0, score=1.0),
            self._make_run(1, completion=0.5, score=0.5),
            self._make_run(2, completion=0.3, score=0.3),
        ]
        profile = compute_batch_profile(runs)
        self.assertEqual(profile['max_reliable_batch_size'], 'B1')

    def test_max_reliable_none_when_all_fail(self):
        runs = [
            self._make_run(0, completion=0.5),
            self._make_run(1, completion=0.2),
        ]
        profile = compute_batch_profile(runs)
        self.assertEqual(profile['max_reliable_batch_size'], 'none')

    def test_empty_runs_returns_defaults(self):
        profile = compute_batch_profile([])
        self.assertEqual(profile['batch_score'], 0.0)
        self.assertEqual(profile['max_reliable_batch_size'], 'none')

    def test_profile_keys(self):
        runs = [self._make_run(0)]
        profile = compute_batch_profile(runs)
        expected_keys = {
            'completion_by_size', 'isolation_by_size', 'max_reliable_batch_size',
            'token_efficiency_ratio', 'estimated_orchestrator_savings_tokens', 'batch_score',
        }
        self.assertEqual(set(profile.keys()), expected_keys)

    def test_averaging_over_multiple_runs(self):
        runs = [
            self._make_run(0, completion=1.0, score=1.0),
            self._make_run(0, completion=0.5, score=0.5),
        ]
        profile = compute_batch_profile(runs)
        self.assertAlmostEqual(profile['completion_by_size']['B1'], 0.75, places=2)


# ─── Integration: handoff_signals ────────────────────────────────────────────

class TestHandoffSignals(unittest.TestCase):

    def test_batch_signal_registered(self):
        from handoff_signals import SIGNALS, get_signal
        names = [s.name for s in SIGNALS]
        self.assertIn('batch', names)

    def test_get_signal_batch(self):
        from handoff_signals import get_signal
        sig = get_signal('batch')
        self.assertEqual(sig.name, 'batch')

    def test_batch_prompts_count(self):
        from handoff_signals import get_signal
        sig = get_signal('batch')
        prompts = sig.get_prompts()
        self.assertEqual(len(prompts), 3, "Should have B1, B2, B3 prompts")

    def test_batch_prompts_contain_labels(self):
        from handoff_signals import get_signal
        sig = get_signal('batch')
        prompts = sig.get_prompts()
        self.assertIn('TASK_1', prompts[0])
        self.assertIn('TASK_2', prompts[0])
        self.assertIn('TASK_4', prompts[1])
        self.assertIn('TASK_6', prompts[2])

    def test_all_signals_present(self):
        from handoff_signals import SIGNALS
        names = [s.name for s in SIGNALS]
        self.assertEqual(names, ['dirac', 'step', 'ramp', 'sweep', 'contract', 'batch'])


# ─── Integration: handoff_probe imports + _batch_timeout ─────────────────────

class TestHandoffProbe(unittest.TestCase):

    def test_probe_imports_without_error(self):
        """Smoke-test: probe module loads cleanly with all new imports."""
        import importlib
        import handoff_probe
        importlib.reload(handoff_probe)  # force re-import to catch stale bytecode

    def test_batch_timeout_b1(self):
        from handoff_probe import _batch_timeout
        self.assertEqual(_batch_timeout(0), 60, "B1 (level 0) should be 60s")

    def test_batch_timeout_b2(self):
        from handoff_probe import _batch_timeout
        self.assertEqual(_batch_timeout(1), 60, "B2 (level 1) should be 60s")

    def test_batch_timeout_b3(self):
        from handoff_probe import _batch_timeout
        self.assertEqual(_batch_timeout(2), 120, "B3 (level 2) should be 120s")

    def test_run_single_accepts_timeout_override(self):
        """_run_single signature must accept timeout_override kwarg."""
        import inspect
        from handoff_probe import _run_single
        sig = inspect.signature(_run_single)
        self.assertIn('timeout_override', sig.parameters)


# ─── Integration: handoff_retroscore ─────────────────────────────────────────

class TestHandoffRetroscore(unittest.TestCase):

    def test_retroscore_imports_without_error(self):
        import importlib
        import handoff_retroscore
        importlib.reload(handoff_retroscore)

    def test_retroscore_batch_branch(self):
        """retroscore_file must call score_batch_run for batch entries."""
        import json
        import tempfile
        import os

        # Create a minimal raw.jsonl with one batch entry
        entry = {
            'signal': 'batch',
            'level': 0,
            'output': 'TASK_1:\ndef add(a, b): return a + b\n\nTASK_2:\ndef reverse(s): return s[::-1]',
            'workdir_snippet': '',
            'behavioral_score': None,
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            f.write(json.dumps(entry) + '\n')
            tmp_path = f.name

        try:
            from handoff_retroscore import retroscore_file
            stats = retroscore_file(tmp_path)
            # Read back and verify batch_score was set
            with open(tmp_path) as f:
                result = json.loads(f.read().strip())
            self.assertIn('batch_score', result)
            self.assertGreater(result['batch_score'], 0.0)
            self.assertIn('task_completion_rate', result)
        finally:
            os.unlink(tmp_path)

    def test_retroscore_non_batch_uses_behavioral_check(self):
        """retroscore_file must call behavioral_check for non-batch signals."""
        import json
        import tempfile
        import os

        entry = {
            'signal': 'dirac',
            'level': 0,
            'output': 'def add(a, b): return a + b',
            'workdir_snippet': '',
            'behavioral_score': None,
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            f.write(json.dumps(entry) + '\n')
            tmp_path = f.name

        try:
            from handoff_retroscore import retroscore_file
            retroscore_file(tmp_path)
            with open(tmp_path) as f:
                result = json.loads(f.read().strip())
            # behavioral_score should be set (1.0 for valid Python function)
            self.assertIn('behavioral_score', result)
            self.assertIsNotNone(result['behavioral_score'])
            # batch_score should NOT be set
            self.assertNotIn('batch_score', result)
        finally:
            os.unlink(tmp_path)

    def test_retroscore_workdir_snippet_passed(self):
        """workdir_snippet must be used for behavioral scoring."""
        import json
        import tempfile
        import os

        # sweep C3 where the code is in workdir_snippet, not output
        entry = {
            'signal': 'sweep',
            'level': 2,
            'output': 'Here is the endpoint:',
            'workdir_snippet': '# FILE: app.py\n@app.route("/users/validate")\nasync def validate():  pass',
            'behavioral_score': 0,
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            f.write(json.dumps(entry) + '\n')
            tmp_path = f.name

        try:
            from handoff_retroscore import retroscore_file
            retroscore_file(tmp_path)
            with open(tmp_path) as f:
                result = json.loads(f.read().strip())
            # Score should be > 0 because workdir_snippet has the route
            self.assertGreater(result['behavioral_score'], 0.0,
                               "workdir_snippet content should improve score vs text-only")
        finally:
            os.unlink(tmp_path)


if __name__ == '__main__':
    unittest.main(verbosity=2)
