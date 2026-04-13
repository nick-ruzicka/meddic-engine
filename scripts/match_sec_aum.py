#!/usr/bin/env python3
"""scripts/match_sec_aum.py

Match each row in `firms` to one representative entity in `sec_universe`
and store that entity's aum_reported back onto firms.aum_reported.

Strategy: a SQL LIKE pattern per firm (handcrafted to isolate the flagship
registered advisor and avoid unrelated hits, e.g. "MOELIS ASSET CATALYST").
Within matched rows we pick MAX(aum_reported) so we don't double-count
parent + subsidiary entities — the largest is almost always the holdco.

Idempotent. Re-run after `--sec-load` or after editing a firm name.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from database import get_db


# Patterns chosen to hit the flagship registered advisor per firm.
# None entries = firm is not in the SEC ADV universe.
PATTERNS: dict[str, str | None] = {
    "Apollo Global Management":   "APOLLO GLOBAL MANAGEMENT%",
    "Ares Management":            "ARES MANAGEMENT LLC",
    "Baird":                      "ROBERT W. BAIRD%",
    "Blackstone":                 "BLACKSTONE INC%",
    "Brookfield Asset Management":"BROOKFIELD ASSET MANAGEMENT%",
    "Evercore":                   "EVERCORE GROUP%",
    "Francisco Partners":         "FRANCISCO PARTNERS MANAGEMENT%",
    "General Atlantic":           "GENERAL ATLANTIC%",
    "Genstar Capital":            "GENSTAR CAPITAL%",
    # M&A-advisory IBs are not registered as investment advisers in SEC ADV,
    # so "AUM in pipeline" is conceptually N/A for them (they advise on deal
    # value rather than managing AUM). Houlihan Lokey and PJT are left as None.
    "Houlihan Lokey":             None,
    "Jefferies":                  "JEFFERIES FINANCE LLC",   # credit/AM sub
    "LLR Partners":               "LLR PARTNERS%",
    "Moelis & Company":           "MOELIS & COMPANY GROUP%",
    "Oaktree Capital Management": "OAKTREE CAPITAL MANAGEMENT%",
    "PJT Partners":               None,   # not registered in SEC ADV
    "Piper Sandler":              "PIPER SANDLER%",
    "Silver Lake":                "SILVER LAKE TECHNOLOGY MANAGEMENT%",
    "Vista Equity Partners":      "VISTA EQUITY PARTNERS%",
    "Warburg Pincus":             "WARBURG PINCUS LLC",
    "William Blair":              "WILLIAM BLAIR & COMPANY%",
}


def match(conn, firm_name: str, pattern: str | None) -> tuple[float | None, str | None]:
    if pattern is None:
        return None, None
    row = conn.execute(
        """SELECT firm_name, aum_reported FROM sec_universe
           WHERE UPPER(firm_name) LIKE UPPER(?)
             AND aum_reported IS NOT NULL
           ORDER BY aum_reported DESC LIMIT 1""",
        (pattern,),
    ).fetchone()
    if not row:
        return None, None
    return float(row["aum_reported"]), row["firm_name"]


def main() -> int:
    conn = get_db()
    # Scope: tier-1 firms only. For each firm:
    #   1. If its name is in the hand-picked PATTERNS dict, match via LIKE.
    #   2. Else if it has a crd_number already set, refresh AUM via that crd.
    #   3. Else skip (do NOT wipe aum_reported — the row was populated some other way).
    firms = conn.execute(
        "SELECT id, name, crd_number, aum_reported FROM firms WHERE tier=1 ORDER BY name"
    ).fetchall()
    matched = unmatched = skipped = 0
    total = 0.0
    print(f"{'FIRM':<32} {'SEC ENTITY / CRD LOOKUP':<50} {'AUM':>18}")
    print("-" * 102)
    for f in firms:
        pattern = PATTERNS.get(f["name"])
        aum: float | None = None
        src = ""

        if pattern:
            aum, src = match(conn, f["name"], pattern)
            if not aum:
                src = f"no match for {pattern!r}"
        elif pattern is None and f["name"] in PATTERNS:
            # Explicitly marked N/A (e.g. PJT, Houlihan Lokey)
            src = "pattern=None (not in SEC ADV)"
        elif f["crd_number"]:
            row = conn.execute(
                "SELECT firm_name, aum_reported FROM sec_universe WHERE crd_number=?",
                (f["crd_number"],),
            ).fetchone()
            if row and row["aum_reported"]:
                aum = float(row["aum_reported"])
                src = f"crd={f['crd_number']} → {row['firm_name']}"
            else:
                src = f"crd={f['crd_number']} — no AUM in sec_universe"
        else:
            # No pattern, no crd — preserve whatever's there (likely a manual seed).
            if f["aum_reported"]:
                total += f["aum_reported"]
                skipped += 1
                print(f"{f['name'][:30]:<32} {'preserved (manual value)':<50} ${f['aum_reported']:>15,.0f}")
            else:
                print(f"{f['name'][:30]:<32} {'no pattern, no crd':<50} {'—':>18}")
                skipped += 1
            continue

        if aum:
            conn.execute("UPDATE firms SET aum_reported=? WHERE id=?", (aum, f["id"]))
            print(f"{f['name'][:30]:<32} {src[:48]:<50} ${aum:>15,.0f}")
            total += aum
            matched += 1
        else:
            # Explicit miss (pattern set but nothing found, or pattern=None N/A).
            # Only wipe if there's no manual value already (protect firms that were
            # populated by the promote_firms script before patterns were added).
            if not f["aum_reported"]:
                conn.execute("UPDATE firms SET aum_reported=NULL WHERE id=?", (f["id"],))
            else:
                total += f["aum_reported"]
            print(f"{f['name'][:30]:<32} {src[:48]:<50} {'—':>18}")
            unmatched += 1
    conn.commit()
    conn.close()
    print("-" * 102)
    print(f"{'TOTAL':<32} matched={matched}, unmatched={unmatched}, preserved={skipped}"
          f"{'':<9} ${total:>15,.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
