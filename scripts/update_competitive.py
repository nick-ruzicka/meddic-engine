#!/usr/bin/env python3
"""scripts/update_competitive.py

Reads competitive intelligence from SQLite, writes export/competitive_data.json.

Runs on the 10-minute cron cycle.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from competitive.models import (
    init_competitive_db,
    get_all_competitors,
    get_latest_brief,
    get_latest_trajectory,
    get_recent_signals,
)
from competitive.analysis import (
    _normalize_sourced_field,
    _normalize_recent_moves,
    _SOURCED_TEXT_FIELDS,
)
from database import get_db

OUTPUT = os.path.join(ROOT, "export", "competitive_data.json")


def _row_to_dict(row) -> dict:
    """Convert a sqlite3.Row (or None) to a plain dict."""
    if row is None:
        return {}
    return dict(row)


def _get_all_signals_last_n_days(conn, days: int = 7) -> list[dict]:
    """Return all competitor_signals from the last N days across all competitors."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        """
        SELECT cs.*, c.name AS competitor_name, c.tier
          FROM competitor_signals cs
          JOIN competitors c ON c.slug = cs.competitor_slug
         WHERE cs.detected_at >= ?
         ORDER BY cs.detected_at DESC
        """,
        (cutoff,),
    ).fetchall()
    return [dict(r) for r in rows]


def _get_all_signals_recent(conn, limit: int = 50) -> list[dict]:
    """Return the last N signals across all competitors."""
    rows = conn.execute(
        """
        SELECT cs.*, c.name AS competitor_name, c.tier
          FROM competitor_signals cs
          JOIN competitors c ON c.slug = cs.competitor_slug
         ORDER BY cs.detected_at DESC, cs.id DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def build_competitive_data() -> dict:
    """Build the full competitive intelligence payload."""
    init_competitive_db()

    conn = get_db()
    try:
        competitor_rows = get_all_competitors()

        # Sort by tier, then name (get_all_competitors orders by tier, slug — re-sort by name)
        competitor_rows = sorted(competitor_rows, key=lambda r: (r["tier"], r["name"].lower()))

        # Signals for stats: last 7 days
        week_signals = _get_all_signals_last_n_days(conn, days=7)
        signals_this_week = len(week_signals)

        # Last 50 signals across all competitors for the top-level signals list
        all_recent_signals = _get_all_signals_recent(conn, limit=50)

        competitors = []
        high_threat = 0

        for row in competitor_rows:
            slug = row["slug"]

            # Brief
            brief_row = get_latest_brief(slug)
            brief_dict = _row_to_dict(brief_row)
            brief_data = None
            brief_generated_at = None
            if brief_dict:
                raw = brief_dict.get("brief_json")
                brief_data = json.loads(raw) if raw else None
                brief_generated_at = brief_dict.get("generated_at")

                # Normalize brief to sourced format for backward compat
                if isinstance(brief_data, dict):
                    for field in _SOURCED_TEXT_FIELDS:
                        if field in brief_data:
                            brief_data[field] = _normalize_sourced_field(brief_data[field])
                    if "recent_moves" in brief_data:
                        brief_data["recent_moves"] = _normalize_recent_moves(brief_data["recent_moves"])

                    # Count high-threat briefs
                    threat = brief_data.get("threat_level", "")
                    if isinstance(threat, str) and threat.lower() == "high":
                        high_threat += 1

            # Trajectory
            traj_row = get_latest_trajectory(slug)
            traj_dict = _row_to_dict(traj_row)
            traj_data = None
            traj_generated_at = None
            if traj_dict:
                raw = traj_dict.get("trajectory_json")
                traj_data = json.loads(raw) if raw else None
                traj_generated_at = traj_dict.get("generated_at")

            # Recent signals for this competitor (last 20)
            recent_signals = get_recent_signals(slug, limit=20)
            recent_signals_list = [dict(r) for r in recent_signals]

            competitors.append({
                "slug":                 slug,
                "name":                 row["name"],
                "url":                  row["url"],
                "tier":                 row["tier"],
                "positioning":          row["positioning"],
                "url_ok":               row["url_ok"],
                "last_ingested":        row["last_ingested"],
                "brief":                brief_data,
                "brief_generated_at":   brief_generated_at,
                "trajectory":           traj_data,
                "trajectory_generated_at": traj_generated_at,
                "recent_signals":       recent_signals_list,
            })

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "stats": {
                "total_competitors": len(competitors),
                "signals_this_week": signals_this_week,
                "high_threat":       high_threat,
            },
            "competitors": competitors,
            "signals":     all_recent_signals,
        }
    finally:
        conn.close()


def main() -> int:
    payload = build_competitive_data()

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

    s = payload["stats"]
    print(
        f"✓ competitive_data.json — {s['total_competitors']} competitors, "
        f"{s['signals_this_week']} signals this week, "
        f"{s['high_threat']} high-threat"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
