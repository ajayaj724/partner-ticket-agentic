# On-call Runbook

When the partner-ticket-agentic system misbehaves, this is the document the on-call engineer reaches for. Each entry: **symptom**, **check**, **interpretation**, **remediation**. Keep it terse — the on-call is already tired.

Source for the failure-mode list: `.planning/codebase/CONCERNS.md §1` (live panel demo risks) and `§4` (scaling gaps that manifest as runtime issues).

---

## 1 · Ollama model not loaded (cold weights)

**Symptom.** First request to a ticket on `provider=ollama` hangs for 10–30 s, then completes. Operator complains about latency. Logs show `llm_call` with `latency_ms` > 10000 only on the first call per process.

**Check.**

```bash
# Is Ollama running?
curl -s http://localhost:11434/api/tags >/dev/null && echo "ollama: up" || echo "ollama: DOWN"

# Is the model pulled?
ollama list | grep llama3.2

# Is the model warm? (weights resident in RAM)
ollama ps
```

**Interpretation.** `ollama list` shows the model present but `ollama ps` shows no running model: weights are on disk, not in RAM. First inference call loads them — 10–30 s on this hardware, longer on cold storage.

**Remediation.**

1. Warm the model: `ollama run llama3.2:3b "hi" >/dev/null`. Returns in ~5 s; weights now resident.
2. Tell the operator: "First call was a cold-cache hit; subsequent calls are warm." Don't apologise; explain.
3. If the demo machine sleeps overnight, this happens again. Disable system sleep for the panel: `caffeinate -d` (macOS) or `systemd-inhibit` (Linux).

**Prevention.** `scripts/preflight.sh` runs `ollama run llama3.2:3b "hi"` as the final warm-up step; the warm is one-shot, not durable across sleep.

---

## 2 · Ollama process not running

**Symptom.** Demo page on `provider=ollama` shows an error banner. Console: `Failed to fetch` or `ConnectionRefusedError`. The dashboard simulator on `provider=ollama` shows toasts with error chips, no successful runs.

**Check.**

```bash
# Is Ollama running at all?
curl -sI http://localhost:11434/api/tags

# If down: any prior crash log?
tail -50 ~/.ollama/logs/server.log 2>/dev/null || echo "no log"
```

**Interpretation.** Connection refused = Ollama not running. Could be: never started, crashed, or killed.

**Remediation.**

1. Start it: `ollama serve &` (or relaunch the Ollama macOS app).
2. Verify: `curl -sI http://localhost:11434/api/tags` returns `HTTP/1.1 200`.
3. Warm the model (entry §1).
4. If it crashed: capture the tail of the log before restarting; file an issue against `ollama-server` with reproduction steps.

**Fallback.** Switch the demo to `provider=mock` (top-left in the UI). The mock path is deterministic, has no LLM dependency, and is the documented CI default per `CLAUDE.md`. Make this switch *before* the panel sees you debug.

---

## 3 · Anthropic API unreachable

**Symptom.** When `provider=anthropic`: requests fail with `LLMProviderError: anthropic API call failed`. Could be timeout, 401, 429, 5xx.

**Check.**

```bash
# Is the API reachable?
curl -sI https://api.anthropic.com/v1/models -H "x-api-key: $ANTHROPIC_API_KEY"

# Is the key set?
env | grep ANTHROPIC_API_KEY | sed 's/=.*$/=<set>/'

# Is the venue WiFi flaky?
ping -c 3 api.anthropic.com
```

**Interpretation.**

- `401` → key not set / wrong key / revoked
- `429` → rate-limited (per-org cap); also check the Anthropic console for usage
- `5xx` → Anthropic-side incident; check status.anthropic.com
- `connection refused` / `timeout` → venue WiFi or DNS

**Remediation.**

1. Don't debug Anthropic live during a panel demo. Switch to `provider=ollama` (real LLM, local) or `provider=mock` (deterministic) in the UI.
2. If post-demo: confirm the key is the production key, check the per-org rate limit in the Anthropic console, retry with exponential backoff (the provider already does this for transient errors per `tools/policy.py`).
3. If the venue WiFi is blocking outbound HTTPS, tether to a phone hotspot — but verify with `curl` first.

**Prevention.** `docs/AI_ACT_ASSESSMENT.md §1` lists Anthropic as the only external egress; production deploys MUST run the connectivity check at startup and degrade to mock gracefully on failure.

---

## 4 · Frontend dev server stuck or crashed

**Symptom.** `localhost:3000` returns 502 / 504 / connection refused. Hot-reload no longer updates the page after a code edit. Next.js dev tools show an error overlay you can't dismiss.

**Check.**

```bash
# Is the Next.js dev server alive?
curl -sI http://localhost:3000 -o /dev/null -w "%{http_code}\n"

# Is there a stuck process?
pgrep -lf "next dev"

# Is the port held by something else?
lsof -i :3000
```

**Interpretation.** A wedged Next.js dev server is a known dev-mode failure mode (Turbopack caches, file-watcher OOMs). Restart cleans it.

**Remediation.**

1. Hard restart: `cd frontend && pkill -f "next dev"; pnpm dev` — takes ~10–15 s cold start.
2. If the issue persists across restarts: clear the Turbopack cache: `rm -rf .next/`.
3. Pre-panel mitigation: `scripts/up.sh` already starts the dev server early. Run it at least 60 s before the panel walks in.

**Fallback.** If the demo page is broken but the API works, you can still demo the CLI: `uv run python -m partner_ticket_agentic --ticket-id sample-1 --llm-provider ollama`. Recovery script in `docs/present.html §10` — "let me show you what would happen" from the static diagram while the front end recovers.

