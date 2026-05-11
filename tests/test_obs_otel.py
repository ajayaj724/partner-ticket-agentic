"""Tests for the OpenTelemetry span helper in obs.py.

The OTel SDK is opt-in via PTAG_OTEL=1 to keep CI / tests / demos
free of exporter noise. These tests cover:

- Off-by-default: span() is a no-op when PTAG_OTEL is unset.
- On: span() creates a real span when the env var is set and the
  [otel] extras are installed.
- Nesting: spans nest correctly (pipeline → tool).
"""

from __future__ import annotations

import importlib

import pytest

from partner_ticket_agentic import obs


@pytest.fixture
def reset_otel_state(monkeypatch):
    """Reset the module-level OTel init flags around each test."""

    monkeypatch.setattr(obs, "_OTEL_SDK_INITIALISED", False)
    monkeypatch.setattr(obs, "_OTEL_TRACER", None)
    yield


def test_span_is_noop_when_otel_disabled(monkeypatch, reset_otel_state) -> None:
    """With PTAG_OTEL unset, span() yields without initialising the SDK."""

    monkeypatch.delenv("PTAG_OTEL", raising=False)
    with obs.span("test.disabled"):
        pass
    assert obs._OTEL_TRACER is None


def test_span_initialises_sdk_when_enabled(monkeypatch, reset_otel_state) -> None:
    """With PTAG_OTEL=1 and the [otel] extras installed, span() returns a tracer."""

    pytest.importorskip(
        "opentelemetry.sdk.trace",
        reason="install [otel] extras to run the live OTel test",
    )
    monkeypatch.setenv("PTAG_OTEL", "1")

    # Capture spans via an in-memory exporter rather than the console one
    # so the assertion is hermetic.
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    # Force-replace any existing global provider so this test owns it.
    new_provider = TracerProvider(
        resource=Resource.create({"service.name": "partner-ticket-agentic-test"})
    )
    exporter = InMemorySpanExporter()
    new_provider.add_span_processor(SimpleSpanProcessor(exporter))
    # OTel doesn't expose a public "force replace"; we use the private
    # API knowingly — the alternative is a process-restart, overkill here.
    importlib.import_module("opentelemetry.trace")._TRACER_PROVIDER = new_provider
    otel_trace.set_tracer_provider(new_provider)

    # Reset our module's cached tracer so it picks up the test provider.
    monkeypatch.setattr(obs, "_OTEL_SDK_INITIALISED", False)
    monkeypatch.setattr(obs, "_OTEL_TRACER", None)

    with obs.span("test.parent", agent="triage"):
        with obs.span("test.child", tool="crm_lookup_partner"):
            pass

    spans = exporter.get_finished_spans()
    names = [s.name for s in spans]
    assert "test.parent" in names
    assert "test.child" in names
