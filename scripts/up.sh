#!/usr/bin/env bash
# Start everything needed for the live demo:
#   - Ollama (if installed and not already running)
#   - FastAPI backend on :8000
#   - Next.js frontend on :3000
# Waits until both HTTP endpoints respond, then opens the browser.
#
# Idempotent: if a service is already up, leaves it alone.
# Logs go to .ptag/logs/{backend,frontend,ollama}.log
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_lib.sh
source "$SCRIPT_DIR/_lib.sh"

# --- Ollama (optional) ----------------------------------------------------
step "Ollama"
if have_cmd ollama; then
  if port_in_use "$OLLAMA_PORT"; then
    ok "ollama already serving on :$OLLAMA_PORT"
  else
    say "starting ollama serve in background…"
    nohup ollama serve >"$LOG_DIR/ollama.log" 2>&1 &
    wait_for "http://localhost:$OLLAMA_PORT/api/tags" 15 "ollama" && ok "ollama up"
  fi
  # match either the full tag (llama3.2:3b) or the bare name (llama3.2)
  models="$(ollama list 2>/dev/null | awk 'NR>1 {print $1}')"
  if printf '%s\n' "$models" | grep -Fxq "$OLLAMA_MODEL" \
     || printf '%s\n' "$models" | grep -Fxq "${OLLAMA_MODEL%%:*}:latest"; then
    ok "model $OLLAMA_MODEL present"
  else
    warn "model $OLLAMA_MODEL not pulled — provider=ollama will fail. Run: ollama pull $OLLAMA_MODEL"
  fi
else
  warn "ollama not installed — provider=ollama will be unavailable (mock + anthropic still work)"
fi

# --- Backend --------------------------------------------------------------
step "Backend (FastAPI on :$BACKEND_PORT)"
if port_in_use "$BACKEND_PORT"; then
  ok "backend already up on :$BACKEND_PORT"
else
  say "starting backend…"
  cd "$ROOT"
  nohup uv run python -m partner_ticket_agentic --web --port "$BACKEND_PORT" \
        >"$LOG_DIR/backend.log" 2>&1 &
  wait_for "http://localhost:$BACKEND_PORT/api/tickets" 30 "backend" && ok "backend up"
fi

# --- Frontend -------------------------------------------------------------
step "Frontend (Next.js on :$FRONTEND_PORT)"
if port_in_use "$FRONTEND_PORT"; then
  ok "frontend already up on :$FRONTEND_PORT"
else
  if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
    warn "node_modules missing — running pnpm install (one time)…"
    (cd "$FRONTEND_DIR" && pnpm install --silent)
  fi
  say "starting frontend…"
  cd "$FRONTEND_DIR"
  nohup pnpm dev >"$LOG_DIR/frontend.log" 2>&1 &
  wait_for "http://localhost:$FRONTEND_PORT/" 30 "frontend" && ok "frontend up"
fi

# --- Open the browser -----------------------------------------------------
step "Browser"
URL="http://localhost:$FRONTEND_PORT/"
if have_cmd open; then
  open "$URL"; ok "opened $URL"
elif have_cmd xdg-open; then
  xdg-open "$URL"; ok "opened $URL"
else
  say "open this manually: $URL"
fi

echo
ok "${C_BOLD}all systems go${C_OFF}"
say "${C_DIM}logs:    $LOG_DIR/{backend,frontend,ollama}.log${C_OFF}"
say "${C_DIM}stop:    ./scripts/down.sh${C_OFF}"
say "${C_DIM}verify:  ./scripts/smoke.sh${C_OFF}"
