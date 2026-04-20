"""Competitive intelligence schema and CRUD helpers.

All database access goes through `get_db()` from `database.py`.
Call `init_competitive_db()` once at startup to create all tables.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional, Union

from database import get_db

logger = logging.getLogger(__name__)


# ── Schema ─────────────────────────────────────────────────────────────────────

def init_competitive_db() -> None:
    """Create all 6 competitive-intelligence tables if they don't exist.

    Safe to call on every startup (idempotent via CREATE TABLE IF NOT EXISTS).
    """
    conn = get_db()
    c = conn.cursor()

    # competitors — master list of tracked competitors
    c.execute("""
        CREATE TABLE IF NOT EXISTS competitors (
            slug            TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            url             TEXT,
            tier            INTEGER DEFAULT 1,
            positioning     TEXT,
            url_ok          INTEGER DEFAULT 1,
            last_ingested   TEXT
        )
    """)

    # competitor_pages — crawled web pages per competitor
    c.execute("""
        CREATE TABLE IF NOT EXISTS competitor_pages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor_slug TEXT NOT NULL REFERENCES competitors(slug),
            url             TEXT NOT NULL,
            page_type       TEXT,
            lastmod         TEXT,
            content         TEXT,
            content_hash    TEXT,
            fetched_at      TEXT DEFAULT (datetime('now')),
            UNIQUE(competitor_slug, url)
        )
    """)

    # Migration: add content_hash column if table exists without it
    page_cols = {row[1] for row in c.execute("PRAGMA table_info(competitor_pages)").fetchall()}
    if "content_hash" not in page_cols:
        c.execute("ALTER TABLE competitor_pages ADD COLUMN content_hash TEXT")

    # competitor_news — news/press items per competitor
    c.execute("""
        CREATE TABLE IF NOT EXISTS competitor_news (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor_slug TEXT NOT NULL REFERENCES competitors(slug),
            title           TEXT,
            url             TEXT NOT NULL,
            source          TEXT,
            published_at    TEXT,
            snippet         TEXT,
            fetched_at      TEXT DEFAULT (datetime('now')),
            UNIQUE(competitor_slug, url)
        )
    """)

    # competitor_briefs — Claude-generated analysis snapshots
    c.execute("""
        CREATE TABLE IF NOT EXISTS competitor_briefs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor_slug TEXT NOT NULL REFERENCES competitors(slug),
            brief_json      TEXT NOT NULL,
            generated_at    TEXT DEFAULT (datetime('now')),
            model           TEXT
        )
    """)

    # competitor_trajectories — directional trajectory analysis
    c.execute("""
        CREATE TABLE IF NOT EXISTS competitor_trajectories (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor_slug TEXT NOT NULL REFERENCES competitors(slug),
            trajectory_json TEXT NOT NULL,
            generated_at    TEXT DEFAULT (datetime('now')),
            model           TEXT
        )
    """)

    # competitor_signals — discrete intelligence signals
    c.execute("""
        CREATE TABLE IF NOT EXISTS competitor_signals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor_slug TEXT NOT NULL REFERENCES competitors(slug),
            signal_type     TEXT,
            summary         TEXT,
            relevance       REAL,
            category        TEXT,
            source_url      TEXT,
            detected_at     TEXT DEFAULT (datetime('now'))
        )
    """)

    # pipeline_runs — cost and usage tracking per pipeline execution
    c.execute("""
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at            TEXT DEFAULT (datetime('now')),
            completed_at          TEXT,
            competitors_processed INTEGER DEFAULT 0,
            competitors_failed    INTEGER DEFAULT 0,
            pages_ingested        INTEGER DEFAULT 0,
            signals_classified    INTEGER DEFAULT 0,
            claude_calls          INTEGER DEFAULT 0,
            claude_input_tokens   INTEGER DEFAULT 0,
            claude_output_tokens  INTEGER DEFAULT 0,
            claude_cost_usd       REAL DEFAULT 0.0,
            status                TEXT DEFAULT 'running'
        )
    """)

    conn.commit()
    conn.close()
    logger.info("Competitive DB tables initialized.")


# ── Competitor CRUD ────────────────────────────────────────────────────────────

def upsert_competitor(
    slug: str,
    name: str,
    url: str,
    *,
    tier: int = 1,
    positioning: Optional[str] = None,
    url_ok: int = 1,
    last_ingested: Optional[str] = None,
) -> None:
    """Insert or update a competitor row."""
    conn = get_db()
    conn.execute(
        """
        INSERT INTO competitors (slug, name, url, tier, positioning, url_ok, last_ingested)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(slug) DO UPDATE SET
            name            = excluded.name,
            url             = excluded.url,
            tier            = excluded.tier,
            positioning     = excluded.positioning,
            url_ok          = excluded.url_ok,
            last_ingested   = excluded.last_ingested
        """,
        (slug, name, url, tier, positioning, url_ok, last_ingested),
    )
    conn.commit()
    conn.close()


def get_competitor(slug: str):
    """Return a single competitor row (sqlite3.Row) or None."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM competitors WHERE slug = ?", (slug,)
    ).fetchone()
    conn.close()
    return row


