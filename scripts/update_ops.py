#!/usr/bin/env python3
"""scripts/update_ops.py

Reads SQLite + file state, writes export/ops_data.json.

Sections:
    health          DB row counts + DB file size
    api_keys        which API keys are configured (masked)
    recent_scores   last 15 scoring events with timing proxy
    recent_signals  last 15 ingested signals
    parse_errors    tail of logs/parse_errors.jsonl
    enrichment      Hunter hit rate + email_source breakdown

Safe to run anytime. No side effects.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta as _timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from database import get_db, DB_PATH
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from metrics import canonical_stats

OUTPUT = os.path.join(ROOT, "export", "ops_data.json")
PARSE_ERRORS = os.path.join(ROOT, "logs", "parse_errors.jsonl")


def _rows(conn, sql, params=()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _masked(env_var: str) -> dict:
    val = os.getenv(env_var, "")
    return {
        "name": env_var,
        "set": bool(val),
        "length": len(val),
        "preview": (val[:4] + "…" + val[-3:]) if len(val) > 10 else ("set" if val else "—"),
    }


def build_health(conn) -> dict:
    db_size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
    tables = {}
    for t in ("firms", "contacts", "signals", "scores", "outreach_queue",
              "review_decisions", "scoring_decisions"):
        try:
            tables[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except Exception:
            tables[t] = None
    return {
        "db_path": DB_PATH,
        "db_size_kb": round(db_size / 1024, 1),
        "row_counts": tables,
    }


def build_api_keys() -> list[dict]:
    return [_masked(k) for k in (
        "ANTHROPIC_API_KEY", "HUNTER_API_KEY", "TWITTER_API_KEY",
        "APIFY_API_TOKEN", "EXA_API_KEY", "API_KEY",
    )]


def build_recent_scores(conn) -> list[dict]:
    return _rows(conn, """
        SELECT s.id, s.scored_at, f.name AS firm, c.name AS contact,
               s.score, s.label, s.action, s.signal_freshness
          FROM scores s
          JOIN contacts c ON c.id = s.contact_id
          JOIN firms    f ON f.id = s.firm_id
         WHERE COALESCE(s.scored_by,'claude') != 'firmographic_only'
         ORDER BY s.id DESC
         LIMIT 15
    """)


def build_recent_signals(conn) -> list[dict]:
    rows = _rows(conn, """
        SELECT s.id, s.signal_type, s.signal_subtype, s.author_name,
               s.signal_date, s.freshness_days, s.content, s.created_at,
               f.name AS firm
          FROM signals s
          LEFT JOIN firms f ON f.id = s.firm_id
         ORDER BY COALESCE(s.signal_date, s.created_at) DESC, s.id DESC
         LIMIT 15
    """)
    for r in rows:
        r["content"] = (r["content"] or "")[:160]
    return rows


def build_enrichment(conn) -> dict:
    # Filter placeholder tier-2 contacts out of the email waterfall — they
    # have no email source and would skew the hit-rate denominator.
    total = conn.execute(
        "SELECT COUNT(*) FROM contacts WHERE COALESCE(is_placeholder,0)=0"
    ).fetchone()[0]
    verified = conn.execute(
        "SELECT COUNT(*) FROM contacts WHERE email_verified=1 AND COALESCE(is_placeholder,0)=0"
    ).fetchone()[0]
    by_source = _rows(conn, """
        SELECT COALESCE(email_source,'(none)') AS source, COUNT(*) AS n
          FROM contacts
         WHERE COALESCE(is_placeholder,0)=0
         GROUP BY email_source
         ORDER BY n DESC
    """)
    return {
        "contacts": total,
        "verified": verified,
        "hit_rate_pct": round(100 * verified / max(total, 1), 1),
        "by_source": by_source,
    }


def build_parse_errors() -> list[dict]:
    out = []
    if not os.path.exists(PARSE_ERRORS):
        return out
    try:
        with open(PARSE_ERRORS, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines[-10:]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                out.append({"raw": line[:200]})
    except Exception:
        pass
    return out


def build_fallthrough(conn) -> dict:
    """Why tier-1 contacts miss the queue. All counts filtered to tier=1
    firms only — tier-2 placeholder contacts dominate raw counts and are
    not the ops story."""
    tier1 = "c.firm_id IN (SELECT id FROM firms WHERE tier = 1)"

    below = conn.execute(f"""
        SELECT COUNT(*) FROM contacts c
         WHERE {tier1}
           AND COALESCE(is_placeholder,0)=0
           AND EXISTS (SELECT 1 FROM scores s WHERE s.contact_id=c.id AND s.score < 50)
    """).fetchone()[0]

    no_email = conn.execute(f"""
        SELECT COUNT(*) FROM contacts c
         WHERE {tier1}
           AND COALESCE(is_placeholder,0)=0
           AND (c.email IS NULL OR c.email = '' OR COALESCE(c.email_verified,0)=0)
    """).fetchone()[0]

    no_signal = conn.execute(f"""
        SELECT COUNT(*) FROM contacts c
         WHERE {tier1}
           AND COALESCE(is_placeholder,0)=0
           AND NOT EXISTS (
               SELECT 1 FROM signals s
                WHERE s.contact_id = c.id OR s.firm_id = c.firm_id
           )
    """).fetchone()[0]

    exploring = conn.execute(f"""
        SELECT COUNT(*) FROM contacts c
          JOIN firms f ON f.id = c.firm_id
         WHERE f.tier = 1
           AND COALESCE(c.is_placeholder,0)=0
           AND LOWER(COALESCE(f.buying_stage,'')) = 'exploring'
    """).fetchone()[0]

    return {
        "below_threshold": below,
        "no_email": no_email,
        "no_signal": no_signal,
        "exploring": exploring,
    }


def build_signal_to_reach(conn) -> dict:
    """Per-firm days between the earliest scraped signal and the earliest
    verified contact. A real, schema-defensible pipeline cadence metric:
    how fast does raw signal become a reachable human? Tier-1 only."""
    rows = conn.execute("""
        SELECT f.id AS firm_id,
               (julianday(c.first_verified_at) - julianday(s.first_signal_at)) AS gap_d
          FROM firms f
          JOIN (
            SELECT firm_id, MIN(COALESCE(signal_date, created_at)) AS first_signal_at
              FROM signals
             WHERE firm_id IS NOT NULL
             GROUP BY firm_id
          ) s ON s.firm_id = f.id
          JOIN (
            SELECT firm_id, MIN(created_at) AS first_verified_at
              FROM contacts
             WHERE email_verified = 1
               AND COALESCE(is_placeholder, 0) = 0
             GROUP BY firm_id
          ) c ON c.firm_id = f.id
         WHERE f.tier = 1
    """).fetchall()

    gaps = [max(0.0, float(r["gap_d"])) for r in rows if r["gap_d"] is not None]

    def _quantile(xs, q):
        if not xs: return None
        s = sorted(xs)
        return float(s[min(len(s) - 1, int(len(s) * q))])

    tier1_total = conn.execute("SELECT COUNT(*) FROM firms WHERE tier = 1").fetchone()[0]
    return {
        "firms_measured": len(gaps),
        "tier1_firms":    tier1_total,
        "coverage_pct":   (100.0 * len(gaps) / tier1_total) if tier1_total else 0.0,
        "median_days":    _quantile(gaps, 0.50),
        "p90_days":       _quantile(gaps, 0.90),
        "avg_days":       (sum(gaps) / len(gaps)) if gaps else None,
    }


def build_source_health(conn, days: int = 14) -> dict:
    """Per-collector daily signal volume for the last N days. Returns a
    matrix: each source has a list of N ints (oldest → newest)."""
    rows = _rows(conn, f"""
        SELECT signal_type,
               DATE(COALESCE(signal_date, created_at)) AS day,
               COUNT(*) AS n
          FROM signals
         WHERE DATE(COALESCE(signal_date, created_at)) >= DATE('now', '-{days-1} days')
         GROUP BY signal_type, day
    """)
    today = datetime.now(timezone.utc).date()
    day_keys = [(today - _timedelta(days=days-1-i)).isoformat() for i in range(days)]
    # bucket by type
    per_type: dict[str, dict[str, int]] = {}
    for r in rows:
        t = r["signal_type"] or "unknown"
        per_type.setdefault(t, {})[r["day"]] = r["n"]

    # Ensure wired collectors always show up, even with zero recent activity.
    # Exa is intentionally excluded — its output is written with signal_type='press'
    # (see collectors/exa_collector.py), so a separate 'exa' row would always
    # render as not_wired and misrepresent the actual pipeline state. LinkedIn is
    # excluded until the collector is connected — surface it only once it fires.
    for t in ("press", "twitter", "hiring"):
        per_type.setdefault(t, {})

    # Has this source EVER produced a signal in the whole DB? Lets us
    # distinguish "not wired yet" from "wired but stale."
    ever_rows = conn.execute(
        "SELECT signal_type, COUNT(*) AS n FROM signals GROUP BY signal_type"
    ).fetchall()
    ever_seen = {r["signal_type"]: r["n"] for r in ever_rows if r["signal_type"]}

    status_labels = {
        "active":     "Active",
        "stale":      "Stale · last signal > 3 days ago",
        "quiet":      "Quiet · wired but no recent activity",
        "not_wired":  "Not wired · collector not configured",
    }

    out = []
    for t, daymap in per_type.items():
        series = [daymap.get(d, 0) for d in day_keys]
        total  = sum(series)
        last_nonzero = next(
            (days-1-i for i, v in enumerate(reversed(series)) if v > 0), None)
        last_active = day_keys[last_nonzero] if last_nonzero is not None else None
        ever_total = ever_seen.get(t, 0)
        if (series[-1] or series[-2] or series[-3] or 0) > 0:
            status = "active"
        elif total > 0:
            status = "stale"
        elif ever_total > 0:
            status = "quiet"
        else:
            status = "not_wired"
        out.append({
            "signal_type":    t,
            "series":         series,
            "days":           day_keys,
            "total":          total,
            "ever_total":     ever_total,
            "last_active":    last_active,
            "status":         status,
            "status_label":   status_labels[status],
        })
    # Sort: active first, then by recent volume, then by ever-seen volume.
    # Never-wired sources sink to the bottom.
    status_rank = {"active": 0, "stale": 1, "quiet": 2, "not_wired": 3}
    out.sort(key=lambda x: (status_rank[x["status"]], -x["total"], -x["ever_total"], x["signal_type"]))
    return {"days": days, "sources": out}


def build_signal_quality(conn) -> dict:
    """Per-source signal quality ranking. Simple: how many signals per source,
    how many firms they touch, and what avg score those firms get."""

    # One row per firm: best + avg Claude score.
    conn.execute("DROP TABLE IF EXISTS _sq_firm_best")
    conn.execute("""
        CREATE TEMP TABLE _sq_firm_best AS
        SELECT firm_id,
               MAX(score) AS best_score,
               AVG(score) AS avg_score
          FROM scores
         WHERE COALESCE(scored_by, 'claude') != 'firmographic_only'
         GROUP BY firm_id
    """)

    # ── per-source: volume, firms, avg score ─────────────────────────────
    sources = _rows(conn, """
        SELECT
            sig.signal_type,
            COUNT(DISTINCT sig.id)       AS total_signals,
            COUNT(DISTINCT sig.firm_id)  AS firms_touched,
            ROUND(AVG(fb.avg_score), 1)  AS avg_score,
            ROUND(AVG(sig.freshness_days), 1) AS avg_freshness_days
        FROM signals sig
        LEFT JOIN firms f ON f.id = sig.firm_id
        LEFT JOIN _sq_firm_best fb ON fb.firm_id = sig.firm_id
        WHERE f.tier = 1 OR sig.firm_id IS NULL
        GROUP BY sig.signal_type
        ORDER BY avg_score DESC NULLS LAST
    """)

    # ── top signals (one per firm, most recent) ──────────────────────────
    top_signals = _rows(conn, """
        WITH ranked AS (
            SELECT sig.id, sig.signal_type, sig.signal_subtype,
                   sig.content, sig.signal_date, sig.source_url,
                   sig.firm_id,
                   f.name AS firm,
                   COALESCE(sc_direct.score, fb.best_score) AS contact_score,
                   ROW_NUMBER() OVER (
                       PARTITION BY sig.firm_id
                       ORDER BY COALESCE(sig.signal_date, sig.created_at) DESC
                   ) AS rn
              FROM signals sig
              JOIN firms f ON f.id = sig.firm_id
              LEFT JOIN scores sc_direct ON sc_direct.contact_id = sig.contact_id
                   AND COALESCE(sc_direct.scored_by, 'claude') != 'firmographic_only'
              LEFT JOIN _sq_firm_best fb ON fb.firm_id = sig.firm_id
             WHERE f.tier = 1
               AND COALESCE(sc_direct.score, fb.best_score) IS NOT NULL
        )
        SELECT id, signal_type, signal_subtype, content, signal_date,
               source_url, firm, contact_score
          FROM ranked
         WHERE rn = 1
         ORDER BY contact_score DESC
         LIMIT 15
    """)
    for r in top_signals:
        r["content"] = (r["content"] or "")[:160]

    interpretation = _build_quality_interpretation(sources)

    conn.execute("DROP TABLE IF EXISTS _sq_firm_best")

    return {
        "sources": sources,
        "top_signals": top_signals,
        "interpretation": interpretation,
    }


def _build_quality_interpretation(sources: list[dict]) -> dict:
    """Brief, actionable interpretation of source quality. No LLM call."""

    findings: list[str] = []
    optimizations: list[str] = []

    if not sources:
        return {"findings": ["No signal data yet."], "optimizations": []}

    scored = [s for s in sources if s.get("avg_score") is not None]
    if not scored:
        return {"findings": ["Signals collected but none scored yet."], "optimizations": []}

    by_score = sorted(scored, key=lambda x: x["avg_score"], reverse=True)
    by_vol = sorted(sources, key=lambda x: x.get("total_signals") or 0, reverse=True)

    # What's working
    best = by_score[0]
    findings.append(
        f"{best['signal_type'].title()} has the highest avg score ({best['avg_score']}) "
        f"across {best['firms_touched']} firms."
    )
    if by_vol[0]["signal_type"] != best["signal_type"]:
        findings.append(
            f"{by_vol[0]['signal_type'].title()} drives volume "
            f"({by_vol[0]['total_signals']} signals) but ranks lower on quality."
        )

    # What to scale
    for s in scored:
        vol = s.get("total_signals") or 0
        avg = s["avg_score"]
        if vol < 30 and avg > 60:
            optimizations.append(
                f"**{s['signal_type'].title()}** scores well ({avg} avg) on just "
                f"{vol} signals — worth scaling up."
            )
        fresh_d = s.get("avg_freshness_days")
        if fresh_d and fresh_d > 60:
            optimizations.append(
                f"**{s['signal_type'].title()}** signals average {fresh_d:.0f} days old. "
                f"Tightening the date window would improve freshness scores."
            )

    return {"findings": findings, "optimizations": optimizations}


def build_firm_velocity(conn, days: int = 14, limit: int = 10) -> dict:
    """Top tier-1 firms by signal volume over the last N days, each with
    a daily series for a sparkline."""
    top = _rows(conn, f"""
        SELECT f.id AS firm_id, f.name AS firm, f.buying_stage AS stage,
               COUNT(s.id) AS total
          FROM firms f
          JOIN signals s ON s.firm_id = f.id
         WHERE f.tier = 1
           AND DATE(COALESCE(s.signal_date, s.created_at)) >= DATE('now', '-{days-1} days')
         GROUP BY f.id
         ORDER BY total DESC
         LIMIT ?
    """, (limit,))
    if not top:
        return {"days": days, "firms": []}

    today = datetime.now(timezone.utc).date()
    day_keys = [(today - _timedelta(days=days-1-i)).isoformat() for i in range(days)]
    firm_ids = [r["firm_id"] for r in top]
    rows = _rows(conn, f"""
        SELECT firm_id,
               DATE(COALESCE(signal_date, created_at)) AS day,
               COUNT(*) AS n
          FROM signals
         WHERE firm_id IN ({','.join('?'*len(firm_ids))})
           AND DATE(COALESCE(signal_date, created_at)) >= DATE('now', '-{days-1} days')
         GROUP BY firm_id, day
    """, firm_ids)
    per_firm: dict[int, dict[str, int]] = {}
    for r in rows:
        per_firm.setdefault(r["firm_id"], {})[r["day"]] = r["n"]

    firms = []
    for t in top:
        daymap = per_firm.get(t["firm_id"], {})
        series = [daymap.get(d, 0) for d in day_keys]
        firms.append({
            "firm":   t["firm"],
            "stage":  t["stage"] or "",
            "total":  t["total"],
            "series": series,
        })
    return {"days": days, "firms": firms, "day_keys": day_keys}


def build_pipeline_cadence(conn) -> list[dict]:
    """Scored-per-day for the last 14 days."""
    return _rows(conn, """
        SELECT DATE(scored_at) AS day, COUNT(*) AS n,
               ROUND(AVG(score),1) AS avg_score
          FROM scores
         WHERE scored_at IS NOT NULL
         GROUP BY DATE(scored_at)
         ORDER BY day DESC
         LIMIT 14
    """)


def main() -> int:
    conn = get_db()
    try:
        payload = {
            "generated_at":      datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "stats":             canonical_stats(conn),
            "health":            build_health(conn),
            "api_keys":          build_api_keys(),
            "enrichment":        build_enrichment(conn),
            "recent_scores":     build_recent_scores(conn),
            "recent_signals":    build_recent_signals(conn),
            "parse_errors":      build_parse_errors(),
            "pipeline_cadence":  build_pipeline_cadence(conn),
            "fallthrough":       build_fallthrough(conn),
            "source_health":     build_source_health(conn),
            "signal_quality":    build_signal_quality(conn),
            "firm_velocity":     build_firm_velocity(conn),
            "signal_to_reach":   build_signal_to_reach(conn),
        }
    finally:
        conn.close()

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

    h = payload["health"]["row_counts"]
    e = payload["enrichment"]
    print(f"✓ ops_data.json — DB {payload['health']['db_size_kb']} KB, "
          f"{h.get('scores',0)} scores, {h.get('signals',0)} signals, "
          f"{e['hit_rate_pct']}% verified, {len(payload['parse_errors'])} parse errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
