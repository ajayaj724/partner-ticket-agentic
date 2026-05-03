"""Partner-Ticketing Agentic Platform.

Reference implementation accompanying Ajay Antony's Capgemini Blue Harvest
panel deck. Demonstrates an agentic AI architecture for partner-ticketing in
a telecom context, built on LangGraph with Pydantic-validated agent I/O and
a pluggable LLM provider abstraction (mock by default; Anthropic and Ollama
opt-in). See ``docs/DESIGN.md`` for the full specification.
"""

__version__ = "0.1.0"
__author__ = "Ajay Antony"
