# shellcheck shell=bash
# Shared helpers for the scripts/ family. Sourced by the other scripts;
# never executed directly.

# Resolve repo root regardless of where the caller invoked the script from.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="$ROOT/frontend"
LOG_DIR="$ROOT/.ptag/logs"
mkdir -p "$LOG_DIR"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
OLLAMA_PORT="${OLLAMA_PORT:-11434}"
# SMALL tier — F1 Triage, F8 Watchdog. The hot-path model.
OLLAMA_MODEL="${OLLAMA_MODEL:-llama3.2:3b}"
# MEDIUM tier — F9 Insights cross-stream synthesis. Larger reasoning capacity
# at the cost of disk + RAM. Must match config/approved_models.yaml ollama.medium.
OLLAMA_MODEL_MEDIUM="${OLLAMA_MODEL_MEDIUM:-llama3.1:8b}"

# ----- pretty output -----------------------------------------------------
if [[ -t 1 ]]; then
  C_OK=$'\033[32m'; C_ERR=$'\033[31m'; C_WARN=$'\033[33m'
  C_DIM=$'\033[2m'; C_BOLD=$'\033[1m'; C_OFF=$'\033[0m'
else
  C_OK=""; C_ERR=""; C_WARN=""; C_DIM=""; C_BOLD=""; C_OFF=""
fi

say()  { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$C_OK"   "$C_OFF" "$*"; }
warn() { printf '%s!%s %s\n'      "$C_WARN" "$C_OFF" "$*"; }
err()  { printf '%s✗%s %s\n' "$C_ERR"  "$C_OFF" "$*" >&2; }
step() { printf '\n%s─── %s%s\n' "$C_BOLD" "$*" "$C_OFF"; }

# ----- predicates --------------------------------------------------------
have_cmd() { command -v "$1" >/dev/null 2>&1; }

port_in_use() {
  # macOS + Linux: prefer lsof, fall back to nc.
  if have_cmd lsof; then
    lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
  else
    nc -z 127.0.0.1 "$1" >/dev/null 2>&1
  fi
}

http_ok() { curl -sf -o /dev/null --max-time 2 "$1"; }

wait_for() {
  # wait_for <url> <max-seconds> <label>
  local url="$1" max="$2" label="$3" elapsed=0
  while (( elapsed < max )); do
    if http_ok "$url"; then return 0; fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
  err "$label did not respond at $url within ${max}s"
  return 1
}
