"""scoring/firmographic_scorer.py

Offline, no-network scorer for Tier-2 monitored firms. Uses only firm-level
data (AUM, firm_type) — no Claude, Hunter, Exa, Apify, Twitter. Zero cost.

Score formula (matches the 4-dimension weighting used by the Claude scorer):
    overall = 0.30·icp_fit + 0.25·ai_readiness + 0.25·reachability
              + 0.20·signal_freshness

For tier-2 firms there are no contacts and no signals yet, so:
    ai_readiness     = 50  (baseline — firm-level AI posture unknown)
    reachability     = 20  (no verified email, no named decision-maker)
    signal_freshness = 0   (no attached signal)

ICP fit is derived from AUM buckets; capped at 50 if firm_type is unknown.
"""
from __future__ import annotations

import json

BASELINE_AI         = 50.0
BASELINE_REACH      = 20.0
BASELINE_FRESHNESS  = 0.0

LABEL   = "Uncontacted"
ACTION  = "Monitor"
SCORED_BY = "firmographic_only"

REASONING = (
    "Firmographic-only score. Tier-2 monitored firm — no contact, no signal, "
    "no LLM pass. ICP bucket from AUM; reachability and freshness baseline. "
    "Promote on trigger (signal, hiring post, press) for full enrichment."
)


def _icp_from_aum(aum: float | None) -> float:
    if aum is None:
        return 45.0
    try:
        aum = float(aum)
    except (TypeError, ValueError):
        return 45.0
    if aum >= 50e9:  return 95.0
    if aum >= 10e9:  return 85.0
    if aum >= 5e9:   return 75.0
    if aum >= 1e9:   return 65.0
    if aum >= 500e6: return 55.0
    return 45.0


def score_firm(firm: dict) -> dict:
    firm_type = (firm.get("firm_type") or "").strip().lower()
    icp = _icp_from_aum(firm.get("aum_reported"))
    # Cap ICP at 50 if firm_type is missing/unknown.
    if firm_type in ("", "unknown", "none"):
        icp = min(icp, 50.0)

    overall = (
        0.30 * icp
        + 0.25 * BASELINE_AI
        + 0.25 * BASELINE_REACH
        + 0.20 * BASELINE_FRESHNESS
    )
    return {
        "score": round(overall, 2),
        "icp_fit": icp,
        "ai_readiness": BASELINE_AI,
        "reachability": BASELINE_REACH,
        "signal_freshness": BASELINE_FRESHNESS,
        "label": LABEL,
        "action": ACTION,
        "reasoning": REASONING,
        "missing": "named contact, verified email, recent signal",
        "sections_used": [],
        "scored_by": SCORED_BY,
    }


def persist_firmographic_score(conn, *, firm_id: int, contact_id: int,
                               result: dict) -> int:
    """Upsert-ish: if a firmographic score already exists for this contact, skip."""
    existing = conn.execute(
        """SELECT id FROM scores
            WHERE contact_id=? AND scored_by='firmographic_only'
            LIMIT 1""",
        (contact_id,),
    ).fetchone()
    if existing:
        return existing["id"]

    cur = conn.execute(
        """INSERT INTO scores
           (contact_id, firm_id, score, icp_fit, ai_readiness, reachability,
            signal_freshness, label, action, reasoning, missing,
            sections_used, scored_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (contact_id, firm_id, result["score"], result["icp_fit"],
         result["ai_readiness"], result["reachability"],
         result["signal_freshness"], result["label"], result["action"],
         result["reasoning"], result["missing"],
         json.dumps(result["sections_used"]), result["scored_by"]),
    )
    return cur.lastrowid
