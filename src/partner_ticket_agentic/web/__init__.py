"""Web UI surface for the agentic platform.

A FastAPI app + a single-page vanilla HTML/JS frontend that depicts the
LangGraph topology, streams the per-agent outputs as a ticket runs, and
renders the HITL approval gate on the F5 draft. Boots via
``python -m partner_ticket_agentic --web`` (port 8000 by default).

Kept under one extras group (``partner-ticket-agentic[web]``) so the
core CLI install stays free of the FastAPI / uvicorn dependency tree.
"""

from __future__ import annotations
