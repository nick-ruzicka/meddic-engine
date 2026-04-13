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
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from database import get_db, DB_PATH

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
            "health":            build_health(conn),
            "api_keys":          build_api_keys(),
            "enrichment":        build_enrichment(conn),
            "recent_scores":     build_recent_scores(conn),
            "recent_signals":    build_recent_signals(conn),
            "parse_errors":      build_parse_errors(),
            "pipeline_cadence":  build_pipeline_cadence(conn),
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
