#!/usr/bin/env python3
"""scripts/migrate_fk_cascade.py

Recreate `outreach_queue` so its score_id FK is `ON DELETE SET NULL`.

Why: today, deleting a row from `scores` violates the FK and either fails or
leaves dangling queue rows pointing at a missing score. The only way to change
a column constraint in SQLite is table rebuild.

Procedure (one transaction):
    1. PRAGMA foreign_keys = OFF (must be off during rebuild)
    2. CREATE TABLE outreach_queue_new (... score_id INTEGER REFERENCES scores(id) ON DELETE SET NULL ...)
    3. INSERT INTO outreach_queue_new SELECT * FROM outreach_queue
    4. DROP TABLE outreach_queue
    5. ALTER TABLE outreach_queue_new RENAME TO outreach_queue
    6. PRAGMA foreign_keys = ON  (and verify integrity)

Idempotent: skips if the target FK is already SET NULL.

Run:
    python3 scripts/migrate_fk_cascade.py
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from database import get_db


NEW_TABLE_SQL = """
CREATE TABLE outreach_queue_new (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id        INTEGER NOT NULL REFERENCES contacts(id),
    firm_id           INTEGER NOT NULL REFERENCES firms(id),
    score_id          INTEGER REFERENCES scores(id) ON DELETE SET NULL,
    status            TEXT DEFAULT 'pending',
    first_line        TEXT,
    first_line_edited TEXT,
    signal_id         INTEGER REFERENCES signals(id),
    skip_reason       TEXT,
    created_at        TEXT DEFAULT (datetime('now')),
    updated_at        TEXT DEFAULT (datetime('now'))
)
"""


def _already_migrated(conn) -> bool:
    """Heuristic: inspect sqlite_master DDL for the SET NULL clause."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='outreach_queue'"
    ).fetchone()
    if not row:
        return False
    sql = (row["sql"] or "").upper()
    return "ON DELETE SET NULL" in sql


def migrate() -> int:
    conn = get_db()
    try:
        if _already_migrated(conn):
            print("✓ outreach_queue already has ON DELETE SET NULL — nothing to do")
            return 0

        existing = conn.execute("SELECT COUNT(*) FROM outreach_queue").fetchone()[0]
        print(f"  Current outreach_queue rows: {existing}")

        # FKs must be OFF during rebuild, but we still want a transactional
        # boundary. PRAGMA foreign_keys cannot be set inside a transaction.
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute("BEGIN")
            conn.execute(NEW_TABLE_SQL)
            conn.execute("""
                INSERT INTO outreach_queue_new
                  (id, contact_id, firm_id, score_id, status, first_line,
                   first_line_edited, signal_id, skip_reason, created_at, updated_at)
                SELECT
                   id, contact_id, firm_id, score_id, status, first_line,
                   first_line_edited, signal_id, skip_reason, created_at, updated_at
                FROM outreach_queue
            """)
            conn.execute("DROP TABLE outreach_queue")
            conn.execute("ALTER TABLE outreach_queue_new RENAME TO outreach_queue")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")

        # Integrity check
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            print(f"✗ FK violations after migration: {violations}")
            return 1

        after = conn.execute("SELECT COUNT(*) FROM outreach_queue").fetchone()[0]
        print(f"✓ Migration complete — {after} rows preserved (was {existing})")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(migrate())
