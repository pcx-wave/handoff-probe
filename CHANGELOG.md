# Probe evolution history

---

## v1.8 — June 2026

### Fix — `_print_h_loss_summary` mixed sweep and contract into a single row (analysis bug)

`_print_h_loss_summary` (printed at end of every `--compare-reference` run) collected all functional scores into a single `scores_by_task` dict keyed only by `task_id`, discarding the `signal` field. When a run included both `--signals sweep,contract`, each task got 10 entries (5 sweep + 5 contract) averaged together into one number. There was no way to see sweep vs contract from the live summary; the output looked like a single mystery score.

This masked real patterns: if contract C3 had one failure while sweep C3 was clean, the mixed average showed C3=0.90 with no indication of which signal caused it. Users comparing the live summary to the canonical funcretro table would see different numbers from the same run.

Fixed: the function now groups by `(signal, task_id)`, prints one row per signal, and appends `contract vs sweep delta: +X.XXX` when both signals are present. Example output:

```
==============================================================
  H_loss report — mistral-medium-3.5
  Signal        C1     C2     C3     C4     C5   H_loss
==============================================================
  contract    1.00   1.00   0.80   1.00   0.93   0.947
  sweep       1.00   1.00   1.00   0.80   0.73   0.907
==============================================================
  contract vs sweep delta: +0.040
```

### Fix — run registry not rebuilt after new runs

`handoff_runregistry.py` was never called automatically; the registry at `~/.handoff/run_registry.json` would go stale the moment a new run finished. Running `handoff_funcretro.py` without `--valid-only` then pulled in incomplete, crashed, or zero-scoring runs from invalid models alongside the canonical ones, producing alarming-looking rows in the funcretro table (models scoring all-zeros at every level). These were not measurement failures; they were unregistered invalid runs contaminating an otherwise clean analysis.

Fixed: `handoff_probe.py` now silently calls `handoff_runregistry.py` at the end of every run (in a `try/except` with a 30s timeout so it never blocks or crashes the probe). `overnight_rerun.sh` gets an explicit rebuild step after all models complete, logged to the summary file.

**Rule encoded in tools:** after any run, the registry is current. `--valid-only` is always usable without a manual rebuild step.

### Diagnostic — contract C3 can score below sweep C3 on individual runs (not a readout bug)

Observed in June 1 runs: mistral and deepseek both showed sweep C3 = 1.00 vs contract C3 = 0.80 (one failure out of five runs each). Root causes:

- **Mistral failure**: the contract C3 prompt specifies `validate_user_data(data: dict) -> tuple[bool, list[str]]`. The model wrote `from typing import tuple` to satisfy the type annotation — invalid, since `tuple` is a built-in in Python 3.9+ and not exported from `typing`. This raises `ImportError` at test-harness execution, scoring 0. The sweep prompt has no type hint, so the model never writes this import.
- **Deepseek failure**: vibe agent produced only seed content (no `validate_user_data`, no `/users/validate` route). The agent ran out of turns or hit a timeout before writing anything. Stored score 0 is correct.

**Neither is a readout bug.** The contract C3 prompt is more prescriptive (two specific deliverables + an exact signature), which introduces one more way to fail per run. Across the full aggregate (`handoff_funcretro.py --valid-only`), contract C3 remains higher than sweep C3 for every model. Single-run variance at n=5 produces swings of ±0.20 per level (granularity 1/5); this is expected and documented in METHODOLOGY.

**Implication for contract prompt design:** the `tuple[bool, list[str]]` annotation in the contract C3 prompt reliably provokes a bad import from some models. This is tolerated at n=5 (one failure in five is within normal variance) but worth fixing before increasing n, to avoid systematic under-scoring.

---

## v1.7 — May 2026

