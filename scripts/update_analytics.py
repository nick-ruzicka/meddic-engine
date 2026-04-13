#!/usr/bin/env python3
"""scripts/update_analytics.py

Reads SQLite state, writes export/analytics_data.json.

Sections:
    hero              top-line numbers
    score_histogram   5-band distribution
    dimensions        avg of each scoring axis
    funnel            firms → signals → contacts → scored → queued → approved
    by_firm_type      aggregate scores per firm type
    by_buying_stage   aggregate scores per buying stage
    by_signal_type    signal mix + avg score of firms with that signal
    top_ready         top 10 pending, Strong+Good match
    flagged           flagged for review (AI rejected despite score)

Idempotent. Run after any --score / --queue / approval.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import mean

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from database import get_db
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from metrics import canonical_stats

OUTPUT = os.path.join(ROOT, "export", "analytics_data.json")

BANDS = [
    ("strong",   75, 101, "Strong Match", "#10b981"),
    ("good",     55,  75, "Good Match",   "#f59e0b"),
    ("moderate", 35,  55, "Moderate",     "#f97316"),
    ("weak",      0,  35, "Weak",         "#ef4444"),
]


def _rows(conn, sql, params=()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def build_hero(conn) -> dict:
    s = dict(conn.execute("""
        SELECT
            (SELECT COUNT(*) FROM firms) AS firms,
            (SELECT COUNT(*) FROM contacts WHERE COALESCE(is_placeholder,0)=0) AS contacts,
            (SELECT COUNT(*) FROM contacts WHERE email_verified=1 AND COALESCE(is_placeholder,0)=0) AS verified,
            (SELECT COUNT(*) FROM signals) AS signals,
            (SELECT COUNT(*) FROM scores WHERE COALESCE(scored_by,'claude') != 'firmographic_only') AS scored,
            (SELECT ROUND(AVG(score),1) FROM scores WHERE COALESCE(scored_by,'claude') != 'firmographic_only') AS avg_score,
            (SELECT COUNT(*) FROM scores WHERE score >= 75 AND COALESCE(scored_by,'claude') != 'firmographic_only') AS strong,
            (SELECT COUNT(*) FROM outreach_queue WHERE status='pending') AS pending,
            (SELECT COUNT(*) FROM outreach_queue WHERE status='approved') AS approved,
            (SELECT COUNT(*) FROM outreach_queue WHERE status='skipped') AS skipped
    """).fetchone())
    s["avg_score"] = s["avg_score"] or 0
    s["verified_pct"] = round(100 * s["verified"] / max(s["contacts"], 1), 1)

    # SEC universe counts — absent gracefully if the loader hasn't run
    try:
        row = conn.execute("""
            SELECT
              (SELECT COUNT(*) FROM sec_universe) AS sec_total,
              (SELECT COUNT(*) FROM sec_universe WHERE icp_fit=1) AS sec_icp
        """).fetchone()
        s["sec_total"] = row["sec_total"]
        s["sec_icp"]   = row["sec_icp"]
    except Exception:
        s["sec_total"] = 0
        s["sec_icp"]   = 0

    # Tier breakdown
    tier_row = conn.execute("""
        SELECT
          (SELECT COUNT(*) FROM firms WHERE tier=1) AS tier1,
          (SELECT COUNT(*) FROM firms WHERE tier=2) AS tier2
    """).fetchone()
    s["tier1"] = tier_row["tier1"] or 0
    s["tier2"] = tier_row["tier2"] or 0

    # Assemble the hero tagline
    active = s["tier1"]
    monitored = s["tier2"]
    if s["sec_total"]:
        s["universe_line"] = (f"{s['sec_total']:,} SEC-INDEXED · "
                              f"{s['sec_icp']:,} ICP-QUALIFIED · "
                              f"{monitored} MONITORED · "
                              f"{active} ACTIVE TARGETS")
    else:
        s["universe_line"] = f"{active:,} ACTIVE TARGETS"
    return s


def build_score_histogram(conn) -> list[dict]:
    # Exclude firmographic-only tier-2 scores — they aren't human-review candidates.
    scores = [r["score"] for r in conn.execute(
        "SELECT score FROM scores WHERE COALESCE(scored_by,'claude') != 'firmographic_only'"
    ).fetchall() if r["score"] is not None]
    total = max(len(scores), 1)
    bands = []
    for key, lo, hi, label, color in BANDS:
        n = sum(1 for s in scores if lo <= s < hi)
        bands.append({"key": key, "label": label, "color": color,
                      "count": n, "pct": round(100 * n / total, 1),
                      "range": f"{lo}–{hi-1 if hi != 101 else 100}"})
    return bands


def build_dimensions(conn) -> dict:
    rows = conn.execute("""SELECT icp_fit, ai_readiness, reachability, signal_freshness
                           FROM scores
                           WHERE score IS NOT NULL
                             AND COALESCE(scored_by,'claude') != 'firmographic_only'""").fetchall()
    out: dict = {}
    for key in ("icp_fit", "ai_readiness", "reachability", "signal_freshness"):
        vals = [r[key] for r in rows if r[key] is not None]
        if vals:
            out[key] = {"avg": round(mean(vals), 1),
                        "min": round(min(vals), 1),
                        "max": round(max(vals), 1),
                        "weight": {"icp_fit": 30, "ai_readiness": 25,
                                   "reachability": 25, "signal_freshness": 20}[key]}
        else:
            out[key] = {"avg": 0, "min": 0, "max": 0, "weight": 0}
    return out


def build_funnel(conn) -> dict:
    """Flat funnel dict — single source of truth for all pipeline counts.

    Keys:
      sec_indexed, icp_qualified, tier2_monitored, tier1_active,
      contacts_scored, qualified, ready

    Also returns `stages` — an ordered list derived from the flat dict for
    the analytics.html funnel renderer. Labels/order live here.
    """
    try:
        sec_indexed   = conn.execute("SELECT COUNT(*) FROM sec_universe").fetchone()[0]
        icp_qualified = conn.execute("SELECT COUNT(*) FROM sec_universe WHERE icp_fit=1").fetchone()[0]
    except Exception:
        sec_indexed = icp_qualified = 0

    tier1 = conn.execute("SELECT COUNT(*) FROM firms WHERE tier=1").fetchone()[0]
    tier2 = conn.execute("SELECT COUNT(*) FROM firms WHERE tier=2").fetchone()[0]

    # Tier-1 filter on every downstream funnel step — bottleneck otherwise
    # conflates tier-2 firmographic scoring (placeholders) with real pipeline.
    contacts_scored = conn.execute("""
        SELECT COUNT(DISTINCT sc.contact_id)
          FROM scores sc
          JOIN contacts c ON c.id = sc.contact_id
          JOIN firms    f ON f.id = c.firm_id
         WHERE f.tier = 1 AND COALESCE(c.is_placeholder,0)=0
    """).fetchone()[0]

    qualified = conn.execute("""
        SELECT COUNT(DISTINCT sc.contact_id)
          FROM scores sc
          JOIN contacts c ON c.id = sc.contact_id
          JOIN firms    f ON f.id = c.firm_id
         WHERE f.tier = 1 AND sc.score >= 55
           AND COALESCE(c.is_placeholder,0)=0
    """).fetchone()[0]

    ready = conn.execute("""
        SELECT COUNT(DISTINCT sc.contact_id)
          FROM scores sc
          JOIN contacts c ON c.id = sc.contact_id
          JOIN firms    f ON f.id = c.firm_id
         WHERE f.tier = 1 AND sc.score >= 55
           AND c.email_verified = 1
           AND COALESCE(c.is_placeholder,0)=0
    """).fetchone()[0]

    flat = {
        "sec_indexed":     sec_indexed,
        "icp_qualified":   icp_qualified,
        "tier2_monitored": tier2,
        "tier1_active":    tier1,
        "contacts_scored": contacts_scored,
        "qualified":       qualified,
        "ready":           ready,
    }

    # Ordered stages for funnel rendering — labels/order live here.
    stage_order = [
        ("sec_indexed",     "SEC-indexed"),
        ("icp_qualified",   "ICP-qualified"),
        ("tier2_monitored", "Tier 2 monitored"),
        ("tier1_active",    "Tier 1 active"),
        ("ready",           "Ready for outreach"),
    ]
    max_n = max((flat[k] for k, _ in stage_order), default=1) or 1
    flat["stages"] = [
        {"key": k, "label": lbl, "count": flat[k],
         "pct": round(100 * flat[k] / max_n, 1)}
        for k, lbl in stage_order
    ]
    return flat


def build_by_firm_type(conn) -> list[dict]:
    rows = _rows(conn, """
        SELECT f.firm_type, COUNT(DISTINCT f.id) AS firms,
               COUNT(s.id) AS scored, ROUND(AVG(s.score), 1) AS avg_score,
               SUM(CASE WHEN s.score >= 75 THEN 1 ELSE 0 END) AS strong
          FROM firms f
     LEFT JOIN contacts c ON c.firm_id = f.id AND COALESCE(c.is_placeholder,0)=0
     LEFT JOIN scores   s ON s.contact_id = c.id
                         AND COALESCE(s.scored_by,'claude') != 'firmographic_only'
         WHERE f.tier = 1
         GROUP BY f.firm_type
         ORDER BY avg_score DESC NULLS LAST
    """)
    return rows


def build_by_buying_stage(conn) -> list[dict]:
    return _rows(conn, """
        SELECT COALESCE(f.buying_stage, 'unknown') AS stage,
               COUNT(DISTINCT f.id) AS firms,
               COUNT(s.id) AS scored,
               ROUND(AVG(s.score), 1) AS avg_score
          FROM firms f
     LEFT JOIN contacts c ON c.firm_id = f.id AND COALESCE(c.is_placeholder,0)=0
     LEFT JOIN scores   s ON s.contact_id = c.id
                         AND COALESCE(s.scored_by,'claude') != 'firmographic_only'
         WHERE f.tier = 1
         GROUP BY f.buying_stage
         ORDER BY avg_score DESC NULLS LAST
    """)


def build_by_signal_type(conn) -> list[dict]:
    return _rows(conn, """
        SELECT signal_type, COUNT(*) AS signals,
               COUNT(DISTINCT firm_id) AS firms,
               ROUND(AVG(freshness_days), 0) AS avg_freshness_days
          FROM signals
         GROUP BY signal_type
         ORDER BY signals DESC
    """)


def build_top_ready(conn, n: int = 10) -> list[dict]:
    return _rows(conn, """
        SELECT q.id AS queue_id, f.name AS firm, c.name AS contact, c.title,
               s.score, s.label, c.email_verified
          FROM outreach_queue q
          JOIN contacts c ON c.id = q.contact_id
          JOIN firms    f ON f.id = q.firm_id
          JOIN scores   s ON s.id = q.score_id
         WHERE q.status = 'pending' AND s.score >= 55
         ORDER BY s.score DESC
         LIMIT ?
    """, (n,))


def build_flagged(conn) -> list[dict]:
    return _rows(conn, """
        SELECT f.name AS firm, c.name AS contact, s.score, s.action,
               s.reasoning, s.label
          FROM scores s
          JOIN contacts c ON c.id = s.contact_id
          JOIN firms    f ON f.id = s.firm_id
         WHERE s.action = 'Flag' OR s.score < 35
         ORDER BY s.score ASC
         LIMIT 20
    """)


def build_signal_conversion(conn) -> list[dict]:
    """Per-signal-type qualified-rate. 'none' = contacts with no signal attached."""
    rows = conn.execute("""
        WITH cs AS (
          SELECT DISTINCT c.id AS contact_id, sig.signal_type
            FROM contacts c
            JOIN signals sig
              ON sig.contact_id = c.id OR sig.firm_id = c.firm_id
        )
        SELECT cs.signal_type AS signal_type,
               COUNT(DISTINCT cs.contact_id) AS contacts,
               SUM(CASE WHEN sc.score >= 55 THEN 1 ELSE 0 END) AS qualified
          FROM cs
          LEFT JOIN scores sc ON sc.contact_id = cs.contact_id
         GROUP BY cs.signal_type
    """).fetchall()
    out = [dict(r) for r in rows]
    # 'none' bucket
    no_sig = conn.execute("""
        SELECT COUNT(DISTINCT c.id) AS contacts,
               SUM(CASE WHEN sc.score >= 55 THEN 1 ELSE 0 END) AS qualified
          FROM contacts c
          LEFT JOIN scores sc ON sc.contact_id = c.id
         WHERE NOT EXISTS (
           SELECT 1 FROM signals s
            WHERE s.contact_id = c.id OR s.firm_id = c.firm_id
         )
    """).fetchone()
    out.append({"signal_type": "none",
                "contacts": no_sig["contacts"] or 0,
                "qualified": no_sig["qualified"] or 0})
    # Ensure each canonical type is present (zero row if absent)
    seen = {r["signal_type"] for r in out}
    for t in ("twitter", "linkedin", "press", "hiring"):
        if t not in seen:
            out.append({"signal_type": t, "contacts": 0, "qualified": 0})
    # Compute rate
    for r in out:
        r["rate"] = round(100 * (r["qualified"] or 0) / r["contacts"], 1) if r["contacts"] else 0.0
    # Preferred order
    order = {"twitter":0, "linkedin":1, "press":2, "hiring":3, "none":4}
    out.sort(key=lambda r: order.get(r["signal_type"], 99))
    return out


def build_competitor_landscape(conn) -> list[dict]:
    rows = _rows(conn, """
        SELECT COALESCE(NULLIF(f.competitor,''), 'none') AS competitor,
               COUNT(DISTINCT f.id) AS firms,
               ROUND(AVG(s.score), 1) AS avg_score
          FROM firms f
          LEFT JOIN contacts c ON c.firm_id = f.id AND COALESCE(c.is_placeholder,0)=0
          LEFT JOIN scores   s ON s.contact_id = c.id
                             AND COALESCE(s.scored_by,'claude') != 'firmographic_only'
         WHERE f.tier = 1
         GROUP BY COALESCE(NULLIF(f.competitor,''), 'none')
    """)
    angles = {
        "alphasense": "Adjacent — not replacing",
        "rogo":       "Complementary add-on",
        "bloomberg":  "Terminal replacement angle",
        "stack_ai":   "Complementary workflow layer",
        "none":       "Cold ICP outreach",
    }
    for r in rows:
        r["angle"] = angles.get(r["competitor"], "Firm-specific positioning")
        r["avg_score"] = r["avg_score"] or 0
    rows.sort(key=lambda r: r["firms"], reverse=True)
    return rows


def build_aum_coverage(conn) -> dict:
    """Total AUM across active firms. Populated by scripts/match_sec_aum.py."""
    try:
        row = conn.execute("""
            SELECT COALESCE(SUM(aum_reported), 0) AS total_aum,
                   SUM(CASE WHEN aum_reported IS NOT NULL THEN 1 ELSE 0 END) AS firms_matched,
                   COUNT(*) AS firms_total
              FROM firms
             WHERE tier = 1
        """).fetchone()
        total = row["total_aum"] or 0
        # Rough denominator: ~$50T of US institutional AUM is the buying universe
        # for enterprise AI platforms in finance.
        return {
            "total_aum":         int(total),
            "firms_matched":     row["firms_matched"] or 0,
            "firms_total":       row["firms_total"]   or 0,
            "display_trillions": round(total / 1e12, 2) if total else 0.0,
            "share_pct":         round(100 * total / 50e12, 1) if total else 0.0,
        }
    except Exception:
        return {"total_aum": 0, "firms_matched": 0, "firms_total": 0,
                "display_trillions": 0.0, "share_pct": 0.0}


def build_ready_and_cost(conn) -> dict:
    """Ready = score >= 55 AND verified email. Cost = hunter contacts × $0.002 per ready."""
    row = conn.execute("""
        SELECT
          (SELECT COUNT(*) FROM scores sc
             JOIN contacts c ON c.id = sc.contact_id
            WHERE sc.score >= 55 AND c.email_verified = 1) AS ready,
          (SELECT COUNT(*) FROM scores WHERE score >= 55) AS qualified,
          (SELECT COUNT(*) FROM contacts WHERE email_source = 'hunter') AS hunter_calls
    """).fetchone()
    ready   = row["ready"] or 0
    qual    = row["qualified"] or 0
    hunter  = row["hunter_calls"] or 0
    cost    = (hunter * 0.002) / ready if ready else 0.0
    return {"ready": ready, "qualified": qual, "hunter_calls": hunter,
            "cost_per_lead": round(cost, 4)}


def build_bottleneck(funnel) -> dict:
    """Find worst pass-through in the funnel and emit an actionable suggestion."""
    stages = funnel.get("stages", []) if isinstance(funnel, dict) else funnel
    if len(stages) < 2:
        return {"step": None, "rate": 0, "message": ""}
    worst = None
    for i in range(1, len(stages)):
        prev, cur = stages[i-1]["count"], stages[i]["count"]
        if prev <= 0:
            continue
        rate = 100 * cur / prev
        if worst is None or rate < worst["rate"]:
            worst = {"from": stages[i-1]["label"], "to": stages[i]["label"],
                     "rate": round(rate, 1)}
    if not worst:
        return {"step": None, "rate": 0, "message": ""}

    suggestions = {
        "Firms with signal":  "Run Exa press + Twitter collectors to attach signals to more firms.",
        "Named contacts":     "Run the Exa team-page enricher on remaining firms.",
        "Contacts scored":    "Re-run --score; some contacts may be blocked on missing firm metadata.",
        "Queued for review":  "Run --queue to promote scored Strong/Good matches into the queue.",
        "Approved to send":   "Review the pending queue — approvals are the final conversion.",
    }
    worst["message"] = (
        f"{worst['from']} → {worst['to']} is the bottleneck at {worst['rate']}% pass-through. "
        f"{suggestions.get(worst['to'], 'Investigate this step for drop-off.')}"
    )
    return worst


def build_router_usage(conn) -> list[dict]:
    """Which skill sections are being injected, and how often."""
    counter: Counter = Counter()
    for row in conn.execute("SELECT sections_used FROM scores WHERE sections_used IS NOT NULL"):
        try:
            for sec in json.loads(row[0]):
                counter[sec] += 1
        except Exception:
            continue
    return [{"section": k, "count": v} for k, v in counter.most_common()]


def main() -> int:
    conn = get_db()
    try:
        canon  = canonical_stats(conn)
        funnel = build_funnel(conn)
        hero   = build_hero(conn)
        ready  = build_ready_and_cost(conn)
        hero.update(ready)

        # Canonical wins for every overlapping metric — hero, stats, and
        # funnel all read the same numbers as dashboard + ops.
        hero["ready"]         = canon["ready"]
        hero["qualified"]     = canon["ready"]   # legacy alias
        hero["strong"]        = canon["strong_match"]
        hero["contacts"]      = canon["total_contacts"]
        hero["verified"]      = canon["verified"]
        hero["pending"]       = canon["queue_pending"]
        hero["avg_score"]     = canon["avg_score"]
        hero["sec_total"]     = canon["sec_indexed"]
        hero["sec_icp"]       = canon["sec_icp"]
        hero["tier1"]         = canon["tier1_firms"]
        hero["tier2"]         = canon["tier2_firms"]
        funnel["ready"]       = canon["ready"]
        funnel["qualified"]   = canon["ready"]
        for stage in funnel.get("stages", []):
            if stage["key"] == "ready":
                stage["count"] = canon["ready"]
            elif stage["key"] == "tier1_active":
                stage["count"] = canon["tier1_firms"]
            elif stage["key"] == "tier2_monitored":
                stage["count"] = canon["tier2_firms"]
            elif stage["key"] == "sec_indexed":
                stage["count"] = canon["sec_indexed"]
            elif stage["key"] == "icp_qualified":
                stage["count"] = canon["sec_icp"]

        stats = dict(canon)
        # Legacy aliases the analytics.html template still reads
        stats.update({
            "qualified":     canon["ready"],
            "contacts":      canon["total_contacts"],
            "scored":        hero["scored"],
            "strong":        canon["strong_match"],
            "pending":       canon["queue_pending"],
            "approved":      hero["approved"],
            "cost_per_lead": hero.get("cost_per_lead", 0),
        })
        payload = {
            "generated_at":        datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "hero":                hero,
            "stats":               stats,
            "score_histogram":     build_score_histogram(conn),
            "dimensions":          build_dimensions(conn),
            "funnel":              funnel,
            "by_firm_type":        build_by_firm_type(conn),
            "by_buying_stage":     build_by_buying_stage(conn),
            "by_signal_type":      build_by_signal_type(conn),
            "signal_conversion":   build_signal_conversion(conn),
            "competitor_landscape":build_competitor_landscape(conn),
            "aum_coverage":        build_aum_coverage(conn),
            "bottleneck":          build_bottleneck(funnel),
            "top_ready":           build_top_ready(conn),
            "flagged":             build_flagged(conn),
            "router_usage":        build_router_usage(conn),
        }
    finally:
        conn.close()

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

    h = payload["hero"]
    print(f"✓ analytics_data.json — {h['scored']} scored, "
          f"{h['strong']} strong, {h['pending']} pending, avg {h['avg_score']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
