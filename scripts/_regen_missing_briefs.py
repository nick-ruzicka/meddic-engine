#!/usr/bin/env python3
"""One-off: regenerate account_brief for any score rows linked to queue rows
that have no first_line yet and no brief. Unblocks batch_generate_first_lines."""
from __future__ import annotations
import os, sys, time, json
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))
from database import get_db
from scoring.contact_scorer import generate_account_brief


def main() -> int:
    conn = get_db()
    rows = conn.execute("""
        SELECT s.id AS score_id, s.score, s.label, s.action, s.reasoning, s.missing,
               c.id AS cid, c.name AS contact_name, c.title, c.email_verified, c.linkedin_url,
               f.id AS fid, f.name AS firm_name, f.firm_type, f.buying_stage,
               f.competitor, f.has_objections, f.aum_reported, f.tier, f.aum_range,
               f.geography, f._status, f.notes
          FROM outreach_queue q
          JOIN scores s   ON s.id = q.score_id
          JOIN contacts c ON c.id = s.contact_id
          JOIN firms    f ON f.id = s.firm_id
         WHERE (q.first_line IS NULL OR q.first_line='')
           AND s.score >= 55
           AND (s.account_brief IS NULL OR s.account_brief='')
    """).fetchall()
    conn.close()
    print(f"Regenerating briefs for {len(rows)} score rows")
    ok = fail = 0
    for i, r in enumerate(rows, 1):
        conn = get_db()
        sig = conn.execute(
            "SELECT signal_type, content, signal_date FROM signals "
            "WHERE contact_id=? OR firm_id=? "
            "ORDER BY COALESCE(signal_date, created_at) DESC LIMIT 1",
            (r["cid"], r["fid"])
        ).fetchone()
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
        score   = {"score": r["score"], "label": r["label"], "action": r["action"],
                   "reasoning": r["reasoning"], "missing": r["missing"]}
        signals = [dict(sig)] if sig else []
        try:
            brief = generate_account_brief(firm, contact, signals, score)
        except Exception as e:
            print(f"  [{i}/{len(rows)}] FAIL {r['firm_name']}/{r['contact_name']}: {e}")
            brief = None
        if not brief:
            fail += 1
            time.sleep(0.4)
            continue
        conn = get_db()
        conn.execute("UPDATE scores SET account_brief=? WHERE id=?",
                     (json.dumps(brief), r["score_id"]))
        conn.commit()
        conn.close()
        ok += 1
        if i % 10 == 0:
            print(f"  progress {i}/{len(rows)} ok={ok} fail={fail}")
        time.sleep(0.4)
    print(f"DONE ok={ok} fail={fail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
