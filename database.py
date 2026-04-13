"""
database.py
SQLite schema and connection management for MEDDIC Engine.
All tables created here. Import get_db() wherever you need a connection.
"""

import sqlite3
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "data/.db")


def get_db():
    """Return a SQLite connection with row_factory set to Row."""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create all tables if they don't exist. Safe to call on every startup."""
    conn = get_db()
    c = conn.cursor()

    # ── firms ─────────────────────────────────────────────────────────────────
    # The universe of target financial institutions.
    c.execute("""
        CREATE TABLE IF NOT EXISTS firms (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL,
            domain          TEXT,
            firm_type       TEXT,   -- pe | hedge_fund | investment_bank | credit | law_firm
            tier            INTEGER DEFAULT 1, -- 1 active | 2 monitored | 3 cold
            aum_range       TEXT,   -- e.g. "$1B-$10B"
            geography       TEXT,   -- US | UK | Europe
            _status   TEXT DEFAULT 'prospect', -- customer | prospect | evaluating | rogo | build_own
            competitor      TEXT,   -- alphasense | rogo | bloomberg | stack_ai | budget | none
            buying_stage    TEXT,   -- deploying | evaluating | exploring | unknown
            has_objections  INTEGER DEFAULT 0,  -- 1 if AI wrapper / compliance objection detected
            notes           TEXT,
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    # ── contacts ──────────────────────────────────────────────────────────────
    # Named decision makers at target firms.
    c.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            firm_id         INTEGER NOT NULL REFERENCES firms(id),
            name            TEXT NOT NULL,
            title           TEXT,
            role_type       TEXT,   -- executive_sponsor | technical_champion | both
            email           TEXT,
            email_verified  INTEGER DEFAULT 0,  -- 1 if Hunter.io verified
            email_source    TEXT,   -- hunter | pattern | linkedin | manual
            linkedin_url    TEXT,
            twitter_handle  TEXT,
            notes           TEXT,
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    # ── signals ───────────────────────────────────────────────────────────────
    # Raw signals collected from Twitter, LinkedIn, Exa press, hiring.
    c.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            firm_id         INTEGER REFERENCES firms(id),
            contact_id      INTEGER REFERENCES contacts(id),
            signal_type     TEXT NOT NULL,  -- twitter | linkedin | press | hiring | manual
            signal_subtype  TEXT,           -- pain | evaluation | transformation | competitor_frustration
            content         TEXT,           -- full post/article text or excerpt
            source_url      TEXT,
            author_handle   TEXT,           -- @handle for social signals
            author_name     TEXT,
            signal_date     TEXT,           -- ISO date of the original signal
            freshness_days  INTEGER,        -- days since signal (computed on insert)
            buying_stage    TEXT,           -- deploying | evaluating | exploring (from content analysis)
            raw_data        TEXT,           -- full JSON from API response
            created_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    # ── scores ────────────────────────────────────────────────────────────────
    # Scoring results per contact. One row per scoring run.
    c.execute("""
        CREATE TABLE IF NOT EXISTS scores (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id      INTEGER NOT NULL REFERENCES contacts(id),
            firm_id         INTEGER NOT NULL REFERENCES firms(id),
            score           REAL,           -- 0-100 final score
            icp_fit         REAL,           -- 0-100 dimension score
            ai_readiness    REAL,           -- 0-100 dimension score
            reachability    REAL,           -- 0-100 dimension score
            signal_freshness REAL,          -- 0-100 dimension score
            label           TEXT,           -- Strong Match | Good Match | Moderate | Weak
            action          TEXT,           -- Approve | Review | Enrich | Flag
            reasoning       TEXT,           -- Claude's 2-3 sentence explanation
            missing         TEXT,           -- what would increase score
            sections_used   TEXT,           -- JSON list of skill sections injected
            scored_at       TEXT DEFAULT (datetime('now'))
        )
    """)

    # ── outreach_queue ────────────────────────────────────────────────────────
    # Contacts ready for review — one row per contact, updated as status changes.
    c.execute("""
        CREATE TABLE IF NOT EXISTS outreach_queue (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id      INTEGER NOT NULL REFERENCES contacts(id),
            firm_id         INTEGER NOT NULL REFERENCES firms(id),
            score_id        INTEGER REFERENCES scores(id),
            status          TEXT DEFAULT 'pending',  -- pending | approved | skipped | flagged
            first_line      TEXT,           -- Claude-generated personalization
            first_line_edited TEXT,         -- Rep-edited version (if different)
            signal_id       INTEGER REFERENCES signals(id),  -- triggering signal
            skip_reason     TEXT,           -- if skipped: wrong_person | bad_timing | in_pipeline | other
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    # ── review_decisions ──────────────────────────────────────────────────────
    # Audit log of every approve/skip/edit decision. Never deleted.
    c.execute("""
        CREATE TABLE IF NOT EXISTS review_decisions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            queue_id        INTEGER NOT NULL REFERENCES outreach_queue(id),
            contact_id      INTEGER NOT NULL REFERENCES contacts(id),
            decision        TEXT NOT NULL,  -- approved | skipped | edited | flagged
            original_line   TEXT,
            edited_line     TEXT,
            skip_reason     TEXT,
            decided_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    # ── scoring_decisions ─────────────────────────────────────────────────────
    # Log every scoring call for feedback loop. Never deleted.
    c.execute("""
        CREATE TABLE IF NOT EXISTS scoring_decisions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id      INTEGER NOT NULL REFERENCES contacts(id),
            firm_id         INTEGER NOT NULL REFERENCES firms(id),
            score           REAL,
            sections_used   TEXT,   -- JSON list
            signal_count    INTEGER,
            decided_at      TEXT DEFAULT (datetime('now'))
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS hunter_cache (
            name_domain     TEXT PRIMARY KEY,   -- "name|domain", lowercase
            email           TEXT,
            score           INTEGER,
            verified        INTEGER,
            source          TEXT,
            cached_at       TEXT
        )
    """)
    for idx_sql in (
        "CREATE INDEX IF NOT EXISTS idx_signals_date      ON signals(signal_date)",
        "CREATE INDEX IF NOT EXISTS idx_contacts_verified ON contacts(email_verified)",
        "CREATE INDEX IF NOT EXISTS idx_scores_score      ON scores(score)",
        "CREATE INDEX IF NOT EXISTS idx_firms_tier        ON firms(tier)",
    ):
        c.execute(idx_sql)

    # ── idempotent migrations ─────────────────────────────────────────────────
    # Add firms.tier if missing (default 1 for backfill).
    firm_cols = {row[1] for row in c.execute("PRAGMA table_info(firms)").fetchall()}
    if "tier" not in firm_cols:
        c.execute("ALTER TABLE firms ADD COLUMN tier INTEGER DEFAULT 1")
    if "aum_reported" not in firm_cols:
        c.execute("ALTER TABLE firms ADD COLUMN aum_reported REAL")
    # Backfill any NULL tier to 1.
    c.execute("UPDATE firms SET tier = 1 WHERE tier IS NULL")

    # Add scores.account_brief (already present in prod DB) and scores.scored_by.
    score_cols = {row[1] for row in c.execute("PRAGMA table_info(scores)").fetchall()}
    if "account_brief" not in score_cols:
        c.execute("ALTER TABLE scores ADD COLUMN account_brief TEXT")
    if "scored_by" not in score_cols:
        c.execute("ALTER TABLE scores ADD COLUMN scored_by TEXT DEFAULT 'claude'")

    # Add contacts.is_placeholder so synthetic tier-2 firm rows can be excluded
    # from outreach flows.
    contact_cols = {row[1] for row in c.execute("PRAGMA table_info(contacts)").fetchall()}
    if "is_placeholder" not in contact_cols:
        c.execute("ALTER TABLE contacts ADD COLUMN is_placeholder INTEGER DEFAULT 0")
    if "do_not_contact" not in contact_cols:
        c.execute("ALTER TABLE contacts ADD COLUMN do_not_contact INTEGER DEFAULT 0")

    conn.commit()
    conn.close()
    logger.info(f"Database initialized at {DB_PATH}")


if __name__ == "__main__":
    init_db()
    print(f"✓ Database initialized at {DB_PATH}")
