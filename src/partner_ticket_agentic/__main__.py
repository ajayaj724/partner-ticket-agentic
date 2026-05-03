"""Module entry point: ``python -m partner_ticket_agentic``.

Delegates to :func:`partner_ticket_agentic.cli.main` so the CLI is reachable
both as ``python -m partner_ticket_agentic`` (the form used in the design
doc's demo plan) and as the installed ``partner-ticket-agentic`` script.
"""

from __future__ import annotations

from partner_ticket_agentic.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
