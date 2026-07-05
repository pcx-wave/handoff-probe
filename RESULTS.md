# handoff-probe — Results
*May 2026 · CLI: vibe · functional layer (code executed against assertions)*

**Provenance.** All scores are retro-computed by `handoff_funcretro.py --valid-only` on the 37 runs classified VALID by `handoff_runregistry.py` (May 29–June 2026). Pre-May-29 runs are excluded: five harness bugs (C3 wrong signature, opencode missing `--dir`, C4 ast.parse on scaffold, C4/C5 missing mock setup, output truncated at 500 chars) made every earlier measurement invalid. The registry and validity guards are encoded in `tools/handoff_validity.py` and `tools/handoff_runregistry.py`.

**Reference baseline.** claude-direct (orchestrator solving the task itself, no delegation) = 1.00 on every level by construction.

---

## L1 — Channel compliance (IPS)

| Model | IPS | Avg latency |
|-------|-----|-------------|
| mistral-medium-3.5 / deepseek-flash | **0.850** each | 22–24 s |

IPS is identical across models. It measures whether the instruction crossed the channel cleanly (format, noise, context) — not whether the code works. All functional differentiation is at L2.

---

## L2 — Functional completion (vibe)

Per-level sample sizes after pooling across all valid runs:

| Model | n sweep (C1–C5) | n contract (C1–C5) |
|---|---|---|
| mistral-medium-3.5 | 29–37 | 33 |
| deepseek-flash | 30–35 | 40–45 |

Score = fraction of functional assertions passed at each level. H_loss = mean across C1–C5. Note: scores across levels are not directly comparable — C1 has 1 assertion, C4 has 3, and the blind spots differ — so H_loss is an aggregate of incommensurable quantities. It is useful for gross comparisons (is this model above 0.70 overall?) but not for fine-grained model ranking. SFR (Silent Failure Risk) threshold: H_loss < 0.70.

### SWEEP — plain prompt

| Model | C1 | C2 | C3 | C4 | C5 | H_loss |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| claude-direct (ref) | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.000 |
| mistral-medium-3.5 | 1.00 | 1.00 | 0.77 | 0.58 | 0.69 | 0.809 |
| deepseek-flash | 0.97 | 0.87 | 0.58 | 0.59 | 0.71 | 0.744 |

### CONTRACT — same tasks + explicit typed interface

| Model | C1 | C2 | C3 | C4 | C5 | H_loss |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| mistral-medium-3.5 | 1.00 | 1.00 | 0.88 | 0.87 | 0.81 | 0.911 |
| deepseek-flash | 1.00 | 0.97 | 0.78 | 0.84 | 0.79 | 0.877 |

---

## CONTRACT lift — isolating shape failure

CONTRACT and SWEEP are a matched pair: identical tasks, the only difference is an explicit function signature appended to the prompt. The lift CONTRACT − SWEEP measures how much functional loss is attributable to *shape failure* (the model implementing the wrong interface) rather than *capability failure* (the model unable to complete the task regardless).

| Model | SWEEP H_loss | CONTRACT H_loss | Lift | Clears SFR? |
|---|:---:|:---:|:---:|:---:|
| mistral-medium-3.5 | 0.809 | 0.911 | **+10%** | yes |
| deepseek-flash | 0.744 | 0.877 | **+13%** | yes |

Per-level lift:

| Level | Mistral | Deepseek | Interpretation |
|---|:---:|:---:|---|
| C3 | +0.11 | +0.20 | Partial recovery; some shape ambiguity, some capability gap |
| C4 | **+0.29** | **+0.25** | Largest gain; async refactor benefits most from pinned signatures |
| C5 | +0.12 | +0.08 | Marginal; remaining gap is capability, not interface |

Pinning the interface recovers most of the C3–C4 degradation. The residual gap to 1.000 (0.09–0.12 H_loss) is not recovered by a typed interface contract alone — other interventions were not tested.

---

## Degradation profile

C3 is the first inflection point across all models. Under SWEEP, mistral declines monotonically through C4 (C3=0.77, C4=0.58) then partially recovers at C5 (0.69). Deepseek shows degradation starting at C2 (0.87), flattens from C3 to C4 (0.58→0.59), then recovers at C5 (0.71). Neither model follows a clean monotone decline: the per-level pattern appears task-specific as much as complexity-driven. CONTRACT interrupts the C3–C4 decline for both models; shape failure dominates in the plain-prompt condition at those levels.

---

## Limits

**Precision.** See 'Reading the scores' below.

**Lower bounds.** The functional harness has known blind spots: C3 accepts partial validation logic, C4 accepts `async def f(): await asyncio.sleep(0)` as a valid coroutine, C5 does not verify that the cache is wired into the Flask routes. Real fidelity may be lower than reported.

---

## Context provisioning: push vs. pull (exploratory pilot — C3 only)

*n=5–6 per arm, mistral-medium-3.5 via vibe, `tools/handoff_routing_experiment.py`. This is a small exploratory pilot outside the retroscored 37-run canonical set above — read it directionally, not as a ranking. Replicate at n≥10 before trusting the magnitudes.*

CONTRACT (above) and a second, lighter intervention — **context-pointing** — are both forms of context engineering, but they supply the missing shape information through opposite mechanisms:

- **CONTRACT = push.** The orchestrator pre-designs the interface (signature, types, schema) and writes it directly into the prompt. The model is handed the answer; it doesn't need to look anything up.
- **Context-pointing = pull.** The orchestrator does no design work — it just names the relevant existing files and tells the delegate to read them first ("before writing, read the existing `app.py`/`utils.py` and follow their conventions"). The model retrieves the shape information itself.

