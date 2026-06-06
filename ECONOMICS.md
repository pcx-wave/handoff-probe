# handoff-probe — Token Economics of Delegation
*May 2026 — measured from instrumented runs*

---

## 1. There are two meters, and they bill differently

| | Orchestrator (Claude) | Sub-model (vibe/opencode) |
|---|---|---|
| Pays for | planning, reading results, verification, corrections | the actual code generation |
| Token shape | ~99% input (context), ~1% output | ~98% input (workdir), ~2% output |
| Price | output 5×, cache-write 1.25×, cache-read 0.1×, input 1× | flat, **5–50× cheaper per token**; often free tier |
| Scarcity | **your plan quota** | separate meter — does **not** touch your Claude quota |

**Both meters are input-dominated.** Generation (output) is a small slice of volume on both sides. The expensive thing is moving context around.

---

## 2. The delegation loop, measured

```
(1) user instruction → Claude plan
(2) Claude → delegation prompt
(3) sub-model implements            ← off your quota
(4) result → Claude verifies
(5) Claude → corrections (loop back to 3)
```

### Per-phase token accounting — a real 1.5h dev session (293 API calls, 7 delegations)

Produced by `tools/handoff_tokens.py`. Cost units: input 1×, cache-write 1.25×, cache-read 0.1×, output 5×.

| Phase | Calls | input | cache_write | cache_read | output | Cost share | Dominated by |
|-------|------:|------:|------------:|-----------:|-------:|-----------:|--------------|
| 1. PLAN | 26 | 39 | 37,575 | 3,167,051 | 24,159 | **10.7%** | cache_read (carried context) |
| 2. DELEGATE_EMIT | 3 | 3 | 2,083 | 349,883 | 2,641 | **1.1%** | *writing the prompt — nearly free* |
| 3. *implementation* | — | — | — | — | — | **off-quota** | **sub-model: 767K in / 8.8K out, $1.10** |
| 4. VERIFY | 38 | 38 | 33,035 | 5,321,566 | 18,105 | **14.7%** | cache_read |
| 5. CORRECTIONS | 37 | 37 | 46,529 | 4,231,520 | 32,671 | **14.2%** | cache_read + output |
| — DIRECT (non-loop work) | 189 | 6,084 | 512,480 | 14,569,455 | 115,564 | 59.3% | cache_read |

What this shows:
- **Handing off is nearly free (step 2 = 1.1%).** The "cost of delegating" is not the handoff itself.
- **The loop's real Claude cost is VERIFY + CORRECTIONS (~29%), both dominated by cache_read** — by carrying context, not by the verification logic itself.
- **The actual implementation cost $1.10 on 776K tokens that never touched the Claude quota.** That is the lever: the token-heavy step is the one that left the meter entirely.

---

## 3. Token profile of a real session (read/analysis-heavy, 403 API calls)

| Term | Raw tokens | Raw % | Weighted | Weighted % (cost) |
|------|-----------|-------|----------|-------------------|
| cache read | 49,285,036 | 94.8% | 4,928,504 | **50.8%** |
| cache write | 2,296,825 | 4.4% | 2,871,031 | 29.6% |
| output | 372,374 | 0.72% | 1,861,870 | **19.2%** |
| fresh input | 39,748 | 0.08% | 39,748 | 0.4% |

Output is the priciest *per token* (5×), but this session produced 372K output against 49M cache-reads — 132× more read volume — so in aggregate, input handling is 80% of cost.

> ⚠️ This session was read/analysis-heavy, not delegation-heavy. Use it to understand the mechanics, not as a universal ratio.

Sub-model side, same project, 88 delegations: `tokens_in 3.76M (98.3%)`, `tokens_out 63K (1.7%)`, cost **$5.57** — all of it off the Claude quota.

---

## 4. The lifetime cost of a token in context

A token that enters Claude's context at turn *t* of a *T*-turn session costs roughly:

```
lifetime_cost(token) ≈ 1.25          (written to cache once)
                      + 0.10 × (T − t) (re-read on every later turn)
```

