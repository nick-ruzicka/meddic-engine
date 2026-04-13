"""api/routes.py

Flask Blueprint for the MEDDIC Engine dashboard API.
Registered by app.py under prefix /api.

All endpoints require `X-API-Key: $API_KEY` header.

Endpoints:
    GET  /contacts          List queued contacts with score + signal context
    POST /approve           Approve queue row, save edited first_line
    POST /skip              Skip queue row with reason
    POST /flag              Flag queue row for human review
    GET  /stats             Pipeline counters
    POST /run               Shell out to main.py with a mode flag
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import Blueprint, jsonify, request

from database import get_db

# In-memory rate limiter for /run. Single-process only; not safe across
# Flask workers, but the demo deployment runs one process.
_RUN_COOLDOWN_S = 120
_last_run: dict[str, float] = {}

logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__)

VALID_RUN_MODES = {"collect", "score", "queue", "full", "sample"}
VALID_STATUSES = {"pending", "approved", "skipped", "flagged", "all"}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_PY = os.path.join(ROOT, "main.py")


# ─────────────────────────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────────────────────────

def require_api_key(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        expected = os.getenv("API_KEY")
        if not expected:
            return jsonify({"error": "API_KEY not configured on server"}), 500
        provided = request.headers.get("X-API-Key", "")
        if provided != expected:
            return jsonify({"error": "unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapper


# ─────────────────────────────────────────────────────────────────────────────
# GET /contacts
# ─────────────────────────────────────────────────────────────────────────────

@api_bp.route("/contacts", methods=["GET"])
@require_api_key
def list_contacts():
    status    = (request.args.get("status") or "pending").lower()
    min_score = float(request.args.get("min_score", 0))
    limit     = max(1, min(int(request.args.get("limit", 50)), 500))

    if status not in VALID_STATUSES:
        return jsonify({"error": f"invalid status; use one of {sorted(VALID_STATUSES)}"}), 400

    status_clause = "" if status == "all" else "AND q.status = ?"
    params: list = [min_score]
    if status != "all":
        params.append(status)
    params.append(limit)

    sql = f"""
        SELECT
            q.id                AS queue_id,
            q.status            AS status,
            q.first_line        AS first_line,
            q.signal_id         AS signal_id,
            c.id                AS contact_id,
            c.name              AS contact_name,
            c.title             AS title,
            c.email             AS email,
            c.email_verified    AS email_verified,
            f.id                AS firm_id,
            f.name              AS firm_name,
            f.firm_type         AS firm_type,
            s.score             AS score,
            s.label             AS label,
            s.action            AS action,
            s.reasoning         AS reasoning,
            sig.signal_type     AS signal_type,
            sig.content         AS signal_content,
            sig.signal_date     AS signal_date
        FROM outreach_queue q
        JOIN contacts c ON c.id = q.contact_id
        JOIN firms    f ON f.id = q.firm_id
        LEFT JOIN scores  s   ON s.id = q.score_id
        LEFT JOIN signals sig ON sig.id = q.signal_id
        WHERE COALESCE(s.score, 0) >= ?
        {status_clause}
        ORDER BY COALESCE(s.score, 0) DESC, q.id DESC
        LIMIT ?
    """

    conn = get_db()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    out = []
    for r in rows:
        out.append({
            "queue_id":       r["queue_id"],
            "contact_id":     r["contact_id"],
            "firm_id":        r["firm_id"],
            "firm_name":      r["firm_name"],
            "firm_type":      r["firm_type"],
            "contact_name":   r["contact_name"],
            "title":          r["title"] or "",
            "email":          r["email"] or "",
            "email_verified": bool(r["email_verified"]),
            "score":          float(r["score"]) if r["score"] is not None else 0.0,
            "label":          r["label"] or "",
            "action":         r["action"] or "",
            "reasoning":      r["reasoning"] or "",
            "signal_type":    r["signal_type"] or "",
            "signal_content": r["signal_content"] or "",
            "signal_date":    r["signal_date"] or "",
            "first_line":     r["first_line"] or "",
            "status":         r["status"],
        })

    return jsonify({"contacts": out, "count": len(out)})


# ─────────────────────────────────────────────────────────────────────────────
# POST /approve
# ─────────────────────────────────────────────────────────────────────────────

@api_bp.route("/approve", methods=["POST"])
@require_api_key
def approve():
    body = request.get_json(silent=True) or {}
    queue_id = body.get("queue_id")
    first_line = (body.get("first_line") or "").strip()[:500]
    if not queue_id:
        return jsonify({"error": "queue_id required"}), 400

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, contact_id, first_line FROM outreach_queue WHERE id = ?",
            (queue_id,),
        ).fetchone()
        if not row:
            return jsonify({"error": "queue_id not found"}), 404

        original = row["first_line"] or ""
        conn.execute(
            """UPDATE outreach_queue
               SET status = 'approved',
                   first_line_edited = ?,
                   updated_at = datetime('now')
               WHERE id = ?""",
            (first_line, queue_id),
        )
        conn.execute(
            """INSERT INTO review_decisions
               (queue_id, contact_id, decision, original_line, edited_line)
               VALUES (?, ?, 'approved', ?, ?)""",
            (queue_id, row["contact_id"], original, first_line),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────────────────────
# POST /skip
# ─────────────────────────────────────────────────────────────────────────────

@api_bp.route("/skip", methods=["POST"])
@require_api_key
def skip():
    body = request.get_json(silent=True) or {}
    queue_id = body.get("queue_id")
    reason = (body.get("reason") or "").strip()
    if not queue_id:
        return jsonify({"error": "queue_id required"}), 400

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, contact_id FROM outreach_queue WHERE id = ?",
            (queue_id,),
        ).fetchone()
        if not row:
            return jsonify({"error": "queue_id not found"}), 404

        conn.execute(
            """UPDATE outreach_queue
               SET status = 'skipped',
                   skip_reason = ?,
                   updated_at = datetime('now')
               WHERE id = ?""",
            (reason, queue_id),
        )
        conn.execute(
            """INSERT INTO review_decisions
               (queue_id, contact_id, decision, skip_reason)
               VALUES (?, ?, 'skipped', ?)""",
            (queue_id, row["contact_id"], reason),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────────────────────
# POST /flag
# ─────────────────────────────────────────────────────────────────────────────

@api_bp.route("/flag", methods=["POST"])
@require_api_key
def flag():
    body = request.get_json(silent=True) or {}
    queue_id = body.get("queue_id")
    if not queue_id:
        return jsonify({"error": "queue_id required"}), 400

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, contact_id FROM outreach_queue WHERE id = ?",
            (queue_id,),
        ).fetchone()
        if not row:
            return jsonify({"error": "queue_id not found"}), 404

        conn.execute(
            """UPDATE outreach_queue
               SET status = 'flagged', updated_at = datetime('now')
               WHERE id = ?""",
            (queue_id,),
        )
        conn.execute(
            """INSERT INTO review_decisions (queue_id, contact_id, decision)
               VALUES (?, ?, 'flagged')""",
            (queue_id, row["contact_id"]),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────────────────────
# POST /dnc — mark contact Do Not Contact, remove from queue
# ─────────────────────────────────────────────────────────────────────────────

@api_bp.route("/dnc", methods=["POST"])
@require_api_key
def dnc():
    body = request.get_json(silent=True) or {}
    contact_id = body.get("contact_id")
    if not contact_id:
        return jsonify({"error": "contact_id required"}), 400

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id FROM contacts WHERE id = ?", (contact_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "contact_id not found"}), 404

        conn.execute(
            "UPDATE contacts SET do_not_contact = 1, updated_at = datetime('now') WHERE id = ?",
            (contact_id,),
        )
        # Flip any pending queue rows for this contact to 'skipped' with dnc reason
        conn.execute(
            """UPDATE outreach_queue
               SET status = 'skipped',
                   skip_reason = 'dnc',
                   updated_at = datetime('now')
             WHERE contact_id = ? AND status = 'pending'""",
            (contact_id,),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True, "contact_id": contact_id})


# ─────────────────────────────────────────────────────────────────────────────
# POST /activate — promote a Tier-2 firm to Tier 1
# ─────────────────────────────────────────────────────────────────────────────

@api_bp.route("/activate", methods=["POST"])
@require_api_key
def activate():
    body = request.get_json(silent=True) or {}
    firm_id = body.get("firm_id")
    if not firm_id:
        return jsonify({"error": "firm_id required"}), 400

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, tier FROM firms WHERE id = ?", (firm_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "firm_id not found"}), 404
        # TODO: a follow-up job should pick up newly-promoted firms and run
        # hunter + exa + linkedin enrichment + Claude scoring on them.
        conn.execute(
            "UPDATE firms SET tier = 1, updated_at = datetime('now') WHERE id = ?",
            (firm_id,),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True, "firm_id": firm_id, "tier": 1})


# ─────────────────────────────────────────────────────────────────────────────
# GET /stats
# ─────────────────────────────────────────────────────────────────────────────

@api_bp.route("/stats", methods=["GET"])
@require_api_key
def stats():
    conn = get_db()
    try:
        def one(sql: str, params=()) -> int:
            return conn.execute(sql, params).fetchone()[0]

        totals = {
            "total_firms":    one("SELECT COUNT(*) FROM firms"),
            "total_contacts": one("SELECT COUNT(*) FROM contacts"),
            "total_signals":  one("SELECT COUNT(*) FROM signals"),
            "pending":   one("SELECT COUNT(*) FROM outreach_queue WHERE status = ?", ("pending",)),
            "approved":  one("SELECT COUNT(*) FROM outreach_queue WHERE status = ?", ("approved",)),
            "skipped":   one("SELECT COUNT(*) FROM outreach_queue WHERE status = ?", ("skipped",)),
            "flagged":   one("SELECT COUNT(*) FROM outreach_queue WHERE status = ?", ("flagged",)),
            "strong_match": one("SELECT COUNT(*) FROM scores WHERE score >= 75"),
            "good_match":   one("SELECT COUNT(*) FROM scores WHERE score >= 55 AND score < 75"),
        }
    finally:
        conn.close()

    return jsonify(totals)


# ─────────────────────────────────────────────────────────────────────────────
# POST /run
# ─────────────────────────────────────────────────────────────────────────────

_VOICE_SKILL_PATH = os.path.join(ROOT, "config", "skills", "voice", "outreach_voice_.md")
HAIKU_MODEL = "claude-haiku-4-5-20251001"

# Daily brief — server-side cache, 30-min TTL
_brief_cache: dict = {"brief": None, "generated_at": None, "signal_count": 0}
BRIEF_TTL_SECONDS = 1800


@api_bp.route("/daily-brief", methods=["GET"])
@require_api_key
def daily_brief():
    now = datetime.now(timezone.utc)
    cached_at = _brief_cache.get("_at")
    if cached_at and now - cached_at < timedelta(seconds=BRIEF_TTL_SECONDS):
        return jsonify({
            "brief": _brief_cache["brief"],
            "signal_count": _brief_cache["signal_count"],
            "generated_at": _brief_cache["generated_at"],
            "cached": True,
        })

    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT s.signal_type, s.content, s.signal_date,
                   f.name AS firm_name,
                   c.name AS contact_name, c.title AS contact_title
              FROM signals s
              JOIN firms f ON s.firm_id = f.id
         LEFT JOIN contacts c ON s.contact_id = c.id
             WHERE f.tier = 1
               AND s.signal_date >= datetime('now', '-48 hours')
               AND length(s.content) > 40
               AND s.content NOT LIKE '%UNITED STATES%'
               AND s.content NOT LIKE '%Table of Contents%'
          ORDER BY s.signal_date DESC
             LIMIT 15
        """).fetchall()
    finally:
        conn.close()

    if not rows:
        result = {"brief": None, "signal_count": 0, "generated_at": now.isoformat()}
        _brief_cache.update(result)
        _brief_cache["_at"] = now
        return jsonify(result)

    signal_text = ""
    for s in rows:
        contact = (
            f" (re: {s['contact_name']}, {s['contact_title'] or 'unknown title'})"
            if s["contact_name"] else ""
        )
        signal_text += f"- [{(s['signal_type'] or 'signal').upper()}] {s['firm_name']}{contact}: {(s['content'] or '')[:150]}\n"

    prompt = (
        "You are a sales intelligence analyst for , an AI platform for "
        "PE/IB/hedge funds.\n\n"
        f"Signals from the last 48h across tier-1 target accounts:\n\n{signal_text}\n"
        "Write a 100-word sales brief for a  AE. The panel it displays in "
        "is narrow (320px) so keep lines short.\n\n"
        "Format:\n"
        "- 1 sentence: overall picture this week\n"
        "- 2-3 bullets: accounts to prioritize TODAY + specific reason + recommended angle (use \u2192)\n"
        "- 1 sentence: any patterns or watch-outs\n\n"
        "Rules: name firms and contacts specifically. No generic phrases. "
        "Sound like a sharp analyst."
    )

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return jsonify({"brief": None, "error": "ANTHROPIC_API_KEY not set",
                        "signal_count": len(rows)}), 500

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        brief_text = "".join(getattr(b, "text", "") for b in resp.content).strip()
    except Exception as e:
        logger.exception("daily_brief generation failed")
        return jsonify({"brief": None, "error": str(e), "signal_count": len(rows)}), 502

    result = {
        "brief": brief_text,
        "signal_count": len(rows),
        "generated_at": now.isoformat(),
    }
    _brief_cache.update(result)
    _brief_cache["_at"] = now
    return jsonify(result)


