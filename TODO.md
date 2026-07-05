# TODO — from project evaluation, June 10 2026

Verified baseline before these items: `handoff_funcretro.py --valid-only` reproduces
RESULTS.md exactly (37 valid runs), 87/87 tests pass, G1 passes. Items below are the
gaps found.

## P1 — published numbers / quick doc fixes — ✅ DONE 2026-07-05 (commit 1a9261e)

- [x] **README.md:15** — 0.49 → 0.58, canonical retro-scored value noted.
- [x] **RESULTS.md:4** — 400 → 500 chars.
- [x] **Bug count reconciliation** — METHODOLOGY.md now says "three of five total
      harness bugs... scored otherwise-working code as 0", with the other two
      (mock setup, truncation) explained as corrupting execution/data rather than
      misreading correct output.
- [x] **CHANGELOG** — v1.9 entry added documenting `_FLASK_MOCK_SETUP`.
- [x] **RESULTS.md** — duplicated "Precision" paragraph removed, single-line
      cross-reference left under "Limits".

## P2 — dependency truth — ✅ DONE 2026-07-05 (commit 1a9261e)

- [x] **requirements.txt** — replaced with `pyyaml` + explanatory comment.
- [x] **README Installation** — was actually already correct on the substantive
      point (pyyaml-only); added a clarifying sentence that Flask/SQLAlchemy/
      httpx/aiohttp are scaffold text, not real dependencies.
- [x] **README Quickstart** — `--compare-reference` added to the scale-up command.

## P3 — harness fixes BEFORE next measurement campaign

- [ ] **C5 round-trip under-credits idiomatic caches** (`handoff_functests.py:550-582`):
      only a class defined in source with `.set()`/`.get()` (or lru_cache fn) can pass
      assertion 3. `cachetools.TTLCache` (dict-style), plain dict caches, and
      module-level cache instances cap at 0.67 structurally. Verified on stored
      deepseek C5 run: 2/3 with and without cachetools available. Part of the C5
      0.69–0.81 plateau is harness design, not capability.
      → extend round-trip: dict-style `__setitem__`/`__getitem__`, module-level
      instances exposing get/set.
- [ ] **Mock `cachetools`** in `_FLASK_MOCK_SETUP` — neither installed nor mocked today.
      No valid run corrupted yet (checked), but a future C5 solution importing it
      ImportErrors → 0, and G2 would mislabel it CODE_NO_PASS (re-score hits the same
      missing module).
- [ ] **Contract C3 prompt trap still live** (`handoff_signals.py:262`):
      `tuple[bool, list[str]]` provokes `from typing import tuple` (invalid ≥3.9) from
      some models. Flagged in CHANGELOG v1.8 as "fix before increasing n" — unchanged.
      → add one sentence: "`tuple` is a builtin; do not import it from typing."
- [ ] **Seeded-workdir confound** (CHANGELOG v1.5 "known confound") — context names
      files that aren't actually seeded, nudging agents toward "already implemented".
      Still open; revise setup to describe only files actually seeded.

## P4 — auditability

- [ ] **ECONOMICS.md §5 quota table has no generating script.** Delegation column
      (~69,000 ≈ 1,430×(5+31.25) + 564×31.25) reconstructs; the direct column's
      per-file increments are non-constant (≈13.6k → 20.1k → 21.9k per file) with no
      stated mechanism. This table already needed one "restore with corrected
      calculation" commit. → commit the calculation script so the ~4-file breakeven
      is auditable.
- [ ] **Date headers** — RESULTS/ECONOMICS say "*May 2026*" but the data window and
      the C3 pilot extend into June 2026.

## Replication queue (from RESULTS caveats, not from this evaluation)

- [ ] Context-pointing pilot (push vs pull, C3 only, n=5–6) — replicate at n≥10
      before trusting magnitudes.
- [ ] devstral-small — valid measurement requires opencode + workdir runs
      (chat-mode vibe runs are excluded as NO_TARGET artefacts).