def get_all_competitors() -> list:
    """Return all competitor rows ordered by tier, then slug."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM competitors ORDER BY tier, slug"
    ).fetchall()
    conn.close()
    return list(rows)


def update_last_ingested(slug: str, timestamp: Optional[str] = None) -> None:
    """Set last_ingested for a competitor. Defaults to current UTC time."""
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    conn = get_db()
    conn.execute(
        "UPDATE competitors SET last_ingested = ? WHERE slug = ?",
        (ts, slug),
    )
    conn.commit()
    conn.close()


# ── Pages ──────────────────────────────────────────────────────────────────────

def save_page(
    competitor_slug: str,
    url: str,
    page_type: str,
    *,
    content: Optional[str] = None,
    content_hash: Optional[str] = None,
    lastmod: Optional[str] = None,
) -> bool:
    """Upsert a crawled page. Keyed on (competitor_slug, url).

    Returns True if this was a new page or content changed (hash differs),
    False if the content is unchanged.
    """
    conn = get_db()

    # Check existing hash for this page
    existing = conn.execute(
        "SELECT content_hash FROM competitor_pages WHERE competitor_slug = ? AND url = ?",
        (competitor_slug, url),
    ).fetchone()

    changed = True
    if existing is not None and content_hash is not None:
        old_hash = existing[0] if existing[0] else None
        if old_hash == content_hash:
            changed = False

    conn.execute(
        """
        INSERT INTO competitor_pages (competitor_slug, url, page_type, lastmod, content, content_hash)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(competitor_slug, url) DO UPDATE SET
            page_type    = excluded.page_type,
            lastmod      = excluded.lastmod,
            content      = excluded.content,
            content_hash = excluded.content_hash,
            fetched_at   = datetime('now')
        """,
        (competitor_slug, url, page_type, lastmod, content, content_hash),
    )
    conn.commit()
    conn.close()
    return changed


def get_pages(competitor_slug: str) -> list:
    """Return all crawled pages for a competitor."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM competitor_pages WHERE competitor_slug = ? ORDER BY fetched_at DESC",
        (competitor_slug,),
    ).fetchall()
    conn.close()
    return list(rows)


# ── News ───────────────────────────────────────────────────────────────────────

def save_news(
    competitor_slug: str,
    *,
    title: Optional[str] = None,
    url: str,
    source: Optional[str] = None,
    published_at: Optional[str] = None,
    snippet: Optional[str] = None,
) -> None:
    """Upsert a news item. Keyed on (competitor_slug, url)."""
    conn = get_db()
    conn.execute(
        """
        INSERT INTO competitor_news (competitor_slug, title, url, source, published_at, snippet)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(competitor_slug, url) DO UPDATE SET
            title       = excluded.title,
            source      = excluded.source,
            published_at= excluded.published_at,
            snippet     = excluded.snippet,
            fetched_at  = datetime('now')
        """,
        (competitor_slug, title, url, source, published_at, snippet),
    )
    conn.commit()
    conn.close()


