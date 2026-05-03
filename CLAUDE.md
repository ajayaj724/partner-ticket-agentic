# Project memory for Claude Code

This file is the standing ruleset for this repo. Read it at the start of every session.

## What this is

Reference implementation accompanying Ajay Antony's Capgemini Blue Harvest panel interview deck. Demonstrates an agentic AI architecture for partner-ticketing in a telecom context. Open source (MIT). Will be shown to engineering leads / software architects.

## Authoritative spec

`docs/DESIGN.md` is the spec. Read it end-to-end before any architectural decision. If the user asks you to change something that contradicts the design doc, **stop and confirm** before proceeding — don't silently diverge.

## Tech rules (non-negotiable)

- Python 3.11+. Use `uv` for the package manager.
- Real **LangGraph** for orchestration. Do not roll your own state machine.
- **Pydantic v2** for every agent's input/output schema. Free-text LLM outputs are rejected at the boundary.
- **Mock LLM is the default provider.** Anthropic and Ollama are opt-in via `--llm-provider`. Default path must run offline with no API keys.
- Tool allow-listing is enforced in code (a check that raises, not a convention).
- Every feature in the catalogue (F1–F8) must have at least one eval entry under `evals/`.
- Use `ruff` for lint + format. Use `pytest` for tests.

## Code style

- Type-annotate every public function. Module docstrings on every file.
- Structured logging — JSON lines, never `print` in library code.
- No silent failures. Every tool call has explicit error handling and a defined fallback.
- Idempotency keys on side-effecting tools (even when mocked).
- Mock LLM responses are written as deterministic if/elif rules — never random, never hash-based. A reviewer must be able to read the mock and predict its output.

## Repository conventions

- Conventional commits: `feat:`, `fix:`, `docs:`, `chore:`, `test:`, `refactor:`. Small, focused commits — one feature or fix per commit.
- Branch protection on `main` is not required for this project, but commit hygiene matters because the panel will read the git log.
- `README.md` is architect-grade — context, ASCII architecture diagram, feature catalogue, quick-start, demo plan. Pulls from `docs/DESIGN.md`.

## Definition of done (per feature)

A feature is done when:
1. The agent + tools are implemented with Pydantic schemas.
2. The mock LLM has deterministic rules for that agent's prompts.
3. There is at least one eval entry exercising it.
4. The CLI demo run that touches it passes cleanly.
5. The README's feature catalogue lists it with one paragraph.

## Definition of done (overall)

The project is done when:
- All five demo runs in `docs/DESIGN.md` Section 6 pass cleanly.
- `pytest` is green.
- `python -m partner_ticket_agentic.evals` runs and prints precision/recall per agent.
- GitHub Actions workflow is green.
- README is complete.
- A panel reviewer can clone, install, and run a demo in under 3 minutes.

## When in doubt

Ask. The user (Ajay) is preparing for a panel interview — surfacing trade-offs to him is more valuable than making the call silently. If you're about to deviate from the design doc, hit pause and surface it.
