#!/usr/bin/env python3
"""scripts/promote_tier2.py

Promote the top 500 SEC-ADV ICP-qualified firms (by AUM) into the `firms`
table as tier=2 monitored accounts. Scores them firmographically — no
Claude / Hunter / Exa / Apify / Twitter calls.

Tier 2 contracts:
    - tier=2, buying_stage='unknown', competitor='none', has_objections=0
    - firm_type='pe' if sec_universe.is_private_fund else 'investment_bank'
    - aum_reported, website (as domain) carried over from sec_universe
    - One synthetic placeholder contact per firm (name='—', is_placeholder=1)
      so we can attach a score (scores.contact_id is NOT NULL).
      Decision: we chose option (b) — synthetic placeholder — because
      scores.contact_id cannot be made NULL without a destructive migration
      and other code (scoring_decisions, api routes) joins on it.

Firmographic score formula (see firmographic_scorer.score_firm):
    icp_fit = f(aum_reported, firm_type)
    ai_readiness=50, reachability=20, signal_freshness=0
    overall = 0.30*icp + 0.25*ai + 0.25*reach + 0.20*fresh
    label='Uncontacted', action='Monitor', scored_by='firmographic_only'

Idempotent: running twice won't duplicate firms (fuzzy-matched on name and
website/domain) and won't duplicate placeholder contacts or scores.

Usage:
    python3 scripts/promote_tier2.py [--limit 500]
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from database import get_db, init_db
from scoring.firmographic_scorer import score_firm, persist_firmographic_score

logger = logging.getLogger(__name__)

_STOPWORDS = {
    "inc", "llc", "lp", "llp", "ltd", "limited", "corp", "corporation",
    "company", "co", "group", "holdings", "the", "and", "of",
    "capital", "partners", "management", "advisors", "llc.", "&",
}


def _norm_name(s: str) -> str:
    if not s:
        return ""
    tokens = re.findall(r"[a-z0-9]+", s.lower())
    tokens = [t for t in tokens if t not in _STOPWORDS]
    return "".join(tokens)


def _domain(website: str) -> str:
    if not website:
        return ""
    try:
        host = urlparse(website if "://" in website else "http://" + website).hostname or ""
        return host.lower().removeprefix("www.")
    except Exception:
        return ""


def _load_existing_fingerprints(conn) -> tuple[set[str], set[str]]:
    names, domains = set(), set()
    for row in conn.execute("SELECT name, domain FROM firms").fetchall():
        if row["name"]:
            names.add(_norm_name(row["name"]))
        if row["domain"]:
            d = _domain(row["domain"])
            if d:
                domains.add(d)
    return names, domains


def _fetch_candidates(conn, limit: int) -> list[dict]:
    # Pull a wider pool than `limit` so duplicate skips don't short us.
    rows = conn.execute(
        """
        SELECT crd_number, firm_name, website, aum_reported,
               is_private_fund, state, city
          FROM sec_universe
         WHERE icp_fit = 1
           AND firm_name IS NOT NULL
           AND TRIM(firm_name) != ''
         ORDER BY COALESCE(aum_reported, 0) DESC
         LIMIT ?
        """,
        (max(limit * 4, limit + 500),),
    ).fetchall()
    return [dict(r) for r in rows]


def _firm_type_from_sec(is_private_fund: int | None) -> str:
    return "pe" if (is_private_fund and int(is_private_fund) > 0) else "investment_bank"


def _ensure_placeholder_contact(conn, firm_id: int) -> int:
    """Return contact_id for a placeholder contact on firm_id, creating one if needed."""
    row = conn.execute(
        "SELECT id FROM contacts WHERE firm_id=? AND is_placeholder=1 LIMIT 1",
        (firm_id,),
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        """INSERT INTO contacts (firm_id, name, title, role_type, is_placeholder)
           VALUES (?, '—', 'No contact — firmographic only', 'placeholder', 1)""",
        (firm_id,),
    )
    return cur.lastrowid


def promote(limit: int = 500) -> dict:
    init_db()
    conn = get_db()
    try:
        existing_names, existing_domains = _load_existing_fingerprints(conn)
        cands = _fetch_candidates(conn, limit)

        promoted = 0
        skipped_dup = 0
        scored = 0

        for c in cands:
            if promoted >= limit:
                break
            fname = (c["firm_name"] or "").strip()
            if not fname:
                continue
            nkey = _norm_name(fname)
            dkey = _domain(c.get("website") or "")

            if (nkey and nkey in existing_names) or (dkey and dkey in existing_domains):
                skipped_dup += 1
                continue

            firm_type = _firm_type_from_sec(c.get("is_private_fund"))
            cur = conn.execute(
                """INSERT INTO firms
                   (name, domain, firm_type, tier, aum_reported,
                    buying_stage, competitor, has_objections, _status, notes)
                   VALUES (?, ?, ?, 2, ?, 'unknown', 'none', 0, 'prospect', ?)""",
                (fname, dkey or None, firm_type, c.get("aum_reported"),
                 f"SEC CRD {c['crd_number']}"),
            )
            firm_id = cur.lastrowid

            contact_id = _ensure_placeholder_contact(conn, firm_id)

            # Firmographic score
            firm_row = {
                "id": firm_id, "name": fname, "firm_type": firm_type,
                "aum_reported": c.get("aum_reported"),
            }
            result = score_firm(firm_row)
            persist_firmographic_score(conn, firm_id=firm_id,
                                       contact_id=contact_id, result=result)
            scored += 1
            promoted += 1

            # Remember so within-batch dups don't re-fire
            if nkey:
                existing_names.add(nkey)
            if dkey:
                existing_domains.add(dkey)

            if promoted % 100 == 0:
                conn.commit()
                print(f"  · {promoted} promoted, {skipped_dup} skipped so far")

        conn.commit()
    finally:
        conn.close()

    return {"promoted": promoted, "skipped_dup": skipped_dup, "scored": scored}


def main() -> int:
    ap = argparse.ArgumentParser(description="Promote top-AUM SEC-ADV firms to tier 2")
    ap.add_argument("--limit", type=int, default=500)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s — %(message)s")

    stats = promote(limit=args.limit)
    print(f"\n✓ Tier-2 promotion complete")
    print(f"  promoted:    {stats['promoted']}")
    print(f"  skipped dup: {stats['skipped_dup']}")
    print(f"  scored:      {stats['scored']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