def get_news(competitor_slug: str) -> list:
    """Return all news items for a competitor, newest first."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM competitor_news WHERE competitor_slug = ? ORDER BY published_at DESC, fetched_at DESC",
        (competitor_slug,),
    ).fetchall()
    conn.close()
    return list(rows)


# ── Briefs ─────────────────────────────────────────────────────────────────────

def save_brief(competitor_slug: str, brief: Union[dict, str], *, model: Optional[str] = None) -> None:
    """Append a new brief snapshot. `brief` can be a dict or JSON string."""
    brief_json = json.dumps(brief) if isinstance(brief, dict) else brief
    conn = get_db()
    conn.execute(
        "INSERT INTO competitor_briefs (competitor_slug, brief_json, model) VALUES (?, ?, ?)",
        (competitor_slug, brief_json, model),
    )
    conn.commit()
    conn.close()


def get_latest_brief(competitor_slug: str):
    """Return the most recent brief row or None."""
    conn = get_db()
    row = conn.execute(
        """
        SELECT * FROM competitor_briefs
        WHERE competitor_slug = ?
        ORDER BY generated_at DESC, id DESC
        LIMIT 1
        """,
        (competitor_slug,),
    ).fetchone()
    conn.close()
    return row


# ── Trajectories ───────────────────────────────────────────────────────────────

def save_trajectory(
    competitor_slug: str,
    trajectory: Union[dict, str],
    *,
    model: Optional[str] = None,
) -> None:
    """Append a new trajectory snapshot."""
    trajectory_json = json.dumps(trajectory) if isinstance(trajectory, dict) else trajectory
    conn = get_db()
    conn.execute(
        "INSERT INTO competitor_trajectories (competitor_slug, trajectory_json, model) VALUES (?, ?, ?)",
        (competitor_slug, trajectory_json, model),
    )
    conn.commit()
    conn.close()


def get_latest_trajectory(competitor_slug: str):
    """Return the most recent trajectory row or None."""
    conn = get_db()
    row = conn.execute(
        """
        SELECT * FROM competitor_trajectories
        WHERE competitor_slug = ?
        ORDER BY generated_at DESC, id DESC
        LIMIT 1
        """,
        (competitor_slug,),
    ).fetchone()
    conn.close()
    return row


# ── Signals ────────────────────────────────────────────────────────────────────

def save_signal(
    competitor_slug: str,
    signal_type: str,
    summary: str,
    relevance: float,
    category: str,
    source_url: Optional[str] = None,
) -> None:
    """Append a new competitive signal."""
    conn = get_db()
    conn.execute(
        """
        INSERT INTO competitor_signals (competitor_slug, signal_type, summary, relevance, category, source_url)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (competitor_slug, signal_type, summary, relevance, category, source_url),
    )
    conn.commit()
    conn.close()


def get_recent_signals(competitor_slug: str, *, limit: int = 50) -> list:
    """Return the most recent signals for a competitor."""
    conn = get_db()
    rows = conn.execute(
        """
        SELECT * FROM competitor_signals
        WHERE competitor_slug = ?
        ORDER BY detected_at DESC, id DESC
        LIMIT ?
        """,
        (competitor_slug, limit),
    ).fetchall()
    conn.close()
    return list(rows)


# ── Pipeline Runs ─────────────────────────────────────────────────────────────

def create_pipeline_run() -> int:
    """Insert a new pipeline_runs row and return its id."""
    conn = get_db()
    cursor = conn.execute("INSERT INTO pipeline_runs DEFAULT VALUES")
    run_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return run_id


def update_pipeline_run(run_id: int, **kwargs) -> None:
    """Update arbitrary fields on a pipeline_runs row."""
    if not kwargs:
        return
    set_clause = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [run_id]
    conn = get_db()
    conn.execute(
        f"UPDATE pipeline_runs SET {set_clause} WHERE id = ?",
        values,
    )
    conn.commit()
    conn.close()


def get_latest_pipeline_run() -> Optional[dict]:
    """Return the most recent pipeline_runs row as a dict, or None."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM pipeline_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return dict(row)
