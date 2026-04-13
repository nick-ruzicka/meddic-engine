#!/usr/bin/env python3
"""scripts/update_dashboard.py

Reads the SQLite DB and writes export/contacts_data.json — the payload that
index.html fetches on load. Shape:

    {
      "generated_at": ISO8601,
      "stats":  { ...same as /api/stats... },
      "contacts": [ ...same shape as GET /api/contacts... ]
    }

Idempotent: safe to re-run. Overwrites the file.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from database import get_db

OUTPUT = os.path.join(ROOT, "export", "contacts_data.json")


def _stats(conn) -> dict:
    def one(sql, params=()):
        return conn.execute(sql, params).fetchone()[0]
    # SEC counts fall back to 0 if the loader hasn't been run yet
    try:
        sec_total = one("SELECT COUNT(*) FROM sec_universe")
        sec_icp   = one("SELECT COUNT(*) FROM sec_universe WHERE icp_fit=1")
    except Exception:
        sec_total = sec_icp = 0

    tier1 = one("SELECT COUNT(*) FROM firms WHERE tier=1")
    tier2 = one("SELECT COUNT(*) FROM firms WHERE tier=2")
    active = tier1 or one("SELECT COUNT(*) FROM firms")
    universe_line = (
        f"{sec_total:,} SEC-INDEXED · {sec_icp:,} ICP-QUALIFIED · "
        f"{tier2} MONITORED · {tier1} ACTIVE TARGETS"
        if sec_total else f"{active:,} ACTIVE TARGETS"
    )

    # Email-source breakdown across ALL contacts (not just queued)
    email_sources = {r["src"]: {"n": r["n"], "verified": r["v"]}
        for r in conn.execute("""
            SELECT COALESCE(NULLIF(email_source,''), 'none') AS src,
                   COUNT(*) AS n,
                   SUM(CASE WHEN email_verified=1 THEN 1 ELSE 0 END) AS v
              FROM contacts
             GROUP BY COALESCE(NULLIF(email_source,''), 'none')
        """).fetchall()}

    return {
        "email_sources":  email_sources,
        "total_firms":    active,
        "tier1":          tier1,
        "tier2":          tier2,
        "tier1_firms":    tier1,
        "tier2_firms":    tier2,
        "total_monitored": tier1 + tier2,
        "total_contacts": one("SELECT COUNT(*) FROM contacts WHERE COALESCE(is_placeholder,0)=0"),
        "total_signals":  one("SELECT COUNT(*) FROM signals"),
        "pending":    one("SELECT COUNT(*) FROM outreach_queue WHERE status=?", ("pending",)),
        "approved":   one("SELECT COUNT(*) FROM outreach_queue WHERE status=?", ("approved",)),
        "skipped":    one("SELECT COUNT(*) FROM outreach_queue WHERE status=?", ("skipped",)),
        "flagged":    one("SELECT COUNT(*) FROM outreach_queue WHERE status=?", ("flagged",)),
        "strong_match": one("SELECT COUNT(*) FROM scores WHERE score >= 75"),
        "good_match":   one("SELECT COUNT(*) FROM scores WHERE score >= 55 AND score < 75"),
        "sec_total":    sec_total,
        "sec_icp":      sec_icp,
        "universe_line": universe_line,
    }


def _contacts(conn) -> list[dict]:
    # Latest signal per contact — fall back to latest signal per firm if contact has none.
    rows = conn.execute("""
        SELECT
            q.id                AS queue_id,
            q.status            AS status,
            q.first_line        AS first_line,
            q.signal_id         AS queue_signal_id,
            c.id                AS contact_id,
            c.name              AS contact_name,
            c.title             AS title,
            c.email             AS email,
            c.email_verified    AS email_verified,
            c.email_source      AS email_source,
            f.id                AS firm_id,
            f.name              AS firm_name,
            f.firm_type         AS firm_type,
            f.tier              AS tier,
            f.buying_stage      AS buying_stage,
            s.score             AS score,
            s.icp_fit           AS icp_fit,
            s.ai_readiness      AS ai_readiness,
            s.reachability      AS reachability,
            s.signal_freshness  AS signal_freshness,
            s.label             AS label,
            s.action            AS action,
            s.reasoning         AS reasoning,
            s.missing           AS missing,
            s.sections_used     AS sections_used,
            s.account_brief     AS account_brief,
            COALESCE(sig_q.signal_type,  sig_c.signal_type,  sig_f.signal_type)  AS signal_type,
            COALESCE(sig_q.content,      sig_c.content,      sig_f.content)      AS signal_content,
            COALESCE(sig_q.source_url,   sig_c.source_url,   sig_f.source_url)   AS signal_url,
            COALESCE(sig_q.signal_date,  sig_c.signal_date,  sig_f.signal_date)  AS signal_date
        FROM outreach_queue q
        JOIN contacts c ON c.id = q.contact_id
        JOIN firms    f ON f.id = q.firm_id
        LEFT JOIN scores  s     ON s.id = q.score_id
        LEFT JOIN signals sig_q ON sig_q.id = q.signal_id
        LEFT JOIN signals sig_c ON sig_c.id = (
            SELECT id FROM signals
             WHERE contact_id = c.id
             ORDER BY COALESCE(signal_date, created_at) DESC LIMIT 1
        )
        LEFT JOIN signals sig_f ON sig_f.id = (
            SELECT id FROM signals
             WHERE firm_id = f.id
             ORDER BY COALESCE(signal_date, created_at) DESC LIMIT 1
        )
        ORDER BY
            COALESCE(s.score, 0) DESC,
            -- Title priority: CTO / Head of AI / Chief AI Officer first,
            -- then other named technical leaders, then general executives.
            -- Title outranks signal_freshness so the right *person* always wins the tie.
            CASE
              WHEN LOWER(c.title) LIKE '%chief technology%'        THEN 1
              WHEN LOWER(c.title) LIKE '%cto%'                     THEN 1
              WHEN LOWER(c.title) LIKE '%head of ai%'              THEN 2
              WHEN LOWER(c.title) LIKE '%chief ai%'                THEN 2
              WHEN LOWER(c.title) LIKE '%head of data%'            THEN 3
              WHEN LOWER(c.title) LIKE '%chief data%'              THEN 3
              WHEN LOWER(c.title) LIKE '%head of technology%'      THEN 3
              WHEN LOWER(c.title) LIKE '%chief information%'       THEN 4
              WHEN LOWER(c.title) LIKE '%cio%'                     THEN 4
              WHEN LOWER(c.title) LIKE '%managing director%ai%'    THEN 5
              WHEN LOWER(c.title) LIKE '%partner%'                 THEN 6
              WHEN LOWER(c.title) LIKE '%president%'               THEN 8
              WHEN LOWER(c.title) LIKE '%coo%'                     THEN 9
              WHEN LOWER(c.title) LIKE '%chief operating%'         THEN 9
              ELSE 7
            END ASC,
            COALESCE(s.signal_freshness, 0) DESC,
            q.id DESC
    """).fetchall()

    def _parse_sections(raw):
        if not raw:
            return []
        try:
            v = json.loads(raw)
            return v if isinstance(v, list) else []
        except Exception:
            return []

    return [{
        "queue_id":         r["queue_id"],
        "contact_id":       r["contact_id"],
        "firm_id":          r["firm_id"],
        "firm_name":        r["firm_name"],
        "firm_type":        r["firm_type"],
        "tier":             r["tier"] or 1,
        "buying_stage":     r["buying_stage"] or "",
        "contact_name":     r["contact_name"],
        "title":            r["title"] or "",
        "email":            r["email"] or "",
        "email_verified":   bool(r["email_verified"]),
        "email_source":     r["email_source"] or "",
        "score":            float(r["score"]) if r["score"] is not None else 0.0,
        "icp_fit":          float(r["icp_fit"]) if r["icp_fit"] is not None else None,
        "ai_readiness":     float(r["ai_readiness"]) if r["ai_readiness"] is not None else None,
        "reachability":     float(r["reachability"]) if r["reachability"] is not None else None,
        "signal_freshness": float(r["signal_freshness"]) if r["signal_freshness"] is not None else None,
        "label":            r["label"] or "",
        "action":           r["action"] or "",
        "reasoning":        r["reasoning"] or "",
        "missing":          r["missing"] or "",
        "sections_used":    _parse_sections(r["sections_used"]),
        "account_brief":    (lambda v: json.loads(v) if v else None)(r["account_brief"]),
        "signal_type":      r["signal_type"] or "",
        "signal_content":   r["signal_content"] or "",
        "signal_url":       r["signal_url"] or "",
        "signal_date":      r["signal_date"] or "",
        "first_line":       r["first_line"] or "",
        "status":           r["status"],
    } for r in rows]


def _tier2_virtual(conn) -> list[dict]:
    """Render tier-2 firms as virtual contact rows for the dashboard.

    These have no outreach_queue entry; rendering is driven by firmographic
    score + a synthetic placeholder contact row.
    """
    rows = conn.execute("""
        SELECT f.id AS firm_id, f.name AS firm_name, f.firm_type AS firm_type,
               f.aum_reported AS aum_reported,
               c.id AS contact_id,
               s.score AS score, s.icp_fit, s.ai_readiness, s.reachability,
               s.signal_freshness, s.label, s.action, s.reasoning, s.missing
          FROM firms f
          JOIN contacts c ON c.firm_id = f.id AND COALESCE(c.is_placeholder,0)=1
          LEFT JOIN scores s ON s.contact_id = c.id
                             AND s.scored_by = 'firmographic_only'
         WHERE f.tier = 2
         ORDER BY COALESCE(s.score,0) DESC, f.id ASC
    """).fetchall()
    out = []
    for r in rows:
        out.append({
            "queue_id":         None,
            "contact_id":       r["contact_id"],
            "firm_id":          r["firm_id"],
            "firm_name":        r["firm_name"],
            "firm_type":        r["firm_type"],
            "tier":             2,
            "buying_stage":     "",
            "contact_name":     "—",
            "title":            "No contact — firmographic only",
            "email":            "",
            "email_verified":   False,
            "email_source":     "",
            "score":            float(r["score"]) if r["score"] is not None else 0.0,
            "icp_fit":          float(r["icp_fit"]) if r["icp_fit"] is not None else None,
            "ai_readiness":     float(r["ai_readiness"]) if r["ai_readiness"] is not None else None,
            "reachability":     float(r["reachability"]) if r["reachability"] is not None else None,
            "signal_freshness": float(r["signal_freshness"]) if r["signal_freshness"] is not None else None,
            "label":            r["label"] or "Uncontacted",
            "action":           r["action"] or "Activate",
            "reasoning":        r["reasoning"] or "",
            "missing":          r["missing"] or "",
            "sections_used":    [],
            "account_brief":    None,
            "signal_type":      "",
            "signal_content":   "",
            "signal_url":       "",
            "signal_date":      "",
            "first_line":       "",
            "status":           "pending",
            "is_placeholder":   True,
        })
    return out


def main() -> int:
    conn = get_db()
    try:
        contacts = _contacts(conn) + _tier2_virtual(conn)
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "stats":    _stats(conn),
            "contacts": contacts,
        }
    finally:
        conn.close()

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    n = len(payload["contacts"])
    pending = payload["stats"]["pending"]
    hhmm = datetime.now().strftime("%H:%M")
    print(f"✓ contacts_data.json — {n} contacts, {pending} pending, generated at {hhmm}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
