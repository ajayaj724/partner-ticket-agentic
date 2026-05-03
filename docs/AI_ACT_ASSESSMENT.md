# EU AI Act Risk Assessment

**System:** Partner-Ticketing Agentic Platform
**Version:** 0.1.0
**Author:** Ajay Antony
**Last reviewed:** 2026-05-04
**Classification proposed:** *Limited risk*

This document is the deployment-time risk assessment that
[`docs/DESIGN.md`](DESIGN.md) §4.5 commits to producing. It pins the
classification of the deployed system against the EU AI Act's risk
tiers, names the obligations that follow from that classification, and
maps each one to the corresponding implementation in this repository.

---

## 1. Classification

### Conclusion

The platform is *limited risk* under the EU AI Act when deployed as
designed.

### Reasoning

Three properties of the deployed system put it below the *high-risk*
threshold:

1. **No irreversible automated decisions affecting individuals.** Every
   outbound communication and every state-changing action goes through
   a human-approval gate (DESIGN.md §2 "Human-in-the-loop on outbound").
   F5 always emits `requires_approval=True`. F6 Scheduler proposes;
   it never books. F7 Linker suggests; it never auto-merges. F8
   Watchdog notifies on-call; it does not change SLAs or close tickets.
2. **Decision-support, not decision-maker.** The agents produce
   classifications, retrievals, drafts, and recommendations. The human
   reviewer makes the final call. A wrongly-triaged ticket can be
   reclassified by the engineer; the audit trail makes the original
   classification visible.
3. **No biometric, employment, education, law enforcement, migration,
   or essential-services determination.** The use case is internal
   B2B partner support — a routine operational workflow.

The system is therefore not a "high-risk AI system" under Annex III of
the AI Act. The transparency obligations of *limited risk* apply (Art.
52).

### Re-evaluation triggers

Re-run this assessment if any of the following change:

- Outbound communication becomes auto-send (F5 stops requiring
  approval). This would move the system into a higher-risk tier
  because consequential decisions reach the partner without human
  review.
- The platform begins to route or block tickets affecting access to
  essential services (e.g., emergency-service circuits where the
  triage outcome affects life-safety). That activates Annex III
  considerations.
- The watchdog's autonomous notifications expand to autonomous
  actions (e.g., auto-cancelling appointments, auto-pausing
  invoicing).
- Any agent gains the ability to write to the partner's CRM record or
  contract terms.

---

## 2. Obligations (limited risk → transparency)

### 2.1 Transparency to natural persons

When a partner interacts with the system (Art. 52(1)), the platform
must make clear that they are interacting with an AI. In the current
deployment the platform never communicates outbound to the partner
without human approval, so the obligation is satisfied trivially:
every message a partner receives is sent by a named human reviewer.

If the deployment ever moves to direct AI-partner messaging (e.g., an
F9 chat concierge — out of scope for v1), each AI-authored message
must carry the disclosure. F5's `DrafterOutput.template_id` is the
natural integration point: append a footer template like *"This reply
was prepared with AI assistance and reviewed by …"* before send.

### 2.2 Logging and traceability

Every agent and tool call emits a structured JSON-line log with a
`trace_id` (DESIGN.md §4.3). `python -m partner_ticket_agentic
--ticket-id … --export-trace PATH` writes a single ticket's full
trace to disk for replay and audit. Logs include the provider, model
tier, latency, and outcome, so regulator-style audits are
data-supported.

### 2.3 Robustness and security (Art. 15 — applies in spirit)

- **Tool allow-listing** (DESIGN.md §2; `safety.py`,
  `tools/registry.py`) caps each agent's blast radius to its declared
  tool surface. An attempted out-of-list call raises a typed
  `ToolNotAllowedError` rather than silently succeeding.
- **Prompt-injection filter** (`safety.py`) rejects the most common
  jailbreak vectors at ingest. The filter is heuristic; production
  deployments would extend with a managed classifier.
- **Schema-first I/O** (Pydantic v2 across every agent) prevents
  malformed model output from propagating into downstream side-effects.

---

## 3. GDPR considerations

### 3.1 PII redaction at ingest

The compliance filter in F5 (`tools/templates.py::compliance_filter`)
scans for IBANs, Belgian national IDs, password-in-body, and secret
tokens, and blocks the draft if any pattern matches. This is the
design's *outbound* gate; an *inbound* PII redaction step would be
added before episodic memory persistence in production (see §3.3).

### 3.2 Lawful basis

The platform processes partner data under the existing customer
contract (Art. 6(1)(b)). The processing is necessary for performance
of the contract — the partner submitted the ticket and expects it to
be handled.

### 3.3 Right to erasure

When a partner is offboarded, the operator must purge that partner's
records from the platform:

- Episodic memory rows (`memory/episodic.py`) keyed by `partner_id`
  must be deleted.
- Long-term memory: structured facts keyed by `partner_id` must be
  deleted; runbook embeddings carry no per-partner data and stay.
- Trace exports retained per the platform's retention policy must be
  filtered.

The implementation of this flow is owned by the customer-offboarding
workflow, not by this repository. DESIGN.md §7 flags it as a
production-completion item.

### 3.4 Data residency

Anthropic supports region-pinning (`anthropic-ratelimit-region`); the
deployed config sets EU-only. Ollama is local-only by construction.
Mock is offline.

---

## 4. Approved-model governance

`config/approved_models.yaml` is the single source of truth for which
(provider, tier) pairs are permitted at runtime. The
`ApprovedModelRegistry` class loads it at provider construction and
raises `LLMProviderError` on unapproved combinations. Adding a new
model is therefore a deliberate, file-edit + commit action — not a
runtime config that can be flipped without review.

---

## 5. Out of scope for v1

The following items are part of a production governance posture but
are not in scope for this reference implementation. Each is flagged
in DESIGN.md §7 as a deployment-completion item.

- Shadow-mode A/B against current human decisions before enabling
  autonomy on any new agent.
- Bias / fairness assessment on triage and routing — small risk in a
  B2B partner context but worth documenting before scale.
- Quarterly review of the prompt-injection filter against new attack
  patterns.
- Integration of right-to-erasure into the customer-offboarding
  workflow.
