"""scoring/contact_scorer.py

Score a (firm, contact, signals) triple against 's ICP using Claude.

Public API:
    score_contact(firm, contact, signals, mock_mode=False) -> dict

Flow:
    1. Build account dict for the skill router
    2. Route sections via config/skill_router.build_scoring_prompt
    3. Either mock or call Claude sonnet-4
    4. Parse structured response
    5. Persist to `scores` and `scoring_decisions`
    6. Return the full score record
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from typing import Optional

# ── Make config/ importable so skill_router can resolve skill_constants ──────
_HERE = os.path.dirname(os.path.abspath(__file__))
_CONFIG_DIR = os.path.join(_HERE, "..", "config")
sys.path.insert(0, os.path.abspath(_CONFIG_DIR))

from dotenv import load_dotenv

load_dotenv()

from database import get_db
from utils.helpers import now_iso, truncate, safe_json
from skill_router import build_scoring_prompt  # from config/

logger = logging.getLogger(__name__)

ANTHROPIC_MODEL = "claude-sonnet-4-6"
BRIEF_MODEL     = "claude-haiku-4-5-20251001"
PARSE_ERROR_LOG = os.path.join(_HERE, "..", "logs", "parse_errors.jsonl")
MAX_TOKENS = 600
TEMPERATURE = 0

SCORE_FIELDS = ("score", "icp_fit", "ai_readiness", "reachability", "signal_freshness")
TEXT_FIELDS  = ("label", "reasoning", "missing", "action")


# ─────────────────────────────────────────────────────────────────────────────
# Account assembly
# ─────────────────────────────────────────────────────────────────────────────

def _build_account(firm: dict, signals: list[dict]) -> dict:
    signal_types = sorted({s.get("signal_type") for s in signals if s.get("signal_type")})
    return {
        "firm_type":      firm.get("firm_type", ""),
        "competitor":     firm.get("competitor") or "none",
        "buying_stage":   firm.get("buying_stage") or "unknown",
        "signal_types":   signal_types,
        "has_objections": bool(firm.get("has_objections", 0)),
    }


def _build_user_message(firm: dict, contact: dict, signals: list[dict]) -> str:
    parts = [
        "## Firm",
        f"- Name: {firm.get('name')}",
        f"- Type: {firm.get('firm_type')}",
        f"- Tier: {firm.get('tier')}",
        f"- AUM: {firm.get('aum_range')}",
        f"- Geography: {firm.get('geography')}",
        f"- Competitor: {firm.get('competitor') or 'none'}",
        f"- Buying stage: {firm.get('buying_stage') or 'unknown'}",
        f"-  status: {firm.get('_status') or 'prospect'}",
        f"- Has objections: {bool(firm.get('has_objections', 0))}",
        f"- Notes: {truncate(firm.get('notes') or '', 300)}",
        "",
        "## Contact",
        f"- Name: {contact.get('name')}",
        f"- Title: {contact.get('title')}",
        f"- Role type: {contact.get('role_type')}",
        f"- Email verified: {bool(contact.get('email_verified', 0))}",
        f"- LinkedIn: {contact.get('linkedin_url') or '—'}",
        "",
        f"## Signals ({len(signals)})",
    ]
    if not signals:
        parts.append("- (none)")
    for s in signals:
        parts.append(
            f"- [{s.get('signal_type')}/{s.get('signal_subtype') or '—'}] "
            f"fresh={s.get('freshness_days', '?')}d stage={s.get('buying_stage') or '—'} "
            f"| {truncate(s.get('content') or '', 200)}"
        )
    parts += [
        "",
        "Return scores for this contact using the output format specified in the "
        "scoring rules. Respond with the plain key:value lines only — no JSON, no markdown.",
    ]
    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Mock scorer — deterministic, no API cost
# ─────────────────────────────────────────────────────────────────────────────

def _mock_score(firm: dict, contact: dict, signals: list[dict]) -> dict:
    if firm.get("_status") == "customer":
        return _score_payload(0, 0, 0, 0, 0, "Weak Match", "Existing customer.", "n/a", "skip")

    ft_bonus = {"pe": 90, "credit": 88, "hedge_fund": 78, "investment_bank": 82}.get(
        firm.get("firm_type", ""), 60
    )
    freshness = 80 if any((s.get("freshness_days") or 999) <= 30 for s in signals) else 45
    reach = 85 if contact.get("email_verified") else (60 if contact.get("email") else 30)
    ai_ready = min(100, 50 + 10 * len(signals))
    if firm.get("has_objections"):
        ai_ready = max(20, ai_ready - 15)

    icp = ft_bonus
    composite = round(0.30 * icp + 0.25 * ai_ready + 0.25 * reach + 0.20 * freshness, 1)
    label, action = _label_for(composite)
    return _score_payload(composite, icp, ai_ready, reach, freshness, label,
                          "Mock-mode deterministic score.",
                          "Live Claude call would refine.", action)


def _label_for(score: float) -> tuple[str, str]:
    if score >= 75:   return "Strong Match", "Approve"
    if score >= 55:   return "Good Match",   "Review"
    if score >= 35:   return "Moderate Match", "Enrich"
    return "Weak Match", "Flag"


def _score_payload(score, icp, ai, reach, fresh, label, reasoning, missing, action) -> dict:
    return {
        "score": float(score),
        "icp_fit": float(icp),
        "ai_readiness": float(ai),
        "reachability": float(reach),
        "signal_freshness": float(fresh),
        "label": label,
        "reasoning": reasoning,
        "missing": missing,
        "action": action,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Claude parsing
# ─────────────────────────────────────────────────────────────────────────────

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _parse_claude_response(text: str) -> dict:
    """Parse `key: value` lines from Claude output. Missing keys fall back."""
    parsed: dict = {}
    for line in text.splitlines():
        line = line.strip().lstrip("-*# ").strip()
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower().replace(" ", "_")
        val = val.strip().strip("`").strip()
        if not val:
            continue
        if key in SCORE_FIELDS:
            m = _NUM_RE.search(val)
            if m:
                try:
                    parsed[key] = max(0.0, min(100.0, float(m.group(0))))
                except ValueError:
                    pass
        elif key in TEXT_FIELDS:
            parsed[key] = val

    # If Claude only returned dimensions (no `score`), compute it
    if "score" not in parsed and all(k in parsed for k in SCORE_FIELDS[1:]):
        parsed["score"] = round(
            0.30 * parsed["icp_fit"] + 0.25 * parsed["ai_readiness"]
            + 0.25 * parsed["reachability"] + 0.20 * parsed["signal_freshness"], 1
        )

    if "score" in parsed and "label" not in parsed:
        parsed["label"], parsed["action"] = _label_for(parsed["score"])

    # Ensure every field is present — fall back to zeros / Flag
    if "score" not in parsed:
        logger.warning("Claude response unparseable; defaulting to 0/Flag")
        _log_parse_error(text)
        return _score_payload(0, 0, 0, 0, 0, "Weak Match",
                              "Claude response unparseable.",
                              f"raw: {truncate(text, 200)}", "Flag")

    for k in SCORE_FIELDS:
        parsed.setdefault(k, 0.0)
    parsed.setdefault("label", "Weak Match")
    parsed.setdefault("reasoning", "")
    parsed.setdefault("missing", "")
    parsed.setdefault("action", "Flag")
    return parsed


def _log_parse_error(raw_text: str) -> None:
    try:
        os.makedirs(os.path.dirname(PARSE_ERROR_LOG), exist_ok=True)
        with open(PARSE_ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "at": now_iso(),
                "model": ANTHROPIC_MODEL,
                "raw": raw_text,
            }) + "\n")
    except Exception as e:
        logger.debug(f"could not write parse_errors.jsonl: {e}")


def _call_claude(system_prompt: str, user_message: str) -> str:
    import anthropic
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    # SDK returns list of content blocks; concatenate text blocks
    return "".join(getattr(b, "text", "") for b in resp.content)


# ─────────────────────────────────────────────────────────────────────────────
# Persistence
# ─────────────────────────────────────────────────────────────────────────────

_BRIEF_SYSTEM = (
    "You are building a one-page account intelligence brief for a  "
    "sales rep.  sells Matrix — an AI platform that processes entire "
    "document sets simultaneously for PE firms, investment banks, hedge funds, "
    "and credit funds. Purpose-built for due diligence, VDR analysis, IC memos, "
    "portfolio monitoring.\n\n"
    "Key proof points you can reference:\n"
    "- Oak Hill Advisors: 6x ROI, Sonja Renander MD U.S. Credit stated publicly "
    "at Private Markets AI Summit 2025\n"
    "- $25T AUM of firms currently using \n"
    "- Centerview Partners and Charlesbank: confirmed customers\n"
    "- KKR, Carlyle: confirmed early adopters\n"
    "- SOC2 Type II, data never leaves customer environment, private deployment "
    "on customer's cloud\n\n"
    "Compliance is the #1 sales barrier in finance. Lead objection handling "
    "with data sovereignty, not product.\n\n"
    "Be specific. Be brief. Be actionable. Sound like a practitioner, not a "
    "vendor. Never use em dashes."
)


def generate_account_brief(firm: dict, contact: dict, signals: list[dict],
                           score_result: dict) -> dict | None:
    """Second Haiku pass: produce a JSON brief with why_now / objection /
    angle / proof_point. Returns None on any failure."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    top_sig = signals[0] if signals else None
    sig_line = (top_sig.get("content") or "")[:300] if top_sig else "no signals attached"
    aum = firm.get("aum_reported")
    aum_line = f"${aum/1e9:.1f}B" if aum else "not confirmed"

    user = f"""FIRM: {firm.get('name')} ({firm.get('firm_type')})
AUM: {aum_line}
CONTACT: {contact.get('name')}, {contact.get('title')}
BUYING STAGE: {firm.get('buying_stage') or 'unknown'}
COMPETITOR: {firm.get('competitor') if firm.get('competitor') and firm.get('competitor') != 'none' else 'not identified'}
SIGNALS: {sig_line}
SCORE: {score_result.get('score')} — {score_result.get('label')}
REASONING: {(score_result.get('reasoning') or '')[:400]}

Generate a brief with EXACTLY these four fields.
Return ONLY valid JSON, no markdown, no explanation:
{{
  "why_now": "1-2 sentences on why outreach makes sense specifically this week — reference the buying stage, any signals, peer firm activity",
  "objection": "The most likely objection from this specific contact/firm type, plus one sentence on how to handle it",
  "angle": "The specific pitch angle for this firm — which workflow pain, which proof point, how to frame  for their exact situation",
  "proof_point": "The single most relevant customer reference or data point for this firm type"
}}"""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=BRIEF_MODEL, max_tokens=700, temperature=0.3,
            system=_BRIEF_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        raw = "".join(getattr(b, "text", "") for b in resp.content).strip()
        # Strip markdown code fences if any
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE)
        # Isolate first JSON object
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        data = json.loads(m.group(0))
        for k in ("why_now", "objection", "angle", "proof_point"):
            if not data.get(k):
                return None
            data[k] = str(data[k]).replace("—", "-").strip()
        return data
    except Exception as e:
        logger.warning(f"brief generation failed for {firm.get('name')}/{contact.get('name')}: {e}")
        return None


