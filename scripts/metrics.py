"""scripts/metrics.py — single source of truth for all dashboard numbers.

All three updaters (update_dashboard.py, update_analytics.py, update_ops.py)
import from here. Add a metric here once; every page stays in sync.

Conventions:
- Default scope is tier=1 firms. Pass tier=None to widen.
- Use MAX(score) per contact when aggregating, to avoid duplicate score rows
  inflating counts/averages.
"""

from __future__ import annotations


# ─── Contact universe ────────────────────────────────────────────────────────

def total_contacts(conn, tier: int | None = 1) -> int:
    if tier is None:
        return conn.execute("SELECT count(*) FROM contacts").fetchone()[0]
    return conn.execute("""
        SELECT count(*) FROM contacts c
        JOIN firms f ON c.firm_id = f.id
        WHERE f.tier = ?
    """, (tier,)).fetchone()[0]


def contacts_verified(conn) -> int:
    """Tier-1 contacts with verified email."""
    return conn.execute("""
        SELECT count(*) FROM contacts c
        JOIN firms f ON c.firm_id = f.id
        WHERE f.tier = 1 AND c.email_verified = 1
    """).fetchone()[0]


def contacts_need_email(conn) -> int:
    """Tier-1 contacts scored but missing verified email."""
    return conn.execute("""
        SELECT count(DISTINCT c.id) FROM contacts c
        JOIN firms f ON c.firm_id = f.id
        JOIN scores s ON s.contact_id = c.id
        WHERE f.tier = 1
        AND (c.email IS NULL OR c.email_verified = 0)
    """).fetchone()[0]


def contacts_ready(conn) -> int:
    """Tier-1, score>=55, verified email — the actionable pool."""
    return conn.execute("""
        SELECT count(DISTINCT c.id)
        FROM contacts c
        JOIN firms f ON c.firm_id = f.id
        JOIN (SELECT contact_id, MAX(score) AS score
              FROM scores GROUP BY contact_id) s ON s.contact_id = c.id
        WHERE f.tier = 1
        AND c.email_verified = 1
        AND s.score >= 55
    """).fetchone()[0]


def contacts_strong_match(conn) -> int:
    """Tier-1, score>=75, verified email."""
    return conn.execute("""
        SELECT count(DISTINCT c.id)
        FROM contacts c
        JOIN firms f ON c.firm_id = f.id
        JOIN (SELECT contact_id, MAX(score) AS score
              FROM scores GROUP BY contact_id) s ON s.contact_id = c.id
        WHERE f.tier = 1
        AND c.email_verified = 1
        AND s.score >= 75
    """).fetchone()[0]


def signal_gaps(conn) -> int:
    """Tier-1 contacts whose firm has no signals attached."""
    return conn.execute("""
        SELECT count(DISTINCT c.id)
        FROM contacts c
        JOIN firms f ON c.firm_id = f.id
        LEFT JOIN signals sig ON sig.firm_id = f.id
        WHERE f.tier = 1 AND sig.id IS NULL
    """).fetchone()[0]


# ─── Outreach queue ──────────────────────────────────────────────────────────

def queue_pending(conn) -> int:
    return conn.execute(
        "SELECT count(*) FROM outreach_queue WHERE status = 'pending'"
    ).fetchone()[0]


def queue_with_first_line(conn) -> int:
    return conn.execute("""
        SELECT count(*) FROM outreach_queue
        WHERE first_line IS NOT NULL AND first_line != ''
    """).fetchone()[0]


# ─── Scoring artifacts ───────────────────────────────────────────────────────

def scored_with_brief(conn) -> int:
    return conn.execute("""
        SELECT count(DISTINCT contact_id) FROM scores
        WHERE account_brief IS NOT NULL
    """).fetchone()[0]


def avg_score(conn) -> float:
    r = conn.execute("""
        SELECT AVG(s.score) FROM (
            SELECT contact_id, MAX(score) AS score
            FROM scores GROUP BY contact_id
        ) s
        JOIN contacts c ON c.id = s.contact_id
        JOIN firms f ON f.id = c.firm_id
        WHERE f.tier = 1
    """).fetchone()[0]
    return round(r or 0, 1)


def avg_icp_fit(conn) -> float:
    r = conn.execute("""
        SELECT AVG(s.icp_fit) FROM scores s
        JOIN contacts c ON c.id = s.contact_id
        JOIN firms f ON f.id = c.firm_id
        WHERE f.tier = 1 AND s.icp_fit IS NOT NULL
    """).fetchone()[0]
    return round(r or 0, 1)


def avg_ai_readiness(conn) -> float:
    r = conn.execute("""
        SELECT AVG(s.ai_readiness) FROM scores s
        JOIN contacts c ON c.id = s.contact_id
        JOIN firms f ON f.id = c.firm_id
        WHERE f.tier = 1 AND s.ai_readiness IS NOT NULL
    """).fetchone()[0]
    return round(r or 0, 1)


# ─── Firm/universe stats ─────────────────────────────────────────────────────

def tier_breakdown(conn) -> dict:
    rows = conn.execute(
        "SELECT tier, count(*) AS n FROM firms GROUP BY tier"
    ).fetchall()
    return {r["tier"]: r["n"] for r in rows}


def sec_universe_stats(conn) -> dict:
    total = conn.execute("SELECT count(*) FROM sec_universe").fetchone()[0]
    icp = conn.execute(
        "SELECT count(*) FROM sec_universe WHERE icp_fit = 1"
    ).fetchone()[0]
    return {"total": total, "icp_qualified": icp}


def total_aum(conn) -> float:
    r = conn.execute("""
        SELECT SUM(aum_reported) FROM firms
        WHERE tier = 1 AND aum_reported IS NOT NULL
    """).fetchone()[0]
    return r or 0


def latest_signal_date(conn) -> str:
    r = conn.execute("SELECT MAX(signal_date) FROM signals").fetchone()[0]
    return r or ""


def total_signals(conn) -> int:
    return conn.execute("SELECT count(*) FROM signals").fetchone()[0]


# ─── Canonical bundle ────────────────────────────────────────────────────────

def canonical_stats(conn) -> dict:
    """Return the dict that all three updaters embed under `stats` in their JSON.
    Single call site → guarantees identical numbers across pages."""
    tiers = tier_breakdown(conn)
    sec = sec_universe_stats(conn)
    return {
        "total_contacts":  total_contacts(conn, tier=1),
        "verified":        contacts_verified(conn),
        "need_email":      contacts_need_email(conn),
        "signal_gaps":     signal_gaps(conn),
        "ready":           contacts_ready(conn),
        "strong_match":    contacts_strong_match(conn),
        "avg_score":       avg_score(conn),
        "avg_icp_fit":     avg_icp_fit(conn),
        "avg_ai_readiness": avg_ai_readiness(conn),
        "queue_pending":   queue_pending(conn),
        "with_first_line": queue_with_first_line(conn),
        "with_brief":      scored_with_brief(conn),
        "sec_indexed":     sec["total"],
        "sec_icp":         sec["icp_qualified"],
        "total_aum":       total_aum(conn),
        "tier1_firms":     tiers.get(1, 0),
        "tier2_firms":     tiers.get(2, 0),
        "tier3_firms":     tiers.get(3, 0),
        "total_signals":   total_signals(conn),
        "latest_signal":   latest_signal_date(conn),
    }


if __name__ == "__main__":
    import json, sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from database import get_db
    conn = get_db()
    try:
        print(json.dumps(canonical_stats(conn), indent=2, default=str))
    finally:
        conn.close()
