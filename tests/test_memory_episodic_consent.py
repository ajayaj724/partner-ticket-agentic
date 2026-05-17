"""GDPR consent schema tests for the episodic memory store.

Pins the v1.1 contract from REQ GDPR-01:

* New rows can carry ``consent_recorded_at`` (ISO 8601 timestamp) and
  ``legal_basis`` (one of ``legitimate_interest`` / ``contract`` / ``consent``).
* Pre-v1.1 databases — created without the GDPR columns — upgrade in place
  via the ``_migrate_add_gdpr_columns`` helper without dropping any rows.
* ``_row_to_entry`` tolerates rows that lack the GDPR columns (defensive
  behaviour for callers that hand-craft rows in tests).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from partner_ticket_agentic.memory.episodic import (
    EpisodicStore,
    _migrate_add_gdpr_columns,
    _row_to_entry,
)


def test_record_round_trip_with_consent_fields() -> None:
    """A fresh DB writes and reads back both GDPR fields cleanly."""

    store = EpisodicStore(":memory:")
    try:
        ts = datetime.now(UTC).isoformat()
        entry = store.record(
            partner_id="P-BRU-01",
            ticket_id="T-001",
            category="circuit_down",
            summary="circuit CIRC-1 down 09:14",
            urgency="critical",
            consent_recorded_at=ts,
            legal_basis="contract",
        )
        assert entry.consent_recorded_at == ts
        assert entry.legal_basis == "contract"

        recent = store.recent("P-BRU-01", limit=5)
        assert len(recent) == 1
        assert recent[0].consent_recorded_at == ts
        assert recent[0].legal_basis == "contract"
    finally:
        store.close()


def test_record_without_consent_fields_persists_nulls() -> None:
    """Pre-v1.1 callers that don't pass the GDPR fields still work; the row
    persists with NULLs in both columns. This is the migration-window
    compatibility contract."""

    store = EpisodicStore(":memory:")
    try:
        entry = store.record(
            partner_id="P-LEGACY-01",
            ticket_id="T-002",
            category="billing_dispute",
            summary="invoice INV-9912 dispute",
        )
        assert entry.consent_recorded_at is None
        assert entry.legal_basis is None
    finally:
        store.close()


def test_legal_basis_accepts_all_three_lawful_bases() -> None:
    """All three Article-6 lawful bases relevant to partner records round-trip."""

    store = EpisodicStore(":memory:")
    try:
        for basis in ("legitimate_interest", "contract", "consent"):
            entry = store.record(
                partner_id=f"P-{basis}",
                ticket_id=f"T-{basis}",
                category="other",
                summary=f"legal_basis={basis}",
                legal_basis=basis,
            )
            assert entry.legal_basis == basis
    finally:
        store.close()


def test_legal_basis_invalid_value_is_dropped_on_read() -> None:
    """A row written directly with a non-allowlisted basis (e.g. via a manual
    SQL fix-up or a future-schema migration in progress) reads back as
    ``None`` rather than as the raw string. Defensive against a downstream
    consumer trusting the typed Literal."""

    store = EpisodicStore(":memory:")
    try:
        # Write directly with an invalid value to simulate a hand-edit.
        store._conn.execute(
            "INSERT INTO episodic_entries "
            "(partner_id, ticket_id, category, urgency, summary, legal_basis) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("P-X", "T-X", "other", None, "manual fix-up", "BOGUS_VALUE"),
        )
        store._conn.commit()
        entries = store.recent("P-X", limit=1)
        assert len(entries) == 1
        # The row exists, but legal_basis is sanitised to None on read.
        assert entries[0].legal_basis is None
    finally:
        store.close()


def test_migrate_adds_columns_to_pre_v1_1_database() -> None:
    """A SQLite DB created with the v1.0 schema (without GDPR columns)
    upgrades in place without losing rows."""

    # Build a pre-v1.1 DB by hand: the old DDL.
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE episodic_entries (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            partner_id  TEXT NOT NULL,
            ticket_id   TEXT NOT NULL,
            category    TEXT NOT NULL,
            urgency     TEXT,
            summary     TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );
        """
    )
    conn.execute(
        "INSERT INTO episodic_entries (partner_id, ticket_id, category, summary) "
        "VALUES ('P-OLD', 'T-OLD', 'other', 'pre-migration row')"
    )
    conn.commit()
    # Pre-migration: only the original columns exist.
    cols_before = {r["name"] for r in conn.execute("PRAGMA table_info(episodic_entries)")}
    assert "consent_recorded_at" not in cols_before
    assert "legal_basis" not in cols_before

    # Run migration.
    added = _migrate_add_gdpr_columns(conn)
    assert set(added) == {"consent_recorded_at", "legal_basis"}

    # Post-migration: columns exist; original row survives with NULLs.
    cols_after = {r["name"] for r in conn.execute("PRAGMA table_info(episodic_entries)")}
    assert "consent_recorded_at" in cols_after
    assert "legal_basis" in cols_after

    row = conn.execute("SELECT * FROM episodic_entries WHERE ticket_id = 'T-OLD'").fetchone()
    assert row["partner_id"] == "P-OLD"
    assert row["consent_recorded_at"] is None
    assert row["legal_basis"] is None

    conn.close()


def test_migrate_is_idempotent() -> None:
    """Running the migration twice does not error or duplicate columns."""

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE episodic_entries (id INTEGER PRIMARY KEY, partner_id TEXT, "
        "ticket_id TEXT, category TEXT, urgency TEXT, summary TEXT, created_at TEXT);"
    )
    first = _migrate_add_gdpr_columns(conn)
    second = _migrate_add_gdpr_columns(conn)
    assert set(first) == {"consent_recorded_at", "legal_basis"}
    assert second == []
    conn.close()


def test_migrate_on_empty_db_is_noop() -> None:
    """Migration on a DB without the table is a no-op — the schema apply
    will create the table with the GDPR columns built in."""

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    added = _migrate_add_gdpr_columns(conn)
    assert added == []
    conn.close()


def test_row_to_entry_tolerates_missing_columns() -> None:
    """``_row_to_entry`` accepts rows without the GDPR columns (legacy
    callers that build rows by hand)."""

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE legacy (id INTEGER PRIMARY KEY, partner_id TEXT, "
        "ticket_id TEXT, category TEXT, urgency TEXT, summary TEXT, created_at TEXT);"
    )
    conn.execute(
        "INSERT INTO legacy VALUES (1, 'P', 'T', 'circuit_down', 'critical', 's', '2026-01-01')"
    )
    row = conn.execute("SELECT * FROM legacy").fetchone()
    entry = _row_to_entry(row)
    assert entry.partner_id == "P"
    assert entry.consent_recorded_at is None
    assert entry.legal_basis is None
    conn.close()


@pytest.mark.parametrize(
    "basis",
    ["legitimate_interest", "contract", "consent"],
)
def test_parametrised_legal_basis_persistence(basis: str) -> None:
    """Quick parametrised sanity that every accepted basis persists and reads."""

    store = EpisodicStore(":memory:")
    try:
        entry = store.record(
            partner_id="P-PARAM",
            ticket_id=f"T-{basis}",
            category="other",
            summary="parametrised",
            legal_basis=basis,
        )
        assert entry.legal_basis == basis
    finally:
        store.close()
