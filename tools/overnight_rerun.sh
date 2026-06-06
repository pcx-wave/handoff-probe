#!/usr/bin/env bash
# overnight_rerun.sh — Re-run all vibe models with the fixed harness.
#
# Fixes applied before this script was written:
#   - aiohttp mock added to _FLASK_MOCK_SETUP
#   - httpx mock added to _FLASK_MOCK_SETUP
#   - _FLASK_MOCK_SETUP added to run_test_c5
#   - lru_cache false-positive guard (MagicMock.hits not int)
#   - output cap raised: text[:8000] in vibe-delegate, output[:12000] in probe
#   - workdir_snippet cap raised: [:12000]
#   - source selection: workdir_content preferred when files written
#
# Usage:
#   nohup bash tools/overnight_rerun.sh > /tmp/overnight_rerun.log 2>&1 &
#   tail -f /tmp/overnight_rerun.log

set -euo pipefail

TOOLS_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$HOME/.handoff/overnight_$(date +%Y%m%d_%H%M%S)"
SUMMARY="$LOG_DIR/summary.txt"
MODEL_FLAG="$HOME/.local/share/vibe-model.flag"
ORIG_MODEL="$(cat "$MODEL_FLAG" 2>/dev/null || echo 'mistral-medium-3.5')"

mkdir -p "$LOG_DIR"

restore_model() {
  echo "$ORIG_MODEL" > "$MODEL_FLAG"
  echo "[cleanup] Restored vibe model to $ORIG_MODEL"
}
trap restore_model EXIT

log() {
  echo "[$(date '+%H:%M:%S')] $*" | tee -a "$SUMMARY"
}

run_vibe_model() {
  local alias="$1"
  local label="$2"
  echo "$alias" > "$MODEL_FLAG"
  log "=== START vibe/$alias ==="
  (cd "$TOOLS_DIR" && python3 handoff_probe.py \
    --model "$label" \
    --cli vibe \
    --signals sweep,contract \
    --compare-reference \
    --runs 5 \
    --clean-workdir) \
    2>&1 | tee -a "$LOG_DIR/vibe_${label}.log" | grep -E '^\s+\[|H_loss|fidelity|VALIDITY|Run complete'
  log "=== DONE  vibe/$alias ==="
}

log "overnight_rerun.sh starting — log dir: $LOG_DIR"
log "Fixes: aiohttp mock, workdir_snippet cap, source selection, C5 mock setup"
log ""

# ── Vibe models ───────────────────────────────────────────────────────────────
run_vibe_model "mistral-medium-3.5"  "mistral-medium-3.5"
run_vibe_model "deepseek-flash"      "deepseek-flash"
run_vibe_model "devstral-small"      "devstral-small"

log ""
log "=== ALL RUNS COMPLETE ==="
log "Run dirs written to ~/.handoff/runs/"
log "To regenerate profiles:"
for label in mistral-medium-3.5 deepseek-flash devstral-small; do
  log "  python3 $TOOLS_DIR/handoff_report.py --profile $label \$(ls -td ~/.handoff/runs/*${label} | head -1)"
done
log ""
log "Summary saved to: $SUMMARY"

# Rebuild registry so --valid-only is accurate for the new runs.
log "=== Rebuilding run registry ==="
python3 "$TOOLS_DIR/handoff_runregistry.py" 2>&1 | tee -a "$SUMMARY"
log "=== Registry rebuild complete ==="

# ── C1-C3 only re-run (patch function for targeted reruns) ────────────────────
run_vibe_model_c123() {
  local alias="$1"
  local label="$2"
  echo "$alias" > "$MODEL_FLAG"
  log "=== START vibe/$alias C1-C3 only ==="
  (cd "$TOOLS_DIR" && python3 handoff_probe.py \
    --model "$label" \
    --cli vibe \
    --signals sweep,contract \
    --compare-reference \
    --runs 5 \
    --clean-workdir \
    --max-level 2) \
    2>&1 | tee -a "$LOG_DIR/vibe_${label}_c123.log" | grep -E '^\s+\[|H_loss|fidelity|VALIDITY|Run complete'
  log "=== DONE  vibe/$alias C1-C3 ==="
}
