"""Episodic memory tier — per-partner, persisted across runs in SQLite.

Stores compact summaries of past ticket flows keyed by ``partner_id``: the
ticket ID, category, urgency, resolution, and a short rationale string.
Used by the Enricher (F2) to answer "what does this partner usually look
like?" without re-running prior pipelines.

The default DB path lives at ``~/.ptag/episodic.db`` per DESIGN.md §4.2.
The store is intentionally tiny — one table, four indexed columns, no
ORM. SQLite's stdlib bindings are enough; pulling in SQLAlchemy would be
abstraction tax for no benefit at this scale.
"""

from __future__ import annotations

import contextlib
import sqlite3
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DEFAULT_DB_PATH = Path.home() / ".ptag" / "episodic.db"

LegalBasis = Literal["legitimate_interest", "contract", "consent"]
"""GDPR Article 6 lawful-bases relevant to partner records.

* ``legitimate_interest`` — operator's normal partner-ops processing
* ``contract`` — performance of the partner contract
* ``consent`` — explicit consent capture (rare in B2B; included for completeness)

Other Article 6 bases (vital_interest, public_task, legal_obligation) are not
expected for partner-ticket records; if a record needs one of those, the
schema needs to be extended deliberately rather than coercing it into one of
the three above.
"""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodic_entries (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    partner_id           TEXT NOT NULL,
    ticket_id            TEXT NOT NULL,
    category             TEXT NOT NULL,
    urgency              TEXT,
    summary              TEXT NOT NULL,
    consent_recorded_at  TEXT,
    legal_basis          TEXT,
    created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_episodic_partner ON episodic_entries(partner_id);
CREATE INDEX IF NOT EXISTS idx_episodic_partner_ticket
    ON episodic_entries(partner_id, ticket_id);
"""

# GDPR consent columns added 2026-05-17 per REQ GDPR-01. Existing rows are
# allowed NULL on both columns; new rows SHOULD populate both (enforced at
# the caller boundary in `EpisodicStore.record`, not at the SQL layer, so a
# legacy migration can still write rows during a backfill).
_GDPR_COLUMNS: tuple[tuple[str, str], ...] = (
    ("consent_recorded_at", "TEXT"),
    ("legal_basis", "TEXT"),
)


def _migrate_add_gdpr_columns(conn: sqlite3.Connection) -> list[str]:
    """Idempotently add the GDPR consent columns to a pre-v1.1 database.

    Returns the list of column names that were added (empty if the DB was
    already on schema v1.1+). Called from ``EpisodicStore.__init__`` before
    the schema apply, so an existing DB upgrades in place without losing rows.
    """

    existing = {row["name"] for row in conn.execute("PRAGMA table_info(episodic_entries)")}
    if not existing:
        # Table doesn't exist yet — schema apply will create it with the new
        # columns. Nothing to migrate.
        return []
    added: list[str] = []
    for col_name, col_type in _GDPR_COLUMNS:
        if col_name not in existing:
            conn.execute(f"ALTER TABLE episodic_entries ADD COLUMN {col_name} {col_type}")
            added.append(col_name)
    if added:
        conn.commit()
    return added


@dataclass(frozen=True, slots=True)
class EpisodicEntry:
    """One row in the episodic-memory table.

    GDPR consent columns (``consent_recorded_at``, ``legal_basis``) are
    optional on read for backward compatibility with pre-v1.1 rows. New rows
    written via :meth:`EpisodicStore.record` SHOULD populate ``legal_basis``;
    the caller decides whether ``consent_recorded_at`` is the partner's
    onboarding timestamp or a fresh consent event.
    """

    id: int
    partner_id: str
    ticket_id: str
    category: str
    urgency: str | None
    summary: str
    created_at: str
    consent_recorded_at: str | None = None
    legal_basis: LegalBasis | None = None


class EpisodicStore:
    """SQLite-backed per-partner episodic store.

    Construct with the default path (``~/.ptag/episodic.db``) for normal
    runs, or with ``":memory:"`` for tests. The constructor creates the
    directory and applies the schema idempotently. A single connection is
    held for the lifetime of the store: ``:memory:`` databases are tied to
    the connection that opens them, so re-opening would discard the table
    between calls.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            db_path = DEFAULT_DB_PATH
        self._path = str(db_path)
        if self._path != ":memory:":
            Path(self._path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        self._conn.row_factory = sqlite3.Row
        # Migrate pre-v1.1 databases in place: add the GDPR consent columns
        # if the existing table is missing them. Runs BEFORE the schema-apply
        # so a freshly-created DB skips the migration cleanly and an existing
        # DB upgrades without dropping rows.
        _migrate_add_gdpr_columns(self._conn)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        # Python 3.13 raises ResourceWarning when an sqlite3.Connection is
        # GC'd without an explicit close(); the warning is attributed to
        # whichever test runs next, which is the same as leaking. Attach
        # a weakref.finalize so the connection closes deterministically
        # when the store object is collected. Callers that want eager
        # closure can still use the context-manager (__enter__ / __exit__)
        # or close() — both win over the finalizer.
        self._finalizer = weakref.finalize(self, _safe_close, self._conn)

    def close(self) -> None:
        self._finalizer.detach()
        with contextlib.suppress(sqlite3.ProgrammingError):
            self._conn.close()

    def __enter__(self) -> EpisodicStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ---- writes -------------------------------------------------------------

    def record(
        self,
        *,
        partner_id: str,
        ticket_id: str,
        category: str,
        summary: str,
        urgency: str | None = None,
        consent_recorded_at: str | None = None,
        legal_basis: LegalBasis | None = None,
    ) -> EpisodicEntry:
        """Append a new episodic entry, returning the persisted row.

        ``consent_recorded_at`` and ``legal_basis`` are GDPR audit fields
        (REQ GDPR-01). Optional for backward compatibility with pre-v1.1
        callers; production deployments SHOULD populate ``legal_basis``.
        """

        cur = self._conn.execute(
            "INSERT INTO episodic_entries "
            "(partner_id, ticket_id, category, urgency, summary, "
            "consent_recorded_at, legal_basis) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                partner_id,
                ticket_id,
                category,
                urgency,
                summary,
                consent_recorded_at,
                legal_basis,
            ),
        )
        row_id = cur.lastrowid
        row = self._conn.execute(
            "SELECT id, partner_id, ticket_id, category, urgency, summary, "
            "consent_recorded_at, legal_basis, created_at "
            "FROM episodic_entries WHERE id = ?",
            (row_id,),
        ).fetchone()
        self._conn.commit()
        return _row_to_entry(row)

    # ---- reads --------------------------------------------------------------

    def recent(self, partner_id: str, limit: int = 10) -> list[EpisodicEntry]:
        """Return the ``limit`` most recent entries for ``partner_id``."""

        rows = self._conn.execute(
            "SELECT id, partner_id, ticket_id, category, urgency, summary, "
            "consent_recorded_at, legal_basis, created_at "
            "FROM episodic_entries WHERE partner_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (partner_id, limit),
        ).fetchall()
        return [_row_to_entry(r) for r in rows]

    def count(self, partner_id: str | None = None) -> int:
        """Return the number of entries — total, or for one partner."""

        if partner_id is None:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM episodic_entries").fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM episodic_entries WHERE partner_id = ?",
                (partner_id,),
            ).fetchone()
        return int(row["n"])


