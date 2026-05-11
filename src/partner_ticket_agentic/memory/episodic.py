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

DEFAULT_DB_PATH = Path.home() / ".ptag" / "episodic.db"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodic_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    partner_id  TEXT NOT NULL,
    ticket_id   TEXT NOT NULL,
    category    TEXT NOT NULL,
    urgency     TEXT,
    summary     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_episodic_partner ON episodic_entries(partner_id);
CREATE INDEX IF NOT EXISTS idx_episodic_partner_ticket
    ON episodic_entries(partner_id, ticket_id);
"""


@dataclass(frozen=True, slots=True)
class EpisodicEntry:
    """One row in the episodic-memory table."""

    id: int
    partner_id: str
    ticket_id: str
    category: str
    urgency: str | None
    summary: str
    created_at: str


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
    ) -> EpisodicEntry:
        """Append a new episodic entry, returning the persisted row."""

        cur = self._conn.execute(
            "INSERT INTO episodic_entries (partner_id, ticket_id, category, urgency, summary) "
            "VALUES (?, ?, ?, ?, ?)",
            (partner_id, ticket_id, category, urgency, summary),
        )
        row_id = cur.lastrowid
        row = self._conn.execute(
            "SELECT id, partner_id, ticket_id, category, urgency, summary, created_at "
            "FROM episodic_entries WHERE id = ?",
            (row_id,),
        ).fetchone()
        self._conn.commit()
        return _row_to_entry(row)

    # ---- reads --------------------------------------------------------------

    def recent(self, partner_id: str, limit: int = 10) -> list[EpisodicEntry]:
        """Return the ``limit`` most recent entries for ``partner_id``."""

        rows = self._conn.execute(
            "SELECT id, partner_id, ticket_id, category, urgency, summary, created_at "
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
    return EpisodicEntry(
        id=int(row["id"]),
        partner_id=str(row["partner_id"]),
        ticket_id=str(row["ticket_id"]),
        category=str(row["category"]),
        urgency=str(row["urgency"]) if row["urgency"] is not None else None,
        summary=str(row["summary"]),
        created_at=str(row["created_at"]),
    )
