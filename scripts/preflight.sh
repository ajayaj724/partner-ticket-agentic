#!/usr/bin/env bash
# Verify the environment is ready for a live demo.
# Checks every dependency, pulls missing model weights, syncs Python and
# Node deps, and runs the smoke test. Run this once the day before the
# panel; never the morning of.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_lib.sh
source "$SCRIPT_DIR/_lib.sh"

FAIL=0
need() { have_cmd "$1" || { err "missing: $1 — $2"; FAIL=$((FAIL + 1)); }; }

step "Tooling"
need uv      "install: curl -LsSf https://astral.sh/uv/install.sh | sh"
need node    "install Node 24 LTS+: https://nodejs.org/"
need pnpm    "install: npm install -g pnpm  (or corepack enable)"
need curl    "should already be on macOS/Linux"
need jq      "install: brew install jq"
need ollama  "install: brew install ollama  (required — UI defaults to provider=ollama)"

if (( FAIL > 0 )); then
  err "missing tooling — fix the above and re-run"
  exit 1
fi
ok "all required tooling present"

step "Python deps"
cd "$ROOT"
if uv sync --all-extras >/dev/null 2>&1; then
  ok "uv sync --all-extras succeeded"
else
  err "uv sync failed — see output:"
  uv sync --all-extras
  exit 1
fi

step "Node deps"
cd "$FRONTEND_DIR"
if [[ -d node_modules ]]; then
  ok "node_modules present (skipping pnpm install)"
else
  say "running pnpm install (one time)…"
  if pnpm install --silent; then
    ok "pnpm install succeeded"
  else
    err "pnpm install failed"; exit 1
  fi
fi

step "Ollama models (required — UI demo default; both tiers must be present)"
if ! port_in_use "$OLLAMA_PORT"; then
  say "starting ollama serve…"
  nohup ollama serve >"$LOG_DIR/ollama.log" 2>&1 &
  wait_for "http://localhost:$OLLAMA_PORT/api/tags" 15 "ollama" || { err "ollama failed to start"; exit 1; }
fi

# Ensure both SMALL and MEDIUM tier models are pulled. F1 Triage and F8 Watchdog
# hit SMALL (llama3.2:3b); F9 Insights cross-stream synthesis hits MEDIUM
# (llama3.1:8b). Skipping either model means an agent fails closed with a 404
# from Ollama on demo day. Catch it here, not on stage.
for MODEL in "$OLLAMA_MODEL" "$OLLAMA_MODEL_MEDIUM"; do
  if ollama list | awk '{print $1}' | grep -qx "$MODEL"; then
    ok "model $MODEL already pulled"
  else
    say "pulling $MODEL (this can take several minutes — MEDIUM is ~5 GB)…"
    ollama pull "$MODEL" || { err "ollama pull failed for $MODEL"; exit 1; }
    ok "pulled $MODEL"
  fi
done

say "warming up models with a noop prompt (loads weights into RAM)…"
for MODEL in "$OLLAMA_MODEL" "$OLLAMA_MODEL_MEDIUM"; do
  ollama run "$MODEL" "hi" >/dev/null 2>&1 \
    || { err "ollama run failed for $MODEL — model not usable"; exit 1; }
  ok "$MODEL warm and responsive"
done

step "End-to-end smoke test"
cd "$ROOT"
# Bring everything up, run smoke, then bring it back down so preflight
# leaves no dangling processes.
"$SCRIPT_DIR/up.sh" >/dev/null
trap '"$SCRIPT_DIR/down.sh" >/dev/null 2>&1 || true' EXIT
"$SCRIPT_DIR/smoke.sh"

echo
ok "${C_BOLD}preflight passed — you are demo-ready${C_OFF}"
say "${C_DIM}next: at demo time run ./scripts/up.sh${C_OFF}"
