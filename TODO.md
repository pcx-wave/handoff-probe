# TODO — from project evaluation, June 10 2026

Verified baseline before these items: `handoff_funcretro.py --valid-only` reproduces
RESULTS.md exactly (37 valid runs), 87/87 tests pass, G1 passes. Items below are the
gaps found.

## P1 — published numbers / quick doc fixes

- [ ] **README.md:15** — stale headline: "Mistral at C4 … functional 0.49" → canonical
      37-run score is **0.58** (0.49 was the May-30 snapshot). Should read 1.00 vs 0.58.
- [ ] **RESULTS.md:4** — "output truncated at **400** chars" → was **500** chars
      (`output[:500]`, CHANGELOG v1.3).
- [ ] **Bug count reconciliation** — README/METHODOLOGY say "three bugs", RESULTS says
      "five". Phrase consistently: five harness bugs total, three of which scored
      working code as 0.
- [ ] **CHANGELOG** — add the missing entry for the C4/C5 mock-setup fix
      (`_FLASK_MOCK_SETUP`, `tools/handoff_functests.py:300`). It is cited in RESULTS
      provenance but documented nowhere.
- [ ] **RESULTS.md** — remove duplicated "Precision" paragraph (appears verbatim at
      line 77 "Limits" and line 125 "Reading the scores"; keep one).

## P2 — dependency truth

- [ ] **requirements.txt** — replace. Currently lists Flask/SQLAlchemy/flask-sqlalchemy/
      cachetools/httpx/aiosqlite (mostly NOT installed in the measurement env; harness
      mocks them) and omits `pyyaml`, the only real import (`handoff_report.py:12`).
      → `pyyaml` + comment that the functional harness mocks flask/sqlalchemy/httpx/aiohttp.
- [ ] **README Installation** — keep in sync with the above ("only external dependency"
      claim is currently contradicted by requirements.txt).
- [ ] **README Quickstart** — the "scale up to `--signals sweep,contract --runs 5`"
      sentence omits `--compare-reference`; without it SWEEP/CONTRACT produce no
      functional scores.

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
