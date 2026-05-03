# Partner-Ticketing Agentic Platform

Reference implementation accompanying Ajay Antony's Capgemini Blue Harvest
panel deck. Demonstrates an agentic AI architecture for partner-ticketing in
a telecom context: deterministic LangGraph orchestration, Pydantic-validated
agent I/O, three memory tiers, per-agent tool allow-listing, and a pluggable
LLM provider (mock by default; Anthropic and Ollama opt-in).

> **Status:** scaffold — feature commits land sequentially (F1 Triage →
> F2 Enricher → F3 Router → F4 Knowledge → F5 Drafter → F7 Linker →
> F6 Scheduler → F8 Watchdog).

See [`docs/DESIGN.md`](docs/DESIGN.md) for the full architectural specification.

## Quick start

```bash
uv sync --all-extras
uv run python -m partner_ticket_agentic --list
uv run python -m partner_ticket_agentic --ticket-id sample-1
```

The default provider is the deterministic mock, so the demo runs offline
with no API keys.

## License

MIT — see [`LICENSE`](LICENSE).
