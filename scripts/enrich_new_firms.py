#!/usr/bin/env python3
"""One-shot enrichment for newly-promoted tier-1 firms with no contacts yet."""
from __future__ import annotations
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

from database import get_db
from enrichment.exa_enricher import enrich_firm


def main():
    conn = get_db()
    new = [dict(r) for r in conn.execute("""
        SELECT f.id, f.name, f.domain FROM firms f
        LEFT JOIN contacts co ON co.firm_id = f.id
        WHERE f.tier = 1 AND f.domain != ''
          AND f.domain NOT LIKE '%youtube%'
          AND f.domain NOT LIKE '%linkedin%'
          AND f.domain NOT LIKE '%podcasts%'
        GROUP BY f.id HAVING COUNT(co.id) = 0
        ORDER BY f.aum_reported DESC NULLS LAST
    """).fetchall()]
    print(f"Enriching {len(new)} new firms via Exa + Hunter…\n")
    t = {"firms": 0, "added": 0, "emails": 0}
    for f in new:
        print(f"→ {f['name']} ({f['domain']})", flush=True)
        try:
            r = enrich_firm(conn, f["id"])
            t["firms"] += 1; t["added"] += r["added"]; t["emails"] += r["emails_found"]
            print(f"  + {r['added']} contacts, {r['emails_found']} verified emails", flush=True)
        except Exception as e:
            print(f"  ! {e}", flush=True)
    print(f"\nDONE · firms={t['firms']} · contacts_added={t['added']} · verified_emails={t['emails']}")


if __name__ == "__main__":
    main()
