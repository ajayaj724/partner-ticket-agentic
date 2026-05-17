#!/usr/bin/env bash
# Quick end-to-end test of the agent pipeline against each provider.
# Hits the live FastAPI on :8000 and prints a compact summary.
#
# Exit code is non-zero if any provider returns an unexpected result.
# Use after ./up.sh to verify the panel demo will work.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_lib.sh
source "$SCRIPT_DIR/_lib.sh"

if ! have_cmd jq; then
  err "jq is required (brew install jq)"
  exit 2
fi

if ! port_in_use "$BACKEND_PORT"; then
  err "backend not up on :$BACKEND_PORT — run ./scripts/up.sh first"
  exit 2
fi

TICKET="${1:-sample-1}"
PROVIDERS=("mock")
if port_in_use "$OLLAMA_PORT" && have_cmd ollama; then
  PROVIDERS+=("ollama")
else
  warn "ollama not reachable on :$OLLAMA_PORT — skipping"
fi

failures=0
step "Smoke test · ticket=$TICKET · providers=${PROVIDERS[*]}"

printf '\n%-12s  %-14s  %-9s  %-6s  %-10s  %s\n' \
  "PROVIDER" "CATEGORY" "URGENCY" "CONF" "LATENCY" "TRACE"
printf '%s\n' "─────────────────────────────────────────────────────────────────────────────"

for prov in "${PROVIDERS[@]}"; do
  start_ns=$(python3 -c 'import time; print(int(time.time_ns()))')
  body=$(curl -sf "http://localhost:$BACKEND_PORT/api/run/$TICKET?provider=$prov" || echo "")
  end_ns=$(python3 -c 'import time; print(int(time.time_ns()))')
  if [[ -z "$body" ]]; then
    printf '%-12s  %s\n' "$prov" "${C_ERR}HTTP error — see $LOG_DIR/backend.log${C_OFF}"
    failures=$((failures + 1))
    continue
  fi
  cat=$(echo "$body"  | jq -r '.state.triage.category   // "—"')
  urg=$(echo "$body"  | jq -r '.state.triage.urgency    // "—"')
  conf=$(echo "$body" | jq -r '.state.triage.confidence // 0')
  trace=$(echo "$body"| jq -r '.state.trace_id          // "—"')
  ms=$(( (end_ns - start_ns) / 1000000 ))
  if [[ "$cat" == "—" ]]; then
    printf '%-12s  %sno triage in response%s\n' "$prov" "$C_ERR" "$C_OFF"
    failures=$((failures + 1))
    continue
  fi
  printf '%-12s  %-14s  %-9s  %-6s  %5sms     %s\n' \
    "$prov" "$cat" "$urg" "$conf" "$ms" "$trace"
done

echo
if (( failures == 0 )); then
  ok "${C_BOLD}smoke test passed${C_OFF}"
  exit 0
fi
err "$failures provider(s) failed"
exit 1
