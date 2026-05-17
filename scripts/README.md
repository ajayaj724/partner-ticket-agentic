# Scripts

Convenience scripts for running the live demo and verifying the environment.
All scripts are POSIX-bash; logs go to `.ptag/logs/`.

| Script | What it does | When to run |
|---|---|---|
| `preflight.sh` | Verifies all tooling, syncs deps, pulls the Ollama model, runs a smoke test. | Once, the day before a demo |
| `up.sh`        | Starts Ollama (if installed), FastAPI on `:8000`, and Next.js on `:3000`. Opens the browser. Idempotent. | Minutes before the demo |
| `smoke.sh`     | Hits `/api/run/sample-1` against each provider; prints triage + latency table. | Any time, to verify the pipeline is alive |
| `down.sh`      | Stops the backend and frontend. Leaves Ollama alone. | When you're done |

## Typical flow

```bash
# Day before
./scripts/preflight.sh

# Demo time
./scripts/up.sh                 # opens http://localhost:3000

# Sanity-check just before going live
./scripts/smoke.sh

# After
./scripts/down.sh
```

## Overrides

All four read environment variables for non-default setups:

```bash
BACKEND_PORT=9000 FRONTEND_PORT=3001 OLLAMA_MODEL=llama3.1:8b ./scripts/up.sh
```

## Where logs go

- `.ptag/logs/backend.log`  — FastAPI / uvicorn output
- `.ptag/logs/frontend.log` — Next.js dev server output
- `.ptag/logs/ollama.log`   — Ollama server output (only if `up.sh` started it)

Tail any of them with `tail -f .ptag/logs/<name>.log`.
