#!/usr/bin/env bash
# Stop the backend and frontend. Does NOT stop Ollama — it may be a
# system service the user wants running.
#
# Safe to run when nothing is up; just reports "not running".
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_lib.sh
source "$SCRIPT_DIR/_lib.sh"

kill_port() {
  local port="$1" label="$2"
  if ! port_in_use "$port"; then
    say "${C_DIM}$label  not running on :$port${C_OFF}"
    return 0
  fi
  if have_cmd lsof; then
    local pids
    pids="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)"
    if [[ -n "$pids" ]]; then
      # shellcheck disable=SC2086
      kill $pids 2>/dev/null || true
      sleep 1
      # shellcheck disable=SC2086
      kill -9 $pids 2>/dev/null || true
    fi
  fi
  if port_in_use "$port"; then
    err "$label still up on :$port"
    return 1
  fi
  ok "$label stopped (:$port)"
}

step "Stopping services"
kill_port "$FRONTEND_PORT" "frontend"
kill_port "$BACKEND_PORT"  "backend"

if port_in_use "$OLLAMA_PORT"; then
  say "${C_DIM}ollama   left running on :$OLLAMA_PORT (use 'ollama stop' if you want to kill it)${C_OFF}"
fi

echo
ok "${C_BOLD}done${C_OFF}"
