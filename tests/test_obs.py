"""Tests for the structured-logging / trace-context plumbing."""

from __future__ import annotations

import json

from partner_ticket_agentic.obs import (
    bind_log_context,
    get_logger,
    new_trace_id,
    trace_collector,
)


def test_new_trace_id_is_16_hex_chars() -> None:
    tid = new_trace_id()
    assert len(tid) == 16
    int(tid, 16)  # parses as hex


def test_logger_emits_json_lines_with_bound_context() -> None:
    # The project logger has propagate=False, so capsys/capfd/caplog don't
    # see its output. The public trace_collector() API uses the same
    # _JsonLineFormatter under the hood, so going through it exercises the
    # full path — log call → formatter → JSON dict — that the CLI relies on
    # for --export-trace.
    log = get_logger("agents.triage")
    with trace_collector() as buf, bind_log_context(trace_id="abc123", ticket_id="sample-1"):
        log.info("hello", extra={"agent": "triage", "step": "llm_call"})
    assert buf, "expected at least one captured record"
    line = buf[-1]
    # Round-trip through json to verify the formatter emits a valid JSON
    # object (trace_collector parses it back, but the assertion below pins
    # the contract that the dict shape matches).
    json.dumps(line)
    assert line["trace_id"] == "abc123"
    assert line["ticket_id"] == "sample-1"
    assert line["agent"] == "triage"
    assert line["step"] == "llm_call"
    assert line["message"] == "hello"


def test_trace_collector_captures_records() -> None:
    log = get_logger("agents.router")
    with trace_collector() as buf, bind_log_context(trace_id="t-1"):
        log.info("decided", extra={"queue": "NOC-L2"})
        log.warning("degraded")
    assert len(buf) == 2
    assert buf[0]["queue"] == "NOC-L2"
    assert buf[1]["level"] == "WARNING"
    assert all(rec["trace_id"] == "t-1" for rec in buf)


def test_bind_context_is_scoped() -> None:
    log = get_logger("agents.triage")
    with trace_collector() as lines, bind_log_context(trace_id="outer"):
        log.info("a")
        with bind_log_context(ticket_id="t-2"):
            log.info("b")
        log.info("c")
    assert len(lines) == 3
    assert lines[0]["trace_id"] == "outer" and "ticket_id" not in lines[0]
    assert lines[1]["trace_id"] == "outer" and lines[1]["ticket_id"] == "t-2"
    assert lines[2]["trace_id"] == "outer" and "ticket_id" not in lines[2]
