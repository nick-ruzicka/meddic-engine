#!/usr/bin/env python3
"""One-off: regenerate account_brief for tier-1 firms with multiple contacts
so the new 'why_this_contact' + 'thread' fields are populated."""

from __future__ import annotations

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv()

from database import get_db
from scoring.contact_scorer import generate_account_brief

TARGET_FIRMS = ("Francisco Partners", "Blackstone", "Ares Management", "Evercore")
SLEEP = 0.4


def main() -> int:
    conn0 = get_db()
    rows = conn0.execute(f"""
        SELECT s.id AS score_id, s.score, s.label, s.action, s.reasoning, s.missing,
               c.id AS cid, c.name AS contact_name, c.title, c.email_verified, c.linkedin_url,
               f.id AS fid, f.name AS firm_name, f.firm_type, f.buying_stage,
               f.competitor, f.has_objections, f.aum_reported, f.tier, f.aum_range,
               f.geography, f._status, f.notes
          FROM scores s
          JOIN contacts c ON c.id = s.contact_id
          JOIN firms    f ON f.id = s.firm_id
         WHERE f.name IN ({','.join('?'*len(TARGET_FIRMS))})
         ORDER BY f.name, s.score DESC
         LIMIT 20
    """, TARGET_FIRMS).fetchall()
    conn0.close()

    print(f"Regenerating briefs for {len(rows)} scored contacts across {len(TARGET_FIRMS)} firms")
    ok = fail = 0
    for r in rows:
        conn = get_db()
        sig = conn.execute("""
            SELECT signal_type, content, signal_date FROM signals
             WHERE contact_id=? OR firm_id=?
             ORDER BY COALESCE(signal_date, created_at) DESC LIMIT 1
        """, (r["cid"], r["fid"])).fetchone()
        conn.close()
        firm = {
            "id": r["fid"], "name": r["firm_name"], "firm_type": r["firm_type"],
            "buying_stage": r["buying_stage"], "competitor": r["competitor"],
            "has_objections": r["has_objections"], "aum_reported": r["aum_reported"],
            "tier": r["tier"], "aum_range": r["aum_range"],
            "geography": r["geography"], "_status": r["_status"],
            "notes": r["notes"],
        }
        contact = {"id": r["cid"], "name": r["contact_name"], "title": r["title"],
                   "email_verified": r["email_verified"], "linkedin_url": r["linkedin_url"]}
        score_result = {"score": r["score"], "label": r["label"], "action": r["action"],
                        "reasoning": r["reasoning"], "missing": r["missing"]}
        signals = [dict(sig)] if sig else []

        brief = generate_account_brief(firm, contact, signals, score_result)
        if brief:
            # Retry write against concurrent writers
            import sqlite3 as _sq
            wrote = False
            for attempt in range(10):
                try:
                    w = get_db()
                    w.execute("UPDATE scores SET account_brief = ? WHERE id = ?",
                              (json.dumps(brief), r["score_id"]))
                    w.commit()
                    w.close()
                    wrote = True
                    break
                except _sq.OperationalError:
                    time.sleep(1.5)
            if not wrote:
                fail += 1
                print(f"  ✗ {r['firm_name']} / {r['contact_name']} (db busy)")
                continue
            ok += 1
            print(f"  ✓ {r['firm_name']} / {r['contact_name']}")
        else:
            fail += 1
            print(f"  ✗ {r['firm_name']} / {r['contact_name']} (empty)")
        time.sleep(SLEEP)
    print(f"\nDONE — {ok} regenerated, {fail} failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
