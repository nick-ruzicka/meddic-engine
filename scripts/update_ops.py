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
         ORDER BY s.id DESC LIMIT 15
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


def build_enrichment_timing(conn) -> dict:
    """Time between contact row creation and the update that set a
    verified email — a wall-clock proxy for enrichment latency. Tier-1
    non-placeholder contacts only. Rows where updated_at == created_at
    (enriched in the same write) are reported separately as 'same_write'
    since elapsed time is meaningless for them."""
    rows = conn.execute("""
        SELECT COALESCE(NULLIF(c.email_source,''), 'unknown') AS source,
               (julianday(c.updated_at) - julianday(c.created_at)) * 86400.0 AS gap_s
          FROM contacts c
          JOIN firms f ON f.id = c.firm_id
         WHERE f.tier = 1
           AND COALESCE(c.is_placeholder, 0) = 0
           AND c.email_verified = 1
           AND c.email IS NOT NULL AND c.email <> ''
    """).fetchall()

    by_source: dict[str, list[float]] = {}
    same_write: dict[str, int] = {}
    for r in rows:
        src = r["source"]
        gap = r["gap_s"] or 0.0
        if gap < 1.0:
            same_write[src] = same_write.get(src, 0) + 1
        else:
            by_source.setdefault(src, []).append(gap)

    def _quantile(xs: list[float], q: float) -> float:
        if not xs: return 0.0
        s = sorted(xs)
        i = min(len(s) - 1, int(len(s) * q))
        return float(s[i])

    sources = []
    for src in set(list(by_source.keys()) + list(same_write.keys())):
        vals = by_source.get(src, [])
        sources.append({
            "source":     src,
            "measured":   len(vals),
            "same_write": same_write.get(src, 0),
            "median_s":   round(_quantile(vals, 0.50), 1),
            "p90_s":      round(_quantile(vals, 0.90), 1),
            "avg_s":      round(sum(vals) / len(vals), 1) if vals else 0.0,
        })
    sources.sort(key=lambda x: -(x["measured"] + x["same_write"]))

    all_vals: list[float] = [v for xs in by_source.values() for v in xs]
    total_same = sum(same_write.values())
    return {
        "total_verified":      sum(len(v) for v in by_source.values()) + total_same,
        "measured":            len(all_vals),
        "same_write":          total_same,
        "median_s":             round(_quantile(all_vals, 0.50), 1),
        "p90_s":                round(_quantile(all_vals, 0.90), 1),
        "avg_s":                round(sum(all_vals)/len(all_vals), 1) if all_vals else 0.0,
        "sources":              sources,
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

    # Ensure known sources always show up, even with zero activity
    for t in ("press", "twitter", "linkedin", "hiring", "exa"):
        per_type.setdefault(t, {})

    out = []
    for t, daymap in per_type.items():
        series = [daymap.get(d, 0) for d in day_keys]
        total = sum(series)
        last_nonzero = next((days-1-i for i, v in enumerate(reversed(series)) if v > 0), None)
        last_active = day_keys[last_nonzero] if last_nonzero is not None else None
        out.append({
            "signal_type": t,
            "series":      series,
            "days":        day_keys,
            "total":       total,
            "last_active": last_active,
            "status":      ("active"   if (series[-1] or series[-2] or series[-3] or 0) > 0
                            else "stale" if total > 0
                            else "silent"),
        })
    out.sort(key=lambda x: (-x["total"], x["signal_type"]))
    return {"days": days, "sources": out}


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
            "firm_velocity":     build_firm_velocity(conn),
            "enrichment_timing": build_enrichment_timing(conn),
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
