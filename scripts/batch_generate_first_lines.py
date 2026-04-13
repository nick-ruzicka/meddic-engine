#!/usr/bin/env python3
"""scripts/batch_generate_first_lines.py

Populate first_line for every queued contact that has an account_brief
but no first_line yet. Uses Claude Haiku for cost efficiency.

Selection criteria:
    - outreach_queue.first_line IS NULL or empty
    - scores.score >= 55
    - scores.account_brief IS NOT NULL
    - contacts.do_not_contact = 0

Rate-limited: 0.3s sleep between calls.
"""

from __future__ import annotations

import json
import logging
import re
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv()

from database import get_db

HAIKU_MODEL = "claude-haiku-4-5-20251001"
VOICE_SKILL_PATH = os.path.join(ROOT, "config", "skills", "voice", "outreach_voice_.md")
SLEEP_SEC = 0.3

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("batch_first_lines")


def load_voice_skill() -> str:
    try:
        with open(VOICE_SKILL_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return (
            "You write short, specific cold-outreach first lines for  - "
            "an AI platform for financial institutions. Lead with a compliance "
            "or data-sovereignty angle when objections exist. No em dashes."
        )


SQL = """
SELECT
    q.id AS queue_id, q.contact_id, q.firm_id,
    c.name AS contact_name, c.title, c.do_not_contact,
    c.research_json,
    f.name AS firm_name, f.firm_type, f.buying_stage,
    f.competitor, f.has_objections,
    s.score, s.label, s.reasoning, s.account_brief,
    COALESCE(sig_q.signal_type,  sig_c.signal_type,  sig_f.signal_type)  AS signal_type,
    COALESCE(sig_q.content,      sig_c.content,      sig_f.content)      AS signal_content,
    COALESCE(sig_q.signal_date,  sig_c.signal_date,  sig_f.signal_date)  AS signal_date
FROM outreach_queue q
JOIN contacts c ON c.id = q.contact_id
JOIN firms    f ON f.id = q.firm_id
LEFT JOIN scores s ON s.id = q.score_id
LEFT JOIN signals sig_q ON sig_q.id = q.signal_id
LEFT JOIN signals sig_c ON sig_c.id = (
    SELECT id FROM signals WHERE contact_id = c.id
     ORDER BY COALESCE(signal_date, created_at) DESC LIMIT 1)
LEFT JOIN signals sig_f ON sig_f.id = (
    SELECT id FROM signals WHERE firm_id = f.id
     ORDER BY COALESCE(signal_date, created_at) DESC LIMIT 1)
WHERE (q.first_line IS NULL OR q.first_line = '')
  AND s.score >= 55
  AND s.account_brief IS NOT NULL
  AND COALESCE(c.do_not_contact, 0) = 0
ORDER BY s.score DESC
"""


def _first_linkedin_post(research_json_raw):
    if not research_json_raw:
        return None
    try:
        rj = json.loads(research_json_raw)
    except Exception:
        return None
    posts = (rj or {}).get("recent_posts") or []
    for p in posts:
        if "linkedin.com/posts" in (p.get("url") or "").lower():
            return p
    return None


def build_user_message(row, brief: dict) -> str:
    sig_line = (
        f"{(row['signal_content'] or '').strip()[:300]} "
        f"({row['signal_type']}"
        f"{', ' + row['signal_date'] if row['signal_date'] else ''})"
        if row["signal_content"] else "No recent signal attached"
    )
    angle = (brief or {}).get("angle", "")
    why_now = (brief or {}).get("why_now", "")
    li_post = _first_linkedin_post(row["research_json"]) if "research_json" in row.keys() else None
    li_block = ""
    if li_post:
        li_block = (
            "\nCONTACT'S RECENT LINKEDIN POST:\n"
            f"\"{(li_post.get('snippet') or '').strip()[:400]}\"\n"
            f"Posted: {li_post.get('date') or 'unknown'}\n"
            "Reference this post specifically in the first line if relevant — "
            "quoting a phrase or naming the announcement makes the opener "
            "unmistakably tied to this person.\n"
        )
    return (
        f"Firm: {row['firm_name']} ({row['firm_type'] or 'unknown'}, "
        f"{row['buying_stage'] or 'unknown stage'})\n"
        f"Contact: {row['contact_name']}, {row['title'] or 'unknown title'}\n"
        f"Competitor context: {row['competitor'] or 'none'}\n"
        f"Top signal: {sig_line}\n"
        f"Score: {row['score'] or 0} - {row['label'] or ''}\n"
        f"Why now: {why_now[:280]}\n"
        f"Pitch angle: {angle[:280]}\n"
        f"{li_block}\n"
        "Generate a complete cold email. Return ONLY JSON, no markdown:\n"
        "{\n"
        '  "subject": "max 8 words, specific not generic, no clickbait, lowercase preferred",\n'
        '  "body": "complete email under 100 words total. Opener references their specific situation. '
        "1-2 sentences explaining the relevant  workflow for their firm type. "
        "1 sentence CTA - specific ask, not generic (e.g. open to a 15-min call this week? / "
        'happy to share the Oak Hill case study?)"\n'
        "}\n"
        "No em dashes. No 'I wanted to reach out'. No 'revolutionary' or 'game-changing'. "
        "Sound like a practitioner, not a vendor. "
        f"{'Lead with compliance/data-sovereignty assurance. ' if row['has_objections'] else ''}"
    )


def main() -> int:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("ANTHROPIC_API_KEY not set - aborting")
        return 1

    try:
        import anthropic
    except ImportError:
        log.error("anthropic SDK not installed")
        return 1

    voice_skill = load_voice_skill()
    client = anthropic.Anthropic(api_key=api_key)

    conn = get_db()
    try:
        rows = conn.execute(SQL).fetchall()
    finally:
        conn.close()

    log.info(f"found {len(rows)} queue rows needing first_line")
    if not rows:
        return 0

    ok, failed = 0, 0
    for i, row in enumerate(rows, 1):
        try:
            brief = json.loads(row["account_brief"]) if row["account_brief"] else {}
        except Exception:
            brief = {}

        user_message = build_user_message(row, brief)
        try:
            resp = client.messages.create(
                model=HAIKU_MODEL,
                max_tokens=500,
                temperature=0.6,
                system=voice_skill,
                messages=[{"role": "user", "content": user_message}],
            )
            raw = "".join(getattr(b, "text", "") for b in resp.content).strip()
            # Strip any code fences Claude snuck in; isolate the JSON object
            raw = re.sub(r"^```(?:json)?\s*|```\s*$", "", raw, flags=re.M).strip()
            m = re.search(r"\{[\s\S]*\}", raw)
            if not m:
                failed += 1
                log.warning(f"[{i}/{len(rows)}] no JSON in response qid={row['queue_id']}")
                continue
            try:
                parsed = json.loads(m.group(0))
            except json.JSONDecodeError as e:
                failed += 1
                log.warning(f"[{i}/{len(rows)}] bad JSON qid={row['queue_id']}: {e}")
                continue
            subject = (parsed.get("subject") or "").replace("—", "-").strip()
            body    = (parsed.get("body") or "").replace("—", "-").strip()
            if not subject or not body:
                failed += 1
                log.warning(f"[{i}/{len(rows)}] missing subject/body qid={row['queue_id']}")
                continue
            text = json.dumps({"subject": subject, "body": body})
            if not text:
                failed += 1
                log.warning(f"[{i}/{len(rows)}] empty generation for qid={row['queue_id']}")
                continue

            conn = get_db()
            try:
                conn.execute(
                    "UPDATE outreach_queue SET first_line = ?, updated_at = datetime('now') WHERE id = ?",
                    (text, row["queue_id"]),
                )
                conn.commit()
            finally:
                conn.close()
            ok += 1
            log.info(f"[{i}/{len(rows)}] qid={row['queue_id']} {row['contact_name']} - OK")
        except Exception as e:
            failed += 1
            log.warning(f"[{i}/{len(rows)}] qid={row['queue_id']} failed: {e}")
        time.sleep(SLEEP_SEC)

    log.info(f"DONE - {ok} populated, {failed} failed")
    print(f"✓ first lines: {ok} populated, {failed} failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