def _load_voice_skill() -> str:
    try:
        with open(_VOICE_SKILL_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logger.warning(f"could not read voice skill: {e}")
        return (
            "You write short, specific cold-outreach first lines for  — "
            "an AI platform for financial institutions. Lead with a compliance "
            "or data-sovereignty angle when objections exist. No em dashes."
        )


@api_bp.route("/generate_first_line", methods=["POST"])
@require_api_key
def generate_first_line():
    body = request.get_json(silent=True) or {}
    queue_id = body.get("queue_id")
    if not queue_id:
        return jsonify({"error": "queue_id required"}), 400

    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT
                q.id AS queue_id, q.contact_id, q.firm_id,
                c.name AS contact_name, c.title AS title,
                f.name AS firm_name, f.firm_type, f.buying_stage,
                f.competitor, f.has_objections,
                s.score, s.label, s.reasoning,
                COALESCE(sig_q.signal_type,  sig_c.signal_type,  sig_f.signal_type)  AS signal_type,
                COALESCE(sig_q.content,      sig_c.content,      sig_f.content)      AS signal_content,
                COALESCE(sig_q.signal_date,  sig_c.signal_date,  sig_f.signal_date)  AS signal_date
            FROM outreach_queue q
            JOIN contacts c ON c.id = q.contact_id
            JOIN firms    f ON f.id = q.firm_id
            LEFT JOIN scores  s     ON s.id = q.score_id
            LEFT JOIN signals sig_q ON sig_q.id = q.signal_id
            LEFT JOIN signals sig_c ON sig_c.id = (
                SELECT id FROM signals WHERE contact_id = c.id
                ORDER BY COALESCE(signal_date, created_at) DESC LIMIT 1)
            LEFT JOIN signals sig_f ON sig_f.id = (
                SELECT id FROM signals WHERE firm_id = f.id
                ORDER BY COALESCE(signal_date, created_at) DESC LIMIT 1)
            WHERE q.id = ?
            """,
            (queue_id,),
        ).fetchone()
        if not row:
            return jsonify({"error": "queue_id not found"}), 404
    finally:
        conn.close()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY not configured"}), 500

    try:
        import anthropic
    except ImportError:
        return jsonify({"error": "anthropic SDK not installed"}), 500

    signal_line = (
        f"{(row['signal_content'] or '').strip()[:300]} "
        f"({row['signal_type']}{', ' + row['signal_date'] if row['signal_date'] else ''})"
        if row["signal_content"] else "No recent signal attached"
    )
    user_message = (
        f"Firm: {row['firm_name']} ({row['firm_type'] or 'unknown'}, "
        f"{row['buying_stage'] or 'unknown stage'})\n"
        f"Contact: {row['contact_name']}, {row['title'] or 'unknown title'}\n"
        f"Competitor context: {row['competitor'] or 'none'}\n"
        f"Top signal: {signal_line}\n"
        f"Score: {row['score'] or 0} - {row['label'] or ''}\n"
        f"Reasoning: {(row['reasoning'] or '').strip()[:500]}\n\n"
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

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=500,
            temperature=0.6,
            system=_load_voice_skill(),
            messages=[{"role": "user", "content": user_message}],
        )
        raw = "".join(getattr(b, "text", "") for b in resp.content).strip()
        raw = re.sub(r"^```(?:json)?\s*|```\s*$", "", raw, flags=re.M).strip()
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return jsonify({"error": "no JSON in Claude response", "raw": raw[:300]}), 502
        try:
            parsed = json.loads(m.group(0))
        except Exception as e:
            return jsonify({"error": f"bad JSON from Claude: {e}", "raw": raw[:300]}), 502
        subject = (parsed.get("subject") or "").replace("—", "-").strip()
        body    = (parsed.get("body") or "").replace("—", "-").strip()
        if not subject or not body:
            return jsonify({"error": "missing subject or body", "raw": raw[:300]}), 502
        first_line = json.dumps({"subject": subject, "body": body})
    except Exception as e:
        logger.exception("generate_first_line failed")
        return jsonify({"error": f"claude error: {e}"}), 502

    if not first_line:
        return jsonify({"error": "empty generation"}), 502

    conn = get_db()
    try:
        conn.execute(
            "UPDATE outreach_queue SET first_line = ?, updated_at = datetime('now') WHERE id = ?",
            (first_line, queue_id),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True, "first_line": first_line, "subject": subject, "body": body, "model": HAIKU_MODEL})


@api_bp.route("/generate_brief", methods=["POST"])
@require_api_key
def generate_brief():
    body = request.get_json(silent=True) or {}
    queue_id = body.get("queue_id")
    if not queue_id:
        return jsonify({"error": "queue_id required"}), 400

    conn = get_db()
    row = conn.execute("""
        SELECT q.id AS queue_id, q.score_id,
               c.id AS contact_id, c.name AS contact_name, c.title,
               c.email_verified, c.linkedin_url,
               f.id AS firm_id, f.name AS firm_name, f.firm_type,
               f.buying_stage, f.competitor, f.has_objections,
               f.aum_reported, f.tier, f.aum_range, f.geography,
               f._status, f.notes,
               s.score, s.label, s.action, s.reasoning, s.missing
          FROM outreach_queue q
          JOIN contacts c ON c.id = q.contact_id
          JOIN firms    f ON f.id = q.firm_id
          LEFT JOIN scores s ON s.id = q.score_id
         WHERE q.id = ?
    """, (queue_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "queue_id not found"}), 404

    # Latest signal for context
    sig = conn.execute("""
        SELECT signal_type, content, signal_date FROM signals
         WHERE contact_id=? OR firm_id=?
         ORDER BY COALESCE(signal_date, created_at) DESC LIMIT 1
    """, (row["contact_id"], row["firm_id"])).fetchone()
    conn.close()

    from scoring.contact_scorer import generate_account_brief
    firm = {
        "id": row["firm_id"], "name": row["firm_name"], "firm_type": row["firm_type"],
        "buying_stage": row["buying_stage"], "competitor": row["competitor"],
        "has_objections": row["has_objections"], "aum_reported": row["aum_reported"],
        "tier": row["tier"], "aum_range": row["aum_range"],
        "geography": row["geography"], "_status": row["_status"],
        "notes": row["notes"],
    }
    contact = {"id": row["contact_id"], "name": row["contact_name"], "title": row["title"],
               "email_verified": row["email_verified"], "linkedin_url": row["linkedin_url"]}
    score_result = {"score": row["score"], "label": row["label"], "action": row["action"],
                    "reasoning": row["reasoning"], "missing": row["missing"]}
    signals = [dict(sig)] if sig else []

    brief = generate_account_brief(firm, contact, signals, score_result)
    if not brief:
        return jsonify({"error": "brief generation failed"}), 502

    conn = get_db()
    try:
        conn.execute("UPDATE scores SET account_brief=? WHERE id=?",
                     (json.dumps(brief), row["score_id"]))
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True, "brief": brief})


@api_bp.route("/healthz", methods=["GET"])
def healthz():
    """Unauthenticated liveness probe — DB reachable + row count."""
    try:
        conn = get_db()
        try:
            firms = conn.execute("SELECT count(*) FROM firms").fetchone()[0]
        finally:
            conn.close()
        return jsonify({
            "status":    "ok",
            "firms":     firms,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        })
    except Exception as e:
        logger.exception("healthz failed")
        return jsonify({"status": "error", "error": str(e)}), 500


@api_bp.route("/run", methods=["POST"])
@require_api_key
def run_pipeline():
    body = request.get_json(silent=True) or {}
    mode = (body.get("mode") or "").lower()
    if mode not in VALID_RUN_MODES:
        return jsonify({"error": f"mode must be one of {sorted(VALID_RUN_MODES)}"}), 400

    now = time.time()
    last = _last_run.get(mode, 0)
    if now - last < _RUN_COOLDOWN_S:
        wait_s = int(_RUN_COOLDOWN_S - (now - last))
        return jsonify({
            "ok": False,
            "error": f"Rate limited — wait {wait_s}s before re-running '{mode}'",
        }), 429
    _last_run[mode] = now

    if mode == "sample":
        cmd = [sys.executable, MAIN_PY, "--full", "--sample"]
    else:
        cmd = [sys.executable, MAIN_PY, f"--{mode}"]

    log_dir = os.path.join(ROOT, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"run_{mode}.log")
    try:
        # Fire-and-forget: caller polls /stats for progress. Output goes to a
        # per-mode log file so hangs and traces are visible after the fact.
        log_f = open(log_path, "w")
        subprocess.Popen(cmd, cwd=ROOT, stdout=log_f, stderr=log_f)
    except Exception as e:
        logger.exception("failed to launch pipeline")
        return jsonify({"error": str(e)}), 500

    return jsonify({"ok": True, "mode": mode, "log": log_path})