---

## 5 · Simulator stuck (dashboard frozen)

**Symptom.** Dashboard KPI tiles stop ticking. Toasts stop appearing. The "Live simulator" status shows running but the "tickets processed" counter doesn't advance.

**Check.**

```bash
# Is the simulator thread alive?
curl -s http://localhost:8000/api/simulate/status | jq .

# Are new tickets being processed?
curl -s http://localhost:8000/api/stats/dashboard?window=5 | jq '.window_size, .recent_runs | length'
```

**Interpretation.** `running: true` with no advance = the simulator thread is wedged. Lock contention with the request path or a stuck downstream tool call is the usual culprit (see `CONCERNS.md §4`).

**Remediation.**

1. Stop and restart the simulator from the UI: pause → wait 2 s → start.
2. If pause doesn't respond, hit the API: `curl -X POST http://localhost:8000/api/simulate/stop`, then `curl -X POST http://localhost:8000/api/simulate/start -d '{...}'`.
3. If neither works, restart the FastAPI process. The dashboard state is in-process (`web/simulator.py`), so a restart resets the window — pre-panel you can re-seed quickly.

**Fallback.** Demo on `provider=mock` runs at ~25 ms per ticket; even after a restart you'll have a populated window within 60 s.

---

## 6 · Eval suite regression (CI fail)

**Symptom.** CI `pytest -q` fails on one of:

- `tests/test_evals.py::test_triage_eval_thresholds`
- `tests/test_evals.py::test_routing_eval_thresholds`
- `tests/test_evals.py::test_runbook_eval_thresholds`
- `tests/test_evals.py::test_breach_eval_thresholds`
- `tests/test_evals.py::test_insights_eval_thresholds`
- `tests/test_hitl_contract.py::*` (HITL gate contract)
- `tests/test_graph_topology.py::*` (graph shape contract)

**Check.** Look at the failing assertion's metric and target.

```bash
# Run the full eval suite locally to see which agent regressed:
uv run python -m partner_ticket_agentic.evals
```

**Interpretation.** Two cases:

- Eval threshold failure → the deterministic mock rule changed and accuracy dropped. Re-read the rule diff in `src/partner_ticket_agentic/agents/*.py` against the latest commit.
- HITL contract failure → someone wrote `requires_approval=False` in the drafter, or the schema default flipped. This is a hard release blocker — re-read `docs/AI_ACT_ASSESSMENT.md` before merging.
- Graph topology failure → the LangGraph state machine shape changed. Re-read `docs/DESIGN.md §2` and the failure message in `tests/test_graph_topology.py`.

**Remediation.**

1. **Do not** silence the failing test. Find the root cause in the commit diff.
2. If the change is intentional (e.g. a new agent), update the expected sets in the failing test AND re-run the eval suite to verify accuracy didn't regress.
3. For HITL contract failures: revert the offending change and re-design without flipping `requires_approval`.

---

## 7 · `BudgetExceededError` blocking partner traffic

**Symptom.** Tickets for one partner consistently fail with `BudgetExceededError: partner 'P-XXX' over budget on tokens` (or `usd`). Operator complains the partner's queue isn't moving.

**Check.**

```bash
# Inspect the budget configured for the partner:
grep -A2 "P-XXX" config/budgets.yaml

# Recent token usage in the cost ledger:
grep "P-XXX" ~/.ptag/cost-ledger.json | tail -10
```

**Interpretation.** The hard-block 100% threshold fired. Either the budget cap is too low for the partner's traffic, or the partner is being abused (a runaway loop, a mis-classified workload).

**Remediation.**

1. **Don't just raise the cap.** Verify whether traffic is legitimate first.
2. If the workload is legitimate, increase the cap in `config/budgets.yaml`, restart the process, monitor.
3. If runaway: investigate the trace (`/api/trace/{ticket_id}`) for the failing tickets — look for a tight retry loop, an oversized prompt, or a stuck agent.

**Prevention.** The 70% INFO and 90% WARN alerts should have surfaced first. If they didn't reach the on-call, alert routing is misconfigured — see Tier-B item OPS-02.

---

## 8 · `BudgetState` lost across process restart (known limitation)

**Symptom.** A partner's spend resets on every FastAPI restart. They run 1000 tickets back-to-back across two restarts and never trip the cap.

**Interpretation.** This is the documented Tier-B gap (`PROJECT.md` Out of Scope · `DUR-03`). The current `BudgetState` is a context-var bound per-pipeline-invocation, not a durable per-partner ledger.

**Remediation (today).** No code fix. Operationally: monitor the JSON-line logs for daily aggregate spend per partner; alert on cumulative-from-deploy rather than per-process.

**Remediation (v2.0).** Production migration phase 2 (`OPS-01` / `DUR-03`) replaces the context-var with a durable Postgres-backed ledger. Scheduled for post-panel.

---

## SLO targets (informational)

| Signal | Mock target | Ollama target | Anthropic target |
|---|---|---|---|
| P95 end-to-end pipeline latency | 50 ms | 12 s | 4 s |
| Error rate (any failed agent) | < 0.1% | < 1% | < 0.5% |
| HITL gate violation (requires_approval=False) | 0 (hard) | 0 (hard) | 0 (hard) |
| Eval accuracy regression vs. baseline | < 2 percentage points | < 5 pp | < 5 pp |

These are targets for the v1.0 demo path. v2.0 production SLOs will be set in collaboration with the operator's NOC (Tier-B `OPS-02`).

---

*Runbook owner: Ajay Antony. Last reviewed: 2026-05-17 (v1.1 milestone start).*
*If a failure mode isn't here, add an entry rather than ad-hoc'ing the response.*