At T=400, t=100: **31.25×** face value. At t=1: **41×**. Near the end: **~1.3×**.

Consequences:
- Pre-hook trimming of verbose bash/build output is worth far more than its face size — you remove the 1.25× write and all subsequent 0.10× re-reads.
- A tool result that enters context early in a long session costs tens of times more than the same result near the end.

---

## 5. Why delegation lets you do more — the context-leak mechanism

The real reason delegation lets you do more per session is not that generation is cheaper on the sub-model (true but secondary). It is that the sub-model's file-reading work never enters Claude's context.

When Claude does a task directly, every file it reads, every tool result that comes back, accumulates in its context and is re-read on every subsequent turn (at the 0.10× rate). When Claude delegates, the sub-model reads those files instead — none of that ever enters Claude's context. Only the result (the diff or the changed file) comes back. Claude's context stays lean, and lean context means more tasks fit in the session budget.

This effect scales with task size: the more files a task touches, the more file-reading work is kept off Claude's context, and the larger the session-budget saving. For a task that reads no files at all (like the probe's self-contained C1–C5 tasks), the mechanism doesn't apply and delegation's fixed overhead dominates — which is why the probe shows no session-budget advantage and is not designed to measure one.

### Quota cost comparison — direct vs delegation

Cost units: output tokens × lifetime 31.25× (T=400 turns, token enters at t=100), cache_read × 0.1 per subsequent turn. Delegation overhead = 1,430 output tokens (prompt + verify) + 564 tokens result diff returned. Source file assumed 2,000 tokens.

| Scenario | Direct quota cost | Delegation quota cost | Saves |
|---|---:|---:|:---:|
| Self-contained task (no file reads) | 18,900 | 69,000 | **−265% ✗** |
| Real task, ~3 source files | 59,600 | 69,000 | **−16% ✗** |
| Real task, ~10 source files | 200,200 | 69,000 | **+66% ✓** |
| Real task, ~30 source files | 637,700 | 69,000 | **+89% ✓** |

**Breakeven: ~4 source files.** Below that, doing it directly costs less quota. Above it, delegation wins by a growing margin.

What's measured: delegation prompt/verify token counts (1,430 tokens across 40 sessions), result diff size (564 tokens avg across C1–C5). What's modelled: file size per source file, session length. The savings curve shape is robust to reasonable variation in those parameters; the exact breakeven shifts by ±1–2 files.

---

## 6. Two objectives — pick the right lever

### A. Cost-efficiency (dollar-weighted tokens)
Context dominates. The levers are:
1. **Compact context after each feature** — attacks cache_read (50.8% of cost in §3).
2. **Hook-trim verbose tool output** — removes write cost and all future re-reads.
3. Delegation helps but is secondary *in dollar terms* because output is "only" ~19% even before delegation.

### B. Quota scarcity (tokens on the Claude meter)
This is the binding constraint on capped plans. The question is not "how cheap" but **"which tokens hit my meter at all."**
1. **Delegation is the only lever that moves work off your quota entirely** — the sub-model's token volume (millions) never appears on your Claude meter.
2. **Context discipline** is what lets a single session do more before hitting the per-session cap.

These attack different limits and are complementary.

---

## 7. How to measure it for your own workflow

- Orchestrator: `~/.claude/projects/<project>/*.jsonl` → per-turn `usage` object.
- Sub-model: `~/.local/share/delegate-runs.jsonl` → per-delegation `tokens_in/out`, `cost_usd`.

```bash
python3 tools/handoff_tokens.py ~/.claude/projects/<project>/<session>.jsonl --project <name>
```

This produces the per-phase table in §2. The sub-model side is pulled from `delegate-runs.jsonl` by matching timestamps.

**Next step:** A/B the same feature done by delegation vs claude-direct, reported in quota-tokens — the number that actually matters on a capped plan. This would also ground the breakeven calculation in §5 empirically rather than from a model.