### Bug fix — C4 scored working async refactors as 0 (third under-measurement bug)
`run_test_c4` ran `ast.parse` on the **raw** best-source. For vibe, that source is the console scaffold (`=== VIBE START === / Workdir: / Agent:`) with the real code fenced in a ```` ```python ```` block and often truncated mid-statement at the capture cap. `ast.parse` choked on the scaffold header → `SyntaxError` → **both** structural asserts (async def, await) failed at once → 0/3, even when a correct async refactor was present. Confirmed on real runs (`'async def' in src` was True while the score was 0).

Fixed: C4 now `extract_code()`s the source first (unwrap scaffold + fences, like C3), and falls back to regex (`async\s+def\s+\w+\(`, `\bawait\s+\w`) for the structural asserts when `ast.parse` fails on truncated fragments — without over-crediting (plain sync code still scores 0; 3 regression tests in `test_handoff_c4_extraction.py`).

**Impact:** vibe sweep C4 rose from a spurious **0.04 to 0.29–0.49**, and contract C4 to **0.67–0.80**. The "C4 collapse" and "opencode is 10× better at C4" headlines were both artifacts of this bug — with C4 measured correctly, fidelity **degrades gracefully** with complexity (no cliff) and the vibe/opencode channels are comparable. This was the **third** harness reading-bug (after C3 signature and opencode `--dir`) that scored working code as 0; the reported numbers are treated as lower bounds.

---

## v1.6 — May 2026

### Validity guards (`handoff_validity.py`) — make invalid-but-clean runs loud
Encodes the methodology invariants that have no local error signal (a run can exit 0 and still measure the wrong thing). Each guard turns "is this valid?" into a check with an oracle:

- **G1 invocation symmetry** — static check that every delegation CLI goes through its production wrapper (`*_DELEGATE`), never a bare CLI call. Would have caught the asymmetric-opencode bug immediately. Enforced on every `pytest tools/`.
- **G2 zero-score classification** — for every C1–C5 run scored 0, says *why*, using the **harness itself as oracle**: a zero is `HARNESS_BLIND_SUSPECT` only if re-scoring the stored code with the current harness recovers >0 (so prose that merely mentions the target, or non-runnable fragments, are correctly `CODE_NO_PASS`, not false alarms). Also detects `SEED_ONLY` (model wrote nothing beyond the seed — the C1/C2 artifact), `TIMEOUT`, `NO_TARGET`.
- **G3 uniform-zero detector** — a (signal, level) that is 0.00 across all runs is flagged for review as a likely setup/invocation artifact rather than capability.

Proven on real data: 39 HARNESS_BLIND_SUSPECT on a pre-fix vibe run (the C3-signature bug + stale stored scores), `CODE_NO_PASS` (not false-blind) on the invalid opencode batch's prose responses, `SEED_ONLY` on the C1/C2 artifact. The probe now self-reports these as ⚠ VALIDITY warnings at the end of every `--compare-reference` run. 9 new unit tests (84 total).

---

## v1.5 — May 2026

### Bug fix — C3 harness was blind to valid solutions with a different signature
The C3 functional check called the validation function only as `_fn(data_dict)` (with a no-arg `_fn()` fallback). A model that wrote a perfectly correct `validate_user_data(username, age, email)` — three positional args instead of a dict — raised `TypeError`, fell through, and scored **0/2**. `_truthy` also mis-read a bare errors-list return (the common `return errors` convention, where empty = valid) as invalid. Both bugs sank functionally-correct code purely for structural choices.

Fixed: `_call_any()` now tries `_fn(dict)`, `_fn(*positional-by-name)`, `_fn(**filtered-kwargs)`, and `_fn()` (route-style), using `inspect.signature`; `_truthy` now interprets bool / `(bool, errors)` / `{is_valid|valid|errors}` / bare errors-list. Verified: 8/8 previously-zero vibe C3 solutions with real on-disk code now score correctly; 75/75 unit tests pass.

**Impact on results:** vibe sweep C3 rose (e.g. deepseek 0.63 → 0.77), and the apparent "contract recovers C3" effect **shrank** — for deepseek it nearly vanished (+0.30 → +0.03). Much of the original contract-C3 gain was a measurement artifact: the contract forced the one signature the harness could call. The real cliff is **C4 (async)**, not C3.

### Coherence — always re-score with the current harness
`functional_score` stored in each run's `raw.jsonl` reflects whatever harness version existed that session, so stored values across sessions are not comparable. Canonical scores come from re-scoring stored code with the current harness (`handoff_funcretro.py`); reported tables are always re-scored, never read from stored fields.

### Major methodology fix — opencode was invoked asymmetrically (results invalid)
The probe delegated **vibe** through its production wrapper (`vibe-delegate`) but **opencode** through a hand-rolled `opencode run --format json --model X <prompt>` that omitted both `--dir <workdir>` and `--dangerously-skip-permissions` that the working `opencode-delegate` wrapper uses. Without `--dir`, opencode does not treat the seeded folder as the project and does not read the existing files — so on edit tasks (C3+) it trusts the prompt's CONTEXT ("Files: app.py, utils.py…") and replies "already implemented, no changes needed," writing nothing → spurious 0. (Controlled test showed `--dangerously-skip-permissions` alone does not block simple file writes, so `--dir`/project-context is the more likely driver; a clean live re-test was blocked by free-tier latency.) The first opencode batch is therefore **not a valid measurement** and is discarded.

Fix: the probe now routes opencode through the vendored `tools/opencode-delegate` wrapper (env override `OPENCODE_DELEGATE`), symmetric with the `vibe-delegate` path — both channels are now measured exactly as the skills use them. Opencode re-run pending clean free-tier availability.

### Known confound — seeded-workdir invites "already done"
The SWEEP/CONTRACT context seeds stub files and names files that don't exist (e.g. "utils.py (helpers)"). Independent of the invocation bug above, this can still nudge agents toward "already implemented." Flagged for a future setup revision (describe only files actually seeded).

---

## v1.4 — May 2026

### Signal 6 — CONTRACT added
A matched twin of SWEEP: identical C1–C5 tasks, but each prompt carries an explicit typed interface contract (exact function signature + return type). Comparing CONTRACT against plain SWEEP isolates how much of a functional failure is a *shape* problem (the model skipped a branch / returned the wrong type — recoverable by specifying the interface) versus a *capability* problem (the async refactor, which stays broken either way). Finding: the contract moves the first-failure boundary from C3 to C4 for every model tested.

### Bug fix — C5 silent pass
The C5 functional check ended with `if not _tested: print("PASS: cache_runs_ok")`, so the third assertion passed **unconditionally** whenever no cache was detected — inflating every C5 to 1.00 even when the model produced no cache at all. Rewritten to actually round-trip a cache: try known class names, then any class exposing `get`/`set` (with `ttl=60` then no-arg), then a `functools.lru_cache` fallback. No unconditional pass. After the fix, C5 settled at its real ~0.67 across models (2 of 3 assertions).

### Repo made self-contained
Source moved from `~/tools/` into `tools/` in the repo; removed the hardcoded `sys.path.insert('/home/pcx-pi/tools')` from all modules; `vibe-delegate` path and `--workdir` now resolve relative to the script / cwd (overridable via `VIBE_DELEGATE`). `tools/conftest.py` added so `pytest tools/` runs standalone.

### Functional vs behavioral separated
Two distinct L2 scorers now coexist and are reported separately: `handoff_behavioral.py` (structural regex/AST — "does the code look right") and `handoff_functests.py` (executes the code — "does it work"). The headline RESULTS table is now the **functional** one; the gap between the two layers (e.g. C4 behavioral 1.00 vs functional 0.13) is the project's central silent-failure finding.

---

## v1.3 — May 2026

### Bug fixes

**1. Truncated output (`output[:500]`)**
The vibe scaffolding (header + tool logs + footer) takes ~220 chars. With a 500-char limit, only ~280 chars remained for the actual code — all C3/C4/C5 output was systematically truncated. The model may have produced correct code, but we were not reading it.
Fixed → `output[:4000]`.

**2. Truncated workdir snippet (`workdir_snippet[:300]`)**
Flask routes added by models appear at the end of files. 300 chars did not capture C3/C5 additions. The harness was reading incomplete files.
Fixed → `workdir_snippet[:3000]`.

**3. Retroscoring without `workdir_content`**
`behavioral_check()` was called without passing `workdir_content=`. For agent channels (vibe), code is written to files on disk, not in the text response. Without this parameter, the harness never saw the created files → L2=0 systematic for all vibe C3+ runs.
Fixed → `workdir_content` collected before tempdir cleanup and passed explicitly.

### Signal 5 — BATCH added
Measures ability to handle N independent tasks in a single invocation: completion, task isolation, token efficiency. Three levels: B1 (2 tasks), B2 (4 tasks), B3 (6 tasks).

---

## v1.2 — May 2026

### L2 behavioral verification added
Before v1.2, the probe only measured whether output *existed* and matched the format (L1). v1.2 adds an assertion harness that executes the generated code and verifies it does what was asked.

`handoff_behavioral.py`: checks per SWEEP level (C1 stdout, C2 function call, C3 Flask route, C4 async, C5 cache TTL).

Retroactive scoring added (`handoff_retroscore.py`) to recompute L2 scores on existing runs.

### Fix C3 — backtick routes
The Flask route detection regex did not match routes inside markdown code fences (` ```python `). Fixed to search in both output and `workdir_content`.

---

## v1.1 — May 2026

### Completion criterion fix
`min_lines=3` replaced by `min_chars=10`: some models produced 3 lines of empty prose that counted as completion.

### Adaptive timeouts for C4/C5
C4 and C5 require more time than C1–C3 (multi-file refactor, full layer generation). Dedicated timeouts added: C4/C5 → 90–180s vs 45–75s for C1–C3.

---

## v1.0 — May 2026

Initial probe: 4 signals (DIRAC, STEP, RAMP, SWEEP), composite IPS score. L1 verification only (format + structural completion).
