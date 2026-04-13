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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from metrics import canonical_stats

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

    # Email-source breakdown — Tier-1 contacts only, exclude placeholders.
    # (Tier 2 placeholder rows would otherwise dominate "no email" with 500+.)
    email_sources = {r["src"]: {"n": r["n"], "verified": r["v"]}
        for r in conn.execute("""
            SELECT COALESCE(NULLIF(c.email_source,''), 'none') AS src,
                   COUNT(*) AS n,
                   SUM(CASE WHEN c.email_verified=1 THEN 1 ELSE 0 END) AS v
              FROM contacts c
              JOIN firms f ON f.id = c.firm_id
             WHERE f.tier = 1
               AND COALESCE(c.is_placeholder,0) = 0
             GROUP BY COALESCE(NULLIF(c.email_source,''), 'none')
        """).fetchall()}

    # Recent triggers — Tier-1 signals from the last 48h, with full source
    # links + contact attribution. Powers the alert bell. Noise filtering
    # is in SQL (cheap exclusions) plus a Python pass for the OR-condition.
    recent_triggers = []
    try:
        trows = conn.execute("""
            SELECT s.id AS sid,
                   s.signal_type, s.signal_subtype,
                   s.content       AS signal_content,
                   s.source_url    AS signal_url,
                   s.signal_date,
                   s.buying_stage,
                   f.id AS firm_id, f.name AS firm_name, f.firm_type,
                   c.name  AS contact_name,
                   c.title AS contact_title
              FROM signals s
              JOIN firms f    ON f.id = s.firm_id
              LEFT JOIN contacts c ON c.id = s.contact_id
             WHERE f.tier = 1
               AND s.signal_date IS NOT NULL
               AND s.signal_date >= datetime('now', '-7 days')
               AND COALESCE(s.source_url,'') NOT LIKE '%.com%'
               AND COALESCE(s.source_url,'') NOT LIKE '%sec.gov/Archives%'
               AND LENGTH(COALESCE(s.content,'')) >= 30
               AND COALESCE(s.content,'') NOT LIKE 'UNITED STATES%'
               AND COALESCE(s.content,'') NOT LIKE 'Table of Contents%'
             ORDER BY s.signal_date DESC
             LIMIT 80
        """).fetchall()
        for t in trows:
            recent_triggers.append({
                "signal_id":     t["sid"],
                "firm_id":       t["firm_id"],
                "firm_name":     t["firm_name"],
                "firm_type":     t["firm_type"] or "",
                "signal_type":   t["signal_type"] or "",
                "signal_subtype": t["signal_subtype"] or "",
                "signal_content": (t["signal_content"] or "")[:120],
                "signal_url":    t["signal_url"] or "",
                "signal_date":   t["signal_date"],
                "buying_stage":  t["buying_stage"] or "",
                "contact_name":  t["contact_name"] or "",
                "contact_title": t["contact_title"] or "",
            })
            if len(recent_triggers) >= 20:
                break
    except Exception:
        pass

    latest_row = conn.execute("SELECT MAX(signal_date) FROM signals").fetchone()
    latest_signal = latest_row[0] if latest_row else None

    # Page-specific (not in canonical): legacy keys + presentation extras
    page_specific = {
        "latest_signal":   latest_signal,
        "email_sources":   email_sources,
        "recent_triggers": recent_triggers,
        "total_firms":     active,
        "tier1":           tier1,
        "tier2":           tier2,
        "total_monitored": tier1 + tier2,
        "approved":  one("SELECT COUNT(*) FROM outreach_queue WHERE status=?", ("approved",)),
        "skipped":   one("SELECT COUNT(*) FROM outreach_queue WHERE status=?", ("skipped",)),
        "flagged":   one("SELECT COUNT(*) FROM outreach_queue WHERE status=?", ("flagged",)),
        "good_match": one("SELECT COUNT(*) FROM scores WHERE score >= 55 AND score < 75"),
        "sec_total":     sec_total,   # legacy alias for canonical sec_indexed
        "universe_line": universe_line,
        # legacy alias for canonical queue_pending — kept so HTML keeps rendering
        "pending":       one("SELECT COUNT(*) FROM outreach_queue WHERE status=?", ("pending",)),
    }
    # Canonical keys overwrite any overlap → all pages stay in sync.
    page_specific.update(canonical_stats(conn))
    return page_specific


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
            c.linkedin_url      AS linkedin_url,
            c.twitter_handle    AS twitter_handle,
            c.research_json     AS research_json,
            c.last_activity_at  AS last_activity_at,
            c.meddic_role       AS meddic_role,
            c.meddic_confidence AS meddic_confidence,
            c.meddic_reasoning  AS meddic_reasoning,
            COALESCE(c.do_not_contact, 0) AS do_not_contact,
            f.id                AS firm_id,
            f.name              AS firm_name,
            f.firm_type         AS firm_type,
            f.tier              AS tier,
            f.buying_stage      AS buying_stage,
            f.competitor        AS competitor,
            f.aum_range         AS aum_range,
            COALESCE(f.has_objections, 0) AS has_objections,
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
            -- Head of AI first, then CTO, CIO, CInvO, MD, everyone else.
            -- Title beats freshness so the right *person* wins at equal score.
            CASE
              WHEN LOWER(c.title) LIKE '%head of ai%'         THEN 1
              WHEN LOWER(c.title) LIKE '%chief ai%'           THEN 1
              WHEN LOWER(c.title) LIKE '%chief technology%'   THEN 2
              WHEN LOWER(c.title) LIKE '%cto%'                THEN 2
              WHEN LOWER(c.title) LIKE '%chief information%'  THEN 3
              WHEN LOWER(c.title) LIKE '%cio%' AND LOWER(c.title) NOT LIKE '%invest%' THEN 3
              WHEN LOWER(c.title) LIKE '%chief investment%'   THEN 4
              WHEN LOWER(c.title) LIKE '%managing director%'  THEN 5
              ELSE 10
            END ASC,
            COALESCE(s.signal_freshness, 0) DESC,
            q.id ASC
    """).fetchall()

    def _parse_sections(raw):
        if not raw:
            return []
        try:
            v = json.loads(raw)
            return v if isinstance(v, list) else []
        except Exception:
            return []

    # Build intra-firm ranking (by score desc) and per-firm contact list
    firm_groups: dict[int, list[dict]] = {}
    for r in rows:
        firm_groups.setdefault(r["firm_id"], []).append(r)
    rank_map: dict[int, int] = {}           # queue_id -> rank_at_firm
    firm_count_map: dict[int, int] = {}     # firm_id -> count of contacts at firm
    firm_peers_map: dict[int, list[dict]] = {}  # firm_id -> [{contact_name,title,score,queue_id}]
    firm_coverage_map: dict[int, str] = {}      # firm_id -> coverage status
    HIGH_CONFIDENCE = 0.80
    for fid, grp in firm_groups.items():
        grp_sorted = sorted(grp, key=lambda x: (x["score"] or 0), reverse=True)
        firm_count_map[fid] = len(grp_sorted)
        firm_peers_map[fid] = [{
            "queue_id":     gr["queue_id"],
            "contact_name": gr["contact_name"],
            "title":        gr["title"] or "",
            "score":        float(gr["score"]) if gr["score"] is not None else 0.0,
        } for gr in grp_sorted]
        for idx, gr in enumerate(grp_sorted, 1):
            rank_map[gr["queue_id"]] = idx
        # Multi-thread coverage — only count high-confidence Claude roles.
        has_eb = any(
            (gr["meddic_role"] == "EB"
             and (gr["meddic_confidence"] or 0) >= HIGH_CONFIDENCE)
            for gr in grp_sorted
        )
        has_ch = any(
            (gr["meddic_role"] == "CH"
             and (gr["meddic_confidence"] or 0) >= HIGH_CONFIDENCE)
            for gr in grp_sorted
        )
        if has_eb and has_ch:
            firm_coverage_map[fid] = "complete"
        elif has_eb:
            firm_coverage_map[fid] = "needs_ch"
        elif has_ch:
            firm_coverage_map[fid] = "needs_eb"
        else:
            firm_coverage_map[fid] = ""  # no badge

    LINKEDIN_ARTIFACTS = (
        "Agree & Join LinkedIn", "Agree &amp; Join LinkedIn",
        "Join to see", "Join LinkedIn", "Sign in to view",
        "Log in to view", "Log in",
        "| comments Agree", "comments Agree",
        "By clicking", "| comments", "View post", "| LinkedIn",
        "…\n",
    )

    def clean_snippet(text: str) -> str:
        if not text:
            return ""
        # Trim known LinkedIn / Exa boilerplate. We only cut when the artifact
        # starts past char 0 — never strip a snippet that legitimately begins
        # with one of these phrases.
        for artifact in LINKEDIN_ARTIFACTS:
            idx = text.find(artifact)
            if idx > 0:
                text = text[:idx]
        text = " ".join(text.split())  # collapse whitespace
        if not text or len(text) < 20:
            return ""  # caller treats empty as "no snippet, suppress card"
        if len(text) <= 200:
            return text
        cut = text.rfind(" ", 0, 200)
        return (text[:cut] if cut > 100 else text[:200]).rstrip(",.;:") + "…"

    def _clean_items(items):
        out = []
        for it in (items or []):
            if not isinstance(it, dict):
                continue
            it = dict(it)
            it["snippet"] = clean_snippet(it.get("snippet", ""))
            out.append(it)
        return out

    def _parse_research(raw):
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except Exception:
            return None
        if isinstance(data, dict):
            data["recent_posts"] = _clean_items(data.get("recent_posts"))
            data["speaking"]     = _clean_items(data.get("speaking"))
            data["press"]        = _clean_items(data.get("press"))
        return data

    # Landing-page detector mirrors collectors/exa_collector.py so that
    # signals whose source_url is just a section root can be upgraded to a
    # contact's specific LinkedIn post URL when one exists in research_json.
    import re as _re
    _LANDING_PATTERNS = [
        r"/news/?$", r"/press/?$", r"/insights/?$",
        r"/blog/?$", r"/media/?$", r"/resources/?$",
        r"/updates/?$", r"/newsroom/?$", r"/articles/?$",
    ]
    _LANDING_RX = _re.compile("|".join(_LANDING_PATTERNS), _re.IGNORECASE)

    def _resolve_signal_url(raw_url, research):
        """If the firm-level signal_url is a landing page AND the contact has
        a real LinkedIn post URL in research, swap to that. The deep link is
        always more useful than a section index."""
        url = raw_url or ""
        if not url or _LANDING_RX.search(url):
            for p in (research or {}).get("recent_posts") or []:
                pu = (p.get("url") or "").lower()
                if "linkedin.com/posts" in pu:
                    return p["url"]
        return url

    def _build(r):
        research = _parse_research(r["research_json"])
        signal_url = _resolve_signal_url(r["signal_url"], research)
        return research, signal_url

    out = []
    for r in rows:
        research, signal_url = _build(r)
        out.append({
        "queue_id":         r["queue_id"],
        "contact_id":       r["contact_id"],
        "firm_id":          r["firm_id"],
        "firm_name":        r["firm_name"],
        "firm_type":        r["firm_type"],
        "tier":             r["tier"] or 1,
        "buying_stage":     r["buying_stage"] or "",
        "competitor":       r["competitor"] or "",
        "aum_range":        r["aum_range"] or "",
        "has_objections":   bool(r["has_objections"]),
        "contact_name":     r["contact_name"],
        "title":            r["title"] or "",
        "email":            r["email"] or "",
        "email_verified":   bool(r["email_verified"]),
        "email_source":     r["email_source"] or "",
        "linkedin_url":     r["linkedin_url"] or "",
        "twitter_handle":   r["twitter_handle"] or "",
        "do_not_contact":   bool(r["do_not_contact"]),
        "rank_at_firm":     rank_map.get(r["queue_id"], 1),
        "firm_contact_count": firm_count_map.get(r["firm_id"], 1),
        "firm_peers":       firm_peers_map.get(r["firm_id"], []),
        "firm_coverage":    firm_coverage_map.get(r["firm_id"], ""),
        "meddic_role":      r["meddic_role"] or "",
        "meddic_confidence": float(r["meddic_confidence"]) if r["meddic_confidence"] is not None else None,
        "meddic_reasoning": r["meddic_reasoning"] or "",
        "research_json":    research,
        "last_activity_at": r["last_activity_at"] or "",
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
        "signal_url":       signal_url or "",
        "signal_date":      r["signal_date"] or "",
        "first_line":       r["first_line"] or "",
        "status":           r["status"],
        })

    # Firm-diversity sort: top 3 at each firm float up, rest fall below.
    # Primary  : MIN(rank_at_firm, 4) ASC — top 3 mingle; rank 4+ drops
    # Secondary: score DESC              — within the same tier, higher score wins
    # Tertiary : existing title priority (CTO/CIO/Head of AI) preserved
    #            by chaining a stable sort — we sort by secondary first, then
    #            primary, so equal-primary rows keep their SQL-ordered sequence.
    # Rationale: avoids 12 Francisco Partners contacts dominating the top list
    # before a single Silver Lake / Blackstone / Vista contact shows up.
    def _sort_key(c):
        return (
            min(c.get("rank_at_firm") or 99, 4),
            -float(c.get("score") or 0),
        )
    out.sort(key=_sort_key)
    return out


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
