#!/usr/bin/env python3
"""Score unscored contacts at newly-promoted tier-1 firms."""
from __future__ import annotations
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

from database import get_db
from scoring.contact_scorer import score_contact


def main(limit: int = 15) -> None:
    conn = get_db()
    rows = conn.execute(f"""
        SELECT co.id AS cid
          FROM contacts co
          JOIN firms f ON f.id = co.firm_id
         WHERE f.tier = 1 AND f.crd_number IS NOT NULL
           AND f.id NOT IN (1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20)
           AND co.email_verified = 1
           AND NOT EXISTS (SELECT 1 FROM scores s WHERE s.contact_id = co.id)
         ORDER BY
           CASE
             WHEN LOWER(co.title) LIKE '%chief%'     THEN 1
             WHEN LOWER(co.title) LIKE '%head of%'   THEN 1
             WHEN LOWER(co.title) LIKE '%president%' THEN 2
             WHEN LOWER(co.title) LIKE '%partner%'   THEN 3
             WHEN LOWER(co.title) LIKE '%managing%'  THEN 3
             ELSE 9
           END ASC,
           co.id ASC
         LIMIT {limit}
    """).fetchall()
    print(f"Scoring {len(rows)} unscored contacts at new tier-1 firms…", flush=True)

    for r in rows:
        row = conn.execute("""
            SELECT co.*, f.firm_type, f.buying_stage, f.competitor, f.has_objections,
                   f.aum_reported, f.tier, f.aum_range, f.geography, f._status,
                   f.notes, f.name AS fname
              FROM contacts co JOIN firms f ON f.id = co.firm_id WHERE co.id = ?
        """, (r["cid"],)).fetchone()
        firm = {"id": row["firm_id"], "name": row["fname"], "firm_type": row["firm_type"],
                "buying_stage": row["buying_stage"], "competitor": row["competitor"],
                "has_objections": row["has_objections"], "aum_reported": row["aum_reported"],
                "tier": row["tier"], "aum_range": row["aum_range"], "geography": row["geography"],
                "_status": row["_status"], "notes": row["notes"]}
        contact = {"id": row["id"], "name": row["name"], "title": row["title"],
                   "email_verified": row["email_verified"], "linkedin_url": row["linkedin_url"]}
        sigs = [dict(s) for s in conn.execute(
            "SELECT * FROM signals WHERE firm_id=? OR contact_id=? ORDER BY signal_date DESC LIMIT 3",
            (row["firm_id"], row["id"])).fetchall()]
        try:
            res = score_contact(firm, contact, sigs)
            print(f"  {firm['name'][:24]:<26} {contact['name'][:26]:<28} → {res['score']}", flush=True)
        except Exception as e:
            print(f"  FAIL {contact['name']}: {e}", flush=True)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 15)