def _persist(firm: dict, contact: dict, signals: list[dict],
             result: dict, sections_used: list[str]) -> int:
    conn = get_db()
    try:
        brief_json = None
        if not result.get("_mock"):
            brief = generate_account_brief(firm, contact, signals, result)
            if brief:
                brief_json = json.dumps(brief)
        cur = conn.execute(
            """INSERT INTO scores
               (contact_id, firm_id, score, icp_fit, ai_readiness, reachability,
                signal_freshness, label, action, reasoning, missing, sections_used,
                account_brief)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                contact["id"], firm["id"],
                result["score"], result["icp_fit"], result["ai_readiness"],
                result["reachability"], result["signal_freshness"],
                result["label"], result["action"], result["reasoning"],
                result["missing"], json.dumps(sections_used), brief_json,
            ),
        )
        score_id = cur.lastrowid

        conn.execute(
            """INSERT INTO scoring_decisions
               (contact_id, firm_id, score, sections_used, signal_count)
               VALUES (?, ?, ?, ?, ?)""",
            (
                contact["id"], firm["id"], result["score"],
                json.dumps(sections_used), len(signals),
            ),
        )
        conn.commit()
        return score_id
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Public entrypoint
# ─────────────────────────────────────────────────────────────────────────────

def score_contact(firm: dict, contact: dict, signals: Optional[list[dict]] = None,
                  mock_mode: bool = False) -> dict:
    """Score a single contact. Returns a dict with all score fields plus ids."""
    signals = signals or []
    account = _build_account(firm, signals)

    prompt_text, sections_used = build_scoring_prompt(account)

    if mock_mode:
        result = _mock_score(firm, contact, signals)
        result["_mock"] = True
        logger.info(f"mock score: {firm.get('name')} / {contact.get('name')} → {result['score']}")
    else:
        user_msg = _build_user_message(firm, contact, signals)
        raw = _call_claude(prompt_text, user_msg)
        result = _parse_claude_response(raw)

    score_id = _persist(firm, contact, signals, result, sections_used)

    return {
        **result,
        "score_id": score_id,
        "firm_id": firm["id"],
        "contact_id": contact["id"],
        "sections_used": sections_used,
        "scored_at": now_iso(),
        "model": "mock" if mock_mode else ANTHROPIC_MODEL,
    }
