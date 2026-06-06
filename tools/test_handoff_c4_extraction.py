"""Regression tests for the C4 reading bug: a correct async refactor wrapped in
the vibe console scaffold / markdown fences / truncated mid-statement must still
get structural credit, instead of scoring 0 because ast.parse choked on the
scaffold header."""
from handoff_functests import run_test_c4


def test_c4_async_inside_vibe_scaffold_scores():
    # Real shape: vibe scaffold header + fenced async code (as captured on disk).
    src = (
        "=== VIBE START ===\n"
        "Workdir : /tmp/handoff_probe_x\nAgent   : default\nModel   : m\n"
        "  [vibe] Refactored the routes to async:\n"
        "```python\n"
        "async def get_data():\n"
        "    result = await fetch()\n"
        "    return result\n"
        "```\n"
        "Tool calls: 2\n"
    )
    passed, total = run_test_c4(src)
    assert total == 3
    assert passed >= 2, f"async def + await present but only scored {passed}/3"


def test_c4_truncated_async_still_gets_structural_credit():
    # Output cut mid-statement at the capture cap — ast.parse fails, regex must catch it.
    src = ("=== VIBE START ===\n  [vibe]\n```python\n"
           "async def get_data():\n    async with AsyncSession(db) as s:\n        \n"
           "Tool calls: 2")
    passed, _ = run_test_c4(src)
    assert passed >= 1, "truncated-but-present async should still get structural credit"


def test_c4_no_async_still_scores_zero():
    # Guard against over-crediting: plain sync code must not pass.
    src = "def get_data():\n    return fetch()\n"
    passed, _ = run_test_c4(src)
    assert passed == 0, f"sync code wrongly credited {passed}/3"
