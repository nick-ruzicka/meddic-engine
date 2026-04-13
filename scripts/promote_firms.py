#!/usr/bin/env python3
"""scripts/promote_firms.py

Batch-promote firms from sec_universe into the firms table as tier=1
active targets. Two batches:

BATCH 1 — hand-picked megafunds matched via firm_name LIKE.
BATCH 2 — top 15 private-fund advisers by AUM_reported not already in firms.

For each new firm we insert: name, crd_number, aum_reported, tier=1,
firm_type (inferred), buying_stage=exploring, _status=prospect,
competitor=none, geography=US. Website carried from sec_universe if present.

Idempotent — skips any crd already linked to a firm row.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from database import get_db


# ─── Batch 1 — hand-picked megafunds ────────────────────────────────────────
# Short display name → LIKE pattern (targets the flagship registered advisor).
HAND_PICKED: list[tuple[str, str, str]] = [
    # (display_name, LIKE pattern, firm_type)
    ("KKR",                        "KKR & CO%",                       "pe"),
    ("Carlyle Group",              "CARLYLE%INVESTMENT MANAGEMENT%",  "pe"),
    ("TPG",                        "TPG GLOBAL%",                     "pe"),
    ("Bain Capital",               "BAIN CAPITAL%",                   "pe"),
    ("Advent International",       "ADVENT INTERNATIONAL%",           "pe"),
    ("Thoma Bravo",                "THOMA BRAVO%",                    "pe"),
    ("Permira",                    "PERMIRA%",                        "pe"),
    ("General Atlantic",           "GENERAL ATLANTIC%",               "pe"),
    ("Vista Equity Partners",      "VISTA EQUITY PARTNERS%",          "pe"),
    ("Warburg Pincus",             "WARBURG PINCUS%",                 "pe"),
    ("Apollo Global Management",   "APOLLO GLOBAL MANAGEMENT%",       "pe"),
    ("Ares Management",            "ARES MANAGEMENT LLC",             "credit_fund"),
    ("Brookfield Asset Management","BROOKFIELD ASSET MANAGEMENT%",    "pe"),
    ("Oaktree Capital",            "OAKTREE CAPITAL MANAGEMENT%",     "credit_fund"),
    ("Francisco Partners",         "FRANCISCO PARTNERS MANAGEMENT%",  "pe"),
]


def _best_match(conn, pattern: str) -> dict | None:
    row = conn.execute(
        """SELECT crd_number, firm_name, aum_reported, website
             FROM sec_universe
            WHERE UPPER(firm_name) LIKE UPPER(?)
              AND aum_reported IS NOT NULL
            ORDER BY aum_reported DESC LIMIT 1""",
        (pattern,),
    ).fetchone()
    return dict(row) if row else None


def _already_promoted(conn, crd: int) -> int | None:
    r = conn.execute(
        "SELECT id FROM firms WHERE crd_number = ? LIMIT 1", (crd,)
    ).fetchone()
    return r["id"] if r else None


def _firm_by_name(conn, name: str) -> int | None:
    r = conn.execute(
        "SELECT id FROM firms WHERE LOWER(name) = LOWER(?) LIMIT 1", (name,)
    ).fetchone()
    return r["id"] if r else None


def _insert_firm(conn, name: str, firm_type: str, crd: int,
                 aum: float, website: str | None) -> int:
    domain = ""
    if website:
        w = website.strip().lower()
        w = w.replace("https://", "").replace("http://", "").split("/")[0]
        if w.startswith("www."):
            w = w[4:]
        domain = w
    cur = conn.execute(
        """INSERT INTO firms
             (name, domain, firm_type, tier, aum_range, geography,
              _status, competitor, buying_stage, has_objections,
              crd_number, aum_reported)
           VALUES (?, ?, ?, 1, ?, 'US', 'prospect', 'none', 'exploring',
                   0, ?, ?)""",
        (name, domain, firm_type,
         f"${aum/1e9:.1f}B" if aum else None, crd, aum),
    )
    return cur.lastrowid


def _update_firm_crd_aum(conn, firm_id: int, crd: int,
                         aum: float, firm_type: str | None = None) -> None:
    if firm_type:
        conn.execute(
            "UPDATE firms SET crd_number=?, aum_reported=?, firm_type=COALESCE(firm_type,?), tier=1 WHERE id=?",
            (crd, aum, firm_type, firm_id),
        )
    else:
        conn.execute(
            "UPDATE firms SET crd_number=?, aum_reported=?, tier=1 WHERE id=?",
            (crd, aum, firm_id),
        )


def batch1(conn) -> tuple[int, int, list[str]]:
    promoted = linked = 0
    failed: list[str] = []
    print("\n── BATCH 1: Hand-picked megafunds ──")
    for display, pattern, firm_type in HAND_PICKED:
        m = _best_match(conn, pattern)
        if not m:
            print(f"  ✗ {display:<30} no match for pattern {pattern!r}")
            failed.append(display)
            continue
        crd, sec_name, aum, website = m["crd_number"], m["firm_name"], m["aum_reported"], m.get("website")
        existing_by_crd  = _already_promoted(conn, crd)
        existing_by_name = _firm_by_name(conn, display)
        if existing_by_crd:
            print(f"  = {display:<30} already linked (crd={crd})")
            continue
        if existing_by_name:
            # Link the existing firm row to the SEC record + refresh AUM.
            _update_firm_crd_aum(conn, existing_by_name, crd, aum, firm_type)
            linked += 1
            print(f"  ↪ {display:<30} linked existing row → crd={crd}  ${aum/1e9:.1f}B")
            continue
        fid = _insert_firm(conn, display, firm_type, crd, aum, website)
        promoted += 1
        print(f"  + {display:<30} inserted firm_id={fid}  crd={crd}  ${aum/1e9:.1f}B  ({sec_name[:40]})")
    return promoted, linked, failed


def batch2(conn, n: int = 15, min_aum: float = 5e9) -> int:
    print(f"\n── BATCH 2: Top {n} private-fund advisers by AUM (>${min_aum/1e9:.0f}B, not already active) ──")
    excl_crds = {r["crd_number"] for r in conn.execute(
        "SELECT crd_number FROM firms WHERE crd_number IS NOT NULL"
    ).fetchall()}
    params = tuple(excl_crds) or (0,)
    rows = conn.execute(f"""
        SELECT crd_number, firm_name, aum_reported, website
          FROM sec_universe
         WHERE icp_fit = 1
           AND is_private_fund = 1
           AND aum_reported IS NOT NULL
           AND aum_reported > ?
           AND crd_number NOT IN ({','.join('?'*len(params))})
         ORDER BY aum_reported DESC
         LIMIT ?
    """, (min_aum, *params, n)).fetchall()

    inserted = 0
    for r in rows:
        name = _titlecase(r["firm_name"])
        if _firm_by_name(conn, name):
            continue
        fid = _insert_firm(conn, name, "pe", r["crd_number"],
                           r["aum_reported"], r["website"])
        inserted += 1
        print(f"  + {name[:46]:<46} firm_id={fid}  crd={r['crd_number']}  ${r['aum_reported']/1e9:.1f}B")
    return inserted


def _titlecase(s: str) -> str:
    # Convert ALL CAPS SEC filings to Title Case, preserving common abbreviations.
    KEEP = {"LLC", "L.P.", "L.L.C.", "LP", "INC", "INC.", "CO.", "COMPANY",
            "MANAGEMENT", "PARTNERS", "CAPITAL", "GROUP", "ADVISORS",
            "ADVISERS", "HOLDINGS", "GLOBAL", "USA", "US", "UK", "II", "III", "IV"}
    return " ".join(
        w if w.upper() in KEEP else w.capitalize()
        for w in s.strip().split()
    )


def main() -> int:
    conn = get_db()
    try:
        b1_new, b1_linked, b1_failed = batch1(conn)
        b2_new = batch2(conn)
        conn.commit()

        total_firms = conn.execute("SELECT COUNT(*) FROM firms").fetchone()[0]
        tier1       = conn.execute("SELECT COUNT(*) FROM firms WHERE tier=1").fetchone()[0]
        aum_t       = (conn.execute("SELECT COALESCE(SUM(aum_reported),0) FROM firms WHERE tier=1").fetchone()[0] or 0) / 1e12

        print("\n── Summary ──")
        print(f"  Batch 1: inserted {b1_new}, linked {b1_linked}, failed {len(b1_failed)}")
        if b1_failed:
            print(f"           failed to match: {', '.join(b1_failed)}")
        print(f"  Batch 2: inserted {b2_new}")
        print(f"  firms total:  {total_firms}")
        print(f"  firms tier-1: {tier1}")
        print(f"  tier-1 AUM:   ${aum_t:.2f}T")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
