# handoff-probe — Methodology
*May 2026*

---

## Principle

The approach is borrowed from electrical engineering signal analysis: inject calibrated signals into the channel and measure what comes out. In EE, a signal chain is characterized by sending known waveforms (impulse, step, ramp, frequency sweep) and observing how the channel transforms them. Here the channel is `orchestrator → CLI → sub-model`, and the signals are calibrated prompts whose expected output is known. The naming follows directly: DIRAC (impulse), STEP, RAMP, SWEEP, CONTRACT (calibrated reference), BATCH (multiplexing).

---

## The 6 signals

| Signal | Injected | Measures |
|--------|----------|----------|
| **DIRAC** | Minimal task, no context | Natural verbosity, format obedience |
| **STEP** | Simple task + noisy project context | Noise rejection |
| **RAMP** | Same task, 5 growing context sizes L1→L5 | Format fidelity vs context length |
| **SWEEP** | Tasks of increasing complexity C1→C5 | Functional completion per level |
| **CONTRACT** | Same C1→C5 tasks + explicit typed interface | Functional gain from pinning the interface |
| **BATCH** | N independent tasks in one invocation | Completion, isolation, token efficiency |

SWEEP and CONTRACT are a matched pair: identical tasks, the only difference is that CONTRACT appends the exact function signature and return type. The delta isolates shape failures (recoverable by specifying the interface) from capability failures (the model can't do the task regardless).

### SWEEP levels

| Level | Task |
|-------|------|
| C1 | Print "hello world" |
| C2 | Write a `reverse_string` function |
| C3 | REST endpoint POST /users/validate with JSON validation |
| C4 | Refactor a full sync module to async/await (2 Flask routes, SQLAlchemy, requests) |
| C5 | Add an in-memory cache layer TTL=60s to a Flask API (4 GET routes) |

### CONTRACT additions

| Level | Added contract |
|-------|----------------|
| C3 | `validate_user_data(data: dict) -> tuple[bool, list[str]]` |
| C4 | exact async signatures the refactored routes must expose |
| C5 | `class SimpleCache: __init__(self, ttl: int = 60)`, `get`/`set` |

---

## L2 functional verification

Generated code is executed in a subprocess with no server, no database.

| Level | Assertions | What is checked |
|-------|-----------|-----------------|
| C1 | 1 | "hello" present in stdout |
| C2 | 3 | `reverse_string("abc") == "cba"`, `("") == ""`, `("a") == "a"` |
| C3 | 2 | `validate_user_data(valid)` → truthy, `validate_user_data(invalid_email)` → falsy |
| C4 | 3 | `async def` present, `await` present, coroutine runs without error |
| C5 | 3 | TTL=60 present (regex), cache class present (regex), cache round-trips (set→get) |

Score per level = `assertions_passed / assertions_total`. Scores across levels are not comparable.

### Known blind spots

| Level | Blind spot | Consequence |
|-------|-----------|-------------|
| C3 | Model validates email but ignores `age < 0` | Score 1.0 with partial validation |
| C4 | `async def f(): await asyncio.sleep(0); return {}` passes runtime check | Score 3/3 with empty refactor |
| C5 | `SimpleCache` class correct but never wired into Flask routes | Score 1.0 with dead cache in production |

C1 and C2 have no blind spots.

---

## Validity guards

Three of five total harness bugs shipped before being caught scored otherwise-working code as 0 — clean, repeatable, invalid measurements that understated capability. All produced exits 0, result files, no error output. (The other two harness bugs — missing C4/C5 mock setup and premature 400-char output truncation, fixed later — corrupted the executed code or data itself rather than misreading correct output; see RESULTS.md provenance.)

| Bug | Reported score | Correct score |
|-----|---------------|---------------|
| C3: harness called `validate(dict)`, not `validate(username, age, email)` | C3 ≈ 0.40 | ≈ 0.60–0.77 |
| opencode invoked without `--dir` → replied "already done", wrote nothing | opencode C3–C5 = 0.00 | C3 ≈ 0.80, C4 ≈ 0.53 |
| C4: `ast.parse` ran on vibe scaffold text, not the code | C4 ≈ 0.04 | ≈ 0.29–0.49 |

Each was caught by the same method: scepticism about a result that didn't match prior knowledge. A level at or near 0 across all runs requires a mechanism. The guards institutionalise that scepticism:

| Guard | Invariant | How enforced |
|-------|-----------|--------------|
| **G1 — invocation symmetry** | every delegation CLI runs through its production wrapper (`vibe-delegate` / `opencode-delegate`) | static scan on every `pytest tools/` |
| **G2 — zero-score classification** | every run scored 0 is labeled why | re-scores stored code with current harness: `HARNESS_BLIND_SUSPECT` if it recovers, else `CODE_NO_PASS` / `SEED_ONLY` / `TIMEOUT` / `NO_TARGET` |
| **G3 — uniform-zero** | a (signal, level) at 0.00 on every run is flagged for review | distribution check across run registry |

Two principles encoded by the guards:
1. **Repeatability ≠ validity.** A run that exits 0 can still measure the wrong thing.
2. **Never trust the stored `functional_score`.** It reflects the harness version at run time. Canonical scores always come from re-scoring stored code with the current harness (`handoff_funcretro.py`).

```bash
python3 tools/handoff_validity.py <run_dir> [<run_dir> ...]   # full audit, exits 1 on FAIL
python3 tools/handoff_validity.py --symmetry-only             # G1 only
```

---

## IPS — channel compliance score

```
IPS = format_fidelity    × 0.25   ← prose added when not requested?
    + noise_rejection    × 0.20   ← contamination by context noise?
    + completion_rate    × 0.25   ← did the model produce non-empty output? (L1 only; not functional correctness)
    + (1 - verbosity_n)  × 0.15   ← excessive verbosity?
    + (bandwidth / 5)    × 0.15   ← degradation on large contexts?
```

| IPS | Recommended use |
|-----|-----------------|
| 0.90+ | All tasks |
| 0.80–0.89 | General use |
| 0.70–0.79 | Simple tasks only |
| < 0.70 | Do not use |

IPS measures channel compliance, not code quality. High IPS + low functional score = silent failure.

---

## BATCH score

```
BATCH_SCORE = task_completion_rate  × 0.40
            + task_isolation_score  × 0.40
            + token_efficiency_score × 0.20
```

| BATCH_SCORE | Interpretation |
|-------------|----------------|
| ≥ 0.95 | Reliable in production |
| 0.80–0.94 | Acceptable — monitor B3 |
| < 0.80 | Prefer sequential calls |

---

## Run protocol

| Parameter | Standard value | Effect |
|-----------|---------------|--------|
| `--runs N` | N=5 (exploratory), N=10 (standard) | Score granularity 0.20 at n=5, 0.10 at n=10 |
| `--clean-workdir` | Always active | Empty tempdir per run — no cross-run contamination |
| `--compare-reference` | For SWEEP/CONTRACT | Activates L2 assertions and functional scores |

At n=5, each level score has granularity 0.20 — one pass/fail = ±0.20. Do not read conclusions from single-run `functional_score` values. Canonical numbers come from `handoff_funcretro.py --valid-only` across all valid runs in the registry.

**Contract C3 variance note:** some models respond to `tuple[bool, list[str]]` by writing `from typing import tuple` (invalid in Python 3.9+). This causes `ImportError` → 0.00 for that run. In aggregate, contract C3 > sweep C3 for all tested models; in a single n=5 run it can go the other way. Not a readout bug.

### Reference commands

```bash
# Full run (n=5, 100 entries)
python3 tools/handoff_probe.py \
  --cli vibe --model mistral-medium-3.5 \
  --signals all --runs 5 --clean-workdir --compare-reference

# Functional pair (the headline diagnostic)
python3 tools/handoff_probe.py \
  --cli vibe --model deepseek-flash \
  --signals sweep,contract --runs 5 --clean-workdir --compare-reference

# Canonical scores
python3 tools/handoff_funcretro.py --valid-only

# Full validity audit
python3 tools/handoff_validity.py ~/.handoff/runs/<run_dir>/
```