The pilot appended the context-pointing sentence to each of the two existing C3 phrasings (sweep = implicit instruction, contract = already-explicit interface) independently and measured both functional score and actual delegate-side token cost (pulled from `~/.local/share/delegate-runs.jsonl`):

| Baseline phrasing | + context-pointing | n | avg functional score | Δ score | avg tokens_total | Δ tokens |
|---|---|:---:|:---:|:---:|:---:|:---:|
| sweep (implicit) | yes | 6 | 1.00 (vs 0.83) | **+0.17** | 59,818 (vs 62,275) | **−3.9%** — every context-pointed run used fewer tokens than every baseline run (ranges 59198–60408 vs 61203–63287, no overlap across 5 vs 6 trials) |
| contract (explicit/typed) | yes | 6 | 0.83 (vs 1.00) | −0.17 | 62,608 (vs 62,030) | +0.9% — ranges overlap heavily; consistent with no real effect |

**Reading.** When the prompt is already explicit (CONTRACT has pushed the shape information), telling the model to also go pull more is redundant retrieval — it can only add overhead, and the data is consistent with that (flat-to-slightly-negative, no real cost difference, single-run-sized delta). When the prompt is implicit (SWEEP baseline), pointing the model at where the conventions live fills the same gap CONTRACT would have pre-filled by pushing — and does it *more cheaply*: fewer tool calls (5.0 vs 7.0 avg), fewer tokens, lower cost (~$0.092 vs ~$0.106/run), with a clean separation between the two token distributions, not just a shifted average.

**Implication — push and pull look like substitutes, not complements, at C3.** They compete to resolve the same uncertainty (does the model know the shape/conventions it needs?) through different channels; stacking them buys nothing and may cost a little. That suggests a rule for matching mechanism to what the orchestrator actually knows in advance:

- **You know the exact interface the delegate must produce → push it (CONTRACT).** Higher design cost, higher certainty. Your own degradation profile already tells you where this cost is worth paying — C3+ tasks, where the per-level lift table above shows shape failure dominating (C4 lift +0.25–0.29).
- **You don't know the exact shape, but you know where the relevant conventions live → pull (context-pointing).** Near-zero design cost (it's generic — "read X, follow its style" applies to almost any task), and in this pilot it was also the cheaper *execution*: a rare case where the same intervention improved the outcome and lowered the bill.

---

## What the data tells you

Three findings generalise beyond the models tested here.

**1. Add the typed contract at C3+, regardless of model.**
The SWEEP→CONTRACT lift is consistent across both measured models (+10% to +13%) and largest at C4. The direction does not depend on which model you use — it depends on how much interface ambiguity the plain prompt leaves open. For any task at C3 or above, appending the function signature and return type is the single highest-leverage prompt change available. It recovers shape failures; it does not substitute for capability.

**2. Execution mode must match the model before comparing scores.**
devstral-small was tested via vibe (chat-mode inline response). devstral is an agent-mode model designed to edit files in a workdir. Validity audits show systematic `NO_TARGET` (14–34 entries per run) and `HARNESS_BLIND_SUSPECT` failures — the harness cannot reliably parse devstral's output in chat mode. devstral scores are excluded from the result tables; a valid measurement requires opencode + workdir runs.

**3. A clean exit is not a success signal.**
`exit 0` with nothing written is the failure signature. At n=5 granularity it is not visible in aggregate scores, but the validity guards (G2/G3) surface it at the run level. Any delegation harness needs an explicit check — `tokens_out > threshold` for vibe, `files_changed > 0` for opencode — before treating the result as usable. Without it, silent failures are indistinguishable from successes in the logs.

---

## Reading the scores

**Precision.** Worst-case 95% CI half-width: n=29 → ±0.18; n=45 → ±0.15. Resolves large effects (contract lift, C3 inflection, SFR boundary) but not model-vs-model differences below ~0.15.

**Per-level blind spots** — a score of 1.00 does not mean the code is production-correct:

| Level | Blind spot |
|-------|-----------|
| C1, C2 | None — assertions check exact values |
| C3 | Validates email; may skip age/username checks |
| C4 | `async def f(): await asyncio.sleep(0)` passes; no real async IO required |
| C5 | `SimpleCache` class correct but never wired into Flask routes scores 1.0 |

**Routing thresholds** (what supervision level is appropriate):

| Score | Routing implication |
|-------|---------------------|
| C1/C2 0.87–1.00 | delegate freely |
| C3 0.78–0.88 (contract) | delegate with typed interface |
| C3 0.58–0.77 (plain) | review output for missing branches |
| C4 0.84–0.87 (contract) | delegate with full async signatures; expect review needed |
| C4 0.58–0.59 (plain) | high review load; always add contract |
| C4 < 0.10 | re-derive before trusting — likely a harness problem |
| C5 0.69–0.81 | delegate scaffold; verify route wiring by hand |

**G2 labels** (from `handoff_validity.py`):

| Label | Meaning | Action |
|-------|---------|--------|
| `HARNESS_BLIND_SUSPECT` | stored code scores 0 but current harness recovers >0 | re-score; number is invalid |
| `CODE_NO_PASS` | code exists, current harness also scores 0 | number is valid |
| `SEED_ONLY` | model wrote nothing beyond the seed | verify model actually ran; check `--dir` flag |
| `TIMEOUT` | process timed out | increase timeout and re-run |
| `NO_TARGET` | harness couldn't find a runnable function/file | check file format and model output structure |

---

*Scores are specific to mistral-medium-3.5 and deepseek-flash via vibe. Run `handoff_probe.py` on your own setup to get numbers that apply to your configuration.*
