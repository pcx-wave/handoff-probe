# handoff-probe

**You delegate coding tasks to a cheaper sub-model. How do you know it actually works?**

Not "did it produce code" — did the code *run*. At what complexity does it break, and does adding a typed interface recover it.

handoff-probe measures this empirically. Three measurement layers:

| Layer | File | Question |
|-------|------|----------|
| L1 — IPS | `handoff_metrics.py` | Did the instruction cross the channel cleanly? (format, noise, context) |
| L2 — behavioral | `handoff_behavioral.py` | Does the code *look* right? (regex/AST) |
| L2 — functional | `handoff_functests.py` | Does the code *work*? (executes it, runs assertions) |

The gap between behavioral and functional is the headline finding. Mistral at C4: IPS 0.850, behavioral 1.00 (`async def` present), functional 0.49 (often incomplete). Structural checks overstate correctness. All reported results use the functional layer.

---

## The 6 signals

| Signal | What it measures |
|--------|------------------|
| **DIRAC** | Natural verbosity, format obedience on a minimal task |
| **STEP** | Noise rejection when the prompt contains irrelevant context |
| **RAMP** | Format fidelity across growing context sizes (L1→L5) |
| **SWEEP** | Functional completion across task complexity (C1→C5) |
| **CONTRACT** | Functional gain when the prompt carries an explicit typed interface (same C1→C5 tasks) |
| **BATCH** | Completion and task isolation over N simultaneous tasks (B1=2, B2=4, B3=6) |

SWEEP and CONTRACT are a matched pair — same tasks, the only difference is that CONTRACT appends the function signature and return type. The gap between them is shape failures (recoverable by specifying the interface) vs capability failures (the model can't do the task regardless).

---

## Installation

```bash
pip install pyyaml   # only external dependency; Python 3.10+ stdlib otherwise
```

---

## Usage

```bash
# Full run, all signals (n=5, 100 entries)
python3 tools/handoff_probe.py \
  --cli vibe --model mistral-medium-3.5 \
  --signals all --runs 5 --clean-workdir --compare-reference

# The headline functional diagnostic
python3 tools/handoff_probe.py \
  --cli vibe --model deepseek-flash \
  --signals sweep,contract --runs 5 --clean-workdir --compare-reference

# Via opencode
python3 tools/handoff_probe.py \
  --cli opencode --model deepseek-v4-flash-free \
  --opencode-model opencode/deepseek-v4-flash-free \
  --signals sweep,contract --runs 5 --clean-workdir --compare-reference

# Gemini (free tier 20 req/day → use --runs 1)
python3 tools/handoff_probe.py --cli gemini --model gemini --runs 1

# Generate the YAML profile
python3 tools/handoff_report.py --profile my-model ~/.handoff/runs/<timestamp>_<model>/

# Unit tests
python3 -m pytest tools/
```

---

## Results

Full results (functional SWEEP, CONTRACT recovery, IPS, BATCH) → **[RESULTS.md](RESULTS.md)**

Short version:
- IPS is identical across models (0.850) — channel compliance says nothing about functional correctness.
- Fidelity degrades with complexity: C1/C2 ~0.87–1.0 → C3 ~0.58–0.77 → C4 (async) ~0.58–0.59 → C5 ~0.69–0.71.
- Adding a **typed interface contract** recovers most of C3–C4: mistral C4 0.58→0.87, deepseek C4 0.59→0.84. Prompt engineering already tells you to specify the interface — what's new is the measured payoff that justifies making it a standing rule.
- vibe and opencode with the same model produce similar scores when invoked correctly. The harness hit **three** bugs that scored working code as 0 — the numbers are lower bounds.

Token economics (two meters, per-step costs, why delegation lets you do more per session) → **[ECONOMICS.md](ECONOMICS.md)**

---

## Gotchas

**`--clean-workdir`** — without it, models see the handoff-probe repo and edit the wrong files. Always active for measurements.

**Adaptive timeouts** — C4/C5 need more time than C1–C3. `exit=124` = timeout → `completion_rate=0.0`.

**vibe vs opencode capture** — vibe writes code to disk; opencode often emits to stdout. `_best_source()` picks the right one per CLI.

**Free-tier models skipping file writes on simple tasks** — on trivial tasks some models reply inline rather than writing files. The harness falls back to the seed stub and scores 0. Not a capability failure — a measurement artefact. Fix: enforce file writes in the delegation prompt if your orchestrator reads from disk.

**gemini quota** — free tier: 20 req/day. A full n=5 run is ~100 req. Use `--runs 1` or a signal subset.

---

## Source files

| File | Role |
|------|------|
| `handoff_probe.py` | Main CLI — run orchestration |
| `handoff_signals.py` | 6 signal definitions (prompts), incl. SWEEP + CONTRACT |
| `handoff_metrics.py` | L1 metrics (IPS) |
| `handoff_functests.py` | L2 functional harness — executes code, runs assertions |
| `handoff_behavioral.py` | L2 structural checks (regex/AST) |
| `handoff_batch.py` | BATCH scoring |
| `handoff_report.py` | YAML profile generation + comparison |
| `handoff_funcretro.py` / `handoff_retroscore.py` | Retroactive scoring on stored runs |
| `handoff_tokens.py` | Per-step token accounting of the delegation loop |
| `handoff_validity.py` | Validity guards — invocation symmetry, zero-score classification, uniform-zero detection |
| `test_handoff_functests.py`, `test_handoff_batch.py`, `test_handoff_validity.py` | Unit tests |

See [METHODOLOGY.md](METHODOLOGY.md) for signal definitions, validity guards, and run protocol.
See [CHANGELOG.md](CHANGELOG.md) for the probe's evolution and corrected measurements.