def _safe_close(conn: sqlite3.Connection) -> None:
    """Close a SQLite connection, swallowing ProgrammingError on double-close."""

    with contextlib.suppress(sqlite3.ProgrammingError):
        conn.close()


def _row_to_entry(row: sqlite3.Row) -> EpisodicEntry:
    # `sqlite3.Row` raises IndexError on missing keys; the GDPR columns may
    # be absent in callers that hand-craft rows for tests. Use the mapping
    # interface defensively.
    keys = set(row.keys())
    consent_recorded_at = (
        str(row["consent_recorded_at"])
        if "consent_recorded_at" in keys and row["consent_recorded_at"] is not None
        else None
    )
    legal_basis_raw = (
        row["legal_basis"] if "legal_basis" in keys and row["legal_basis"] is not None else None
    )
    legal_basis: LegalBasis | None = (
        legal_basis_raw
        if legal_basis_raw in {"legitimate_interest", "contract", "consent"}
        else None
    )
    return EpisodicEntry(
        id=int(row["id"]),
        partner_id=str(row["partner_id"]),
        ticket_id=str(row["ticket_id"]),
        category=str(row["category"]),
        urgency=str(row["urgency"]) if row["urgency"] is not None else None,
        summary=str(row["summary"]),
        created_at=str(row["created_at"]),
        consent_recorded_at=consent_recorded_at,
        legal_basis=legal_basis,
    )
