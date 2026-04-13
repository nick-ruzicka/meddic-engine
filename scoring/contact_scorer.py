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

ANTHROPIC_MODEL = os.getenv("ANTHROPIC_SCORING_MODEL", "claude-sonnet-4-6")
BRIEF_MODEL     = os.getenv("ANTHROPIC_BRIEF_MODEL", "claude-haiku-4-5-20251001")
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


def _fetch_other_contacts(firm_id: int, exclude_contact_id: int) -> list[dict]:
    """Return other contacts at the same firm (excluding this one and placeholders)."""
    if not firm_id:
        return []
    try:
        conn = get_db()
        rows = conn.execute(
            """SELECT id, name, title, role_type
               FROM contacts
               WHERE firm_id = ? AND id != ?
                 AND COALESCE(is_placeholder, 0) = 0
                 AND COALESCE(do_not_contact, 0) = 0
               ORDER BY id ASC
               LIMIT 8""",
            (firm_id, exclude_contact_id or 0),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.debug(f"_fetch_other_contacts failed: {e}")
        return []


def _meddic_role(title: str) -> str:
    """Legacy title-regex MEDDIC role — fallback only when Claude hasn't
    classified the contact yet. Use _load_meddic_role() to read the stored
    Claude-assigned role for a contact."""
    t = (title or "").lower()
    if any(k in t for k in ("cto", "cio", "ceo", "coo", "cdo", "caio",
                            "chief", "president", "managing director",
                            " md", "md,", "md ", "partner")):
        return "EB"
    if any(k in t for k in ("head of ai", "head of data", "head of research",
                            "head of technology", "head of engineering",
                            "director", "vp", "vice president")):
        return "CH"
    return "UC"


def _load_meddic_role(contact_id: int) -> dict:
    """Return {role, confidence, reasoning} from contacts table, or empty dict."""
    if not contact_id:
        return {}
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT meddic_role, meddic_confidence, meddic_reasoning "
            "FROM contacts WHERE id=?", (contact_id,)
        ).fetchone()
        conn.close()
        if not row or not row["meddic_role"]:
            return {}
        return {
            "role": row["meddic_role"],
            "confidence": float(row["meddic_confidence"] or 0.0),
            "reasoning": row["meddic_reasoning"] or "",
        }
    except Exception as e:
        logger.debug(f"_load_meddic_role failed: {e}")
        return {}


def _personal_signals(contact_id: int, limit: int = 3) -> list[dict]:
    if not contact_id:
        return []
    try:
        conn = get_db()
        rows = conn.execute(
            """SELECT signal_type, signal_subtype, content, signal_date, source_url
                 FROM signals
                WHERE contact_id = ?
                ORDER BY COALESCE(signal_date, created_at) DESC
                LIMIT ?""",
            (contact_id, limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.debug(f"_personal_signals failed: {e}")
        return []


def _load_research(contact_id: int) -> dict:
    if not contact_id:
        return {}
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT research_json FROM contacts WHERE id=?", (contact_id,)
        ).fetchone()
        conn.close()
        if not row or not row["research_json"]:
            return {}
        return json.loads(row["research_json"])
    except Exception as e:
        logger.debug(f"_load_research failed: {e}")
        return {}


_MEDDIC_SYSTEM = (
    "You classify a contact's B2B sales role for  — an enterprise AI "
    "platform sold to PE firms, investment banks, hedge funds, and credit "
    "funds.\n\n"
    "Roles:\n"
    "EB = Economic Buyer: final authority on vendor selection AND budget, "
    "AND their remit covers technology / AI / data decisions. Not every "
    "senior person is an EB — a Managing Director of Investor Relations is "
    "NOT an EB for .\n"
    "CH = Champion: internal advocate who will use the tool and build the "
    "business case. Head of AI, Director of Technology, VP of Data, AI Lead, "
    "Head of Research Technology.\n"
    "UC = User / Contact: practitioner without purchase authority — analyst, "
    "associate, VP without AI mandate.\n"
    "UNKNOWN: insufficient information to classify confidently.\n\n"
    "Return ONLY a valid JSON object, no markdown, in this exact shape:\n"
    "{\"role\": \"EB|CH|UC|UNKNOWN\", \"confidence\": <0.0-1.0>, "
    "\"reasoning\": \"<one sentence>\"}"
)


def _classify_meddic_role(firm: dict, contact: dict) -> dict | None:
    """Single Haiku call. Returns {role, confidence, reasoning} or None on
    failure. Safe to call repeatedly — persistence layer gates by
    contacts.meddic_role IS NULL."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    cid = contact.get("id")
    personal = _personal_signals(cid, limit=3)
    research = _load_research(cid)
    activity_summary = research.get("activity_summary") or ""
    other_contacts = _fetch_other_contacts(firm.get("id", 0), cid or 0)

    personal_block = "\n".join(
        f"- [{s.get('signal_type')}] {(s.get('content') or '')[:180]}"
        for s in personal
    ) if personal else "(none attributed)"
    others_block = "\n".join(
        f"- {o.get('name')} — {o.get('title') or '—'}" for o in other_contacts
    ) if other_contacts else "(none on file)"

    user = (
        f"Contact: {contact.get('name')}\n"
        f"Title: {contact.get('title') or '—'}\n"
        f"Firm: {firm.get('name')} ({firm.get('firm_type') or '?'})\n\n"
        f"Signals attributed to this person:\n{personal_block}\n\n"
        f"Public activity summary: {activity_summary or '(no research)'}\n\n"
        f"Other contacts at this firm (for context):\n{others_block}\n\n"
        "Classify this contact. Return ONLY the JSON object specified in the "
        "system prompt."
    )
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = _call_claude_with_retry(
            client,
            model=BRIEF_MODEL, max_tokens=200, temperature=0,
            system=_MEDDIC_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        raw = "".join(getattr(b, "text", "") for b in resp.content).strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE)
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        data = json.loads(m.group(0))
        role = str(data.get("role", "")).upper().strip()
        if role not in ("EB", "CH", "UC", "UNKNOWN"):
            return None
        try:
            conf = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        conf = max(0.0, min(1.0, conf))
        reasoning = str(data.get("reasoning") or "").strip()
        return {"role": role, "confidence": conf, "reasoning": reasoning}
    except Exception as e:
        logger.warning(
            f"MEDDIC classification failed for {contact.get('name')}: {e}"
        )
        return None


def _persist_meddic_role(contact_id: int, result: dict) -> None:
    if not contact_id or not result:
        return
    try:
        conn = get_db()
        conn.execute(
            """UPDATE contacts
                  SET meddic_role = ?,
                      meddic_confidence = ?,
                      meddic_reasoning = ?,
                      meddic_classified_at = datetime('now'),
                      updated_at = datetime('now')
                WHERE id = ?""",
            (result["role"], result["confidence"],
             result.get("reasoning") or "", contact_id),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f"_persist_meddic_role failed: {e}")


def _classify_role(title: str) -> str:
    """Cheap heuristic — is this contact an economic buyer or technical champion?"""
    t = (title or "").lower()
    if any(k in t for k in ("cto", "chief technology", "head of ai", "chief ai",
                            "head of data", "chief data", "head of engineering",
                            "head of technology", "head of research technology")):
        return "technical_champion"
    if any(k in t for k in ("cio", "chief investment", "managing partner",
                            "president", "chief executive", "ceo", "coo",
                            "chief operating", "partner", "managing director")):
        return "economic_buyer"
    return "other"


def _build_user_message(firm: dict, contact: dict, signals: list[dict]) -> str:
    others = _fetch_other_contacts(firm.get("id", 0), contact.get("id", 0))
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
    if others:
        parts += ["", f"## Other contacts at this firm ({len(others)})"]
        for o in others:
            parts.append(f"- {o.get('name')}, {o.get('title') or '—'}")
        parts.append(
            "\nScore and reason for THIS contact specifically — differentiate based on "
            "title, seniority, and which workflow this person would own. Do NOT repeat "
            "identical reasoning across Co-CIOs or peers at the same firm."
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


def _retryable_anthropic_errors():
    """Tuple of anthropic exception classes worth retrying. Imported lazily so
    the SDK is only required when scoring is actually run."""
    import anthropic
    return (
        anthropic.APIStatusError,
        anthropic.APITimeoutError,
        anthropic.APIConnectionError,
    )


def _call_claude_with_retry(client, **kwargs):
    """Wrap client.messages.create with exponential backoff on transient errors.
    3 attempts, 2s→30s backoff. Permanent errors (auth, bad request) raise immediately."""
    from tenacity import (
        retry, stop_after_attempt, wait_exponential,
        retry_if_exception_type, before_sleep_log,
    )

    @retry(
        retry=retry_if_exception_type(_retryable_anthropic_errors()),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _call():
        return client.messages.create(**kwargs)
    return _call()


def _call_claude(system_prompt: str, user_message: str) -> str:
    import anthropic
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    client = anthropic.Anthropic(api_key=api_key)
    resp = _call_claude_with_retry(
        client,
        model=ANTHROPIC_MODEL,
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
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

    others = _fetch_other_contacts(firm.get("id", 0), contact.get("id", 0))
    others_block = (
        "\n".join(f"- {o.get('name')}, {o.get('title') or '—'}" for o in others)
        if others else "(none — this is the only contact on file)"
    )
    this_role = _classify_role(contact.get("title", ""))
    other_roles = [_classify_role(o.get("title", "")) for o in others]
    # Prefer Claude-assigned MEDDIC role when available; otherwise fall back
    # to the title regex. Confidence below 0.5 gets demoted to UNKNOWN for
    # brief labeling (critique fix #6).
    stored_meddic = _load_meddic_role(contact.get("id", 0))
    if stored_meddic.get("role") and stored_meddic["role"] != "UNKNOWN" \
       and stored_meddic.get("confidence", 0) >= 0.5:
        meddic_role = stored_meddic["role"]
    elif stored_meddic.get("role") == "UNKNOWN":
        meddic_role = "UC"  # brief still needs a label
    else:
        meddic_role = _meddic_role(contact.get("title", ""))
    meddic_role_label = {"EB": "ECONOMIC BUYER",
                         "CH": "CHAMPION",
                         "UC": "USER / CHAMPION"}.get(meddic_role, "CHAMPION")

    # Pull per-contact research (posts/speaking/press) if available.
    research = _load_research(contact.get("id", 0))
    research_block = ""
    if research and (research.get("activity_summary")
                     or research.get("recent_posts")
                     or research.get("speaking")
                     or research.get("press")):
        summary = research.get("activity_summary") or ""
        items = (research.get("recent_posts", [])
                 + research.get("speaking", [])
                 + research.get("press", []))
        top_items = sorted(
            items, key=lambda x: (x.get("date") or ""), reverse=True
        )[:2]
        item_lines = [
            f"  · [{it.get('signal_type')}] {it.get('date') or '?'}: "
            f"{(it.get('title') or '')[:120]} — "
            f"{(it.get('snippet') or '')[:160]}"
            for it in top_items
        ]
        research_block = (
            "CONTACT RESEARCH (use for specificity — reference actual "
            "public activity where relevant, do not invent):\n"
            f"Activity summary: {summary or '(no summary)'}\n"
            "Top items:\n" + ("\n".join(item_lines) if item_lines else "  (none)")
            + "\n"
        )

    # Firm-type + competitor-aware decision criteria seed
    ft = (firm.get("firm_type") or "").lower()
    comp = (firm.get("competitor") or "").lower()
    if "alphasense" in comp:
        dc_seed = "RAG accuracy on private docs, not just public market search."
    elif "rogo" in comp and ft in ("ib", "investment_bank", "advisory"):
        dc_seed = "Document synthesis depth, complementary to existing stack."
    elif ft in ("pe", "private_equity", "credit", "credit_fund"):
        dc_seed = "Speed to value, data sovereignty, ROI proof."
    elif ft in ("ib", "investment_bank", "advisory"):
        dc_seed = "Document synthesis depth at deal velocity, compliance-reviewable output."
    elif ft in ("hf", "hedge_fund"):
        dc_seed = "Analyst productivity on qualitative research, citation traceability."
    else:
        dc_seed = "Data sovereignty, measurable ROI, fit to existing workflow."

    # Decision process — deterministic from buying_stage
    bs = (firm.get("buying_stage") or "").lower()
    if bs == "deploying":
        decision_process = "Active vendor selection - decision likely within 90 days."
    elif bs == "evaluating":
        decision_process = "3-6 month evaluation window - build relationship with champion now."
    elif bs == "exploring":
        decision_process = "12-18 month horizon - nurture sequence, not hot outreach."
    else:
        decision_process = "Timing unconfirmed - let signals drive cadence."
    # Find a complementary pair if any
    pair_role_hint = ""
    if this_role == "economic_buyer" and "technical_champion" in other_roles:
        idx = other_roles.index("technical_champion")
        pair_role_hint = (
            f"This contact is an ECONOMIC BUYER. A technical champion exists at this firm "
            f"({others[idx].get('name')}, {others[idx].get('title')}) — recommend pairing them."
        )
    elif this_role == "technical_champion" and "economic_buyer" in other_roles:
        idx = other_roles.index("economic_buyer")
        pair_role_hint = (
            f"This contact is a TECHNICAL CHAMPION. An economic buyer exists at this firm "
            f"({others[idx].get('name')}, {others[idx].get('title')}) — recommend pairing them."
        )
    elif this_role == "economic_buyer":
        pair_role_hint = (
            "This contact is an ECONOMIC BUYER. No technical champion on file — recommend "
            "finding a CTO / Head of AI / Head of Data to dual-thread."
        )
    elif this_role == "technical_champion":
        pair_role_hint = (
            "This contact is a TECHNICAL CHAMPION. Recommend pairing with an economic buyer "
            "(CIO, Managing Partner, President) to advance past compliance review."
        )

    user = f"""FIRM: {firm.get('name')} ({firm.get('firm_type')})
AUM: {aum_line}
CONTACT: {contact.get('name')}, {contact.get('title')}
CONTACT ROLE CLASSIFICATION: {this_role}
MEDDIC ROLE: {meddic_role_label}
BUYING STAGE: {firm.get('buying_stage') or 'unknown'}
COMPETITOR: {firm.get('competitor') if firm.get('competitor') and firm.get('competitor') != 'none' else 'not identified'}
SIGNALS: {sig_line}
SCORE: {score_result.get('score')} - {score_result.get('label')}
REASONING: {(score_result.get('reasoning') or '')[:400]}

OTHER CONTACTS AT THIS FIRM:
{others_block}

MULTI-THREAD HINT:
{pair_role_hint or '(single-threaded)'}

{research_block}
DECISION CRITERIA SEED (use verbatim as the first sentence of decision_criteria): "{dc_seed}"

You are producing a MEDDIC-framed account brief. Return ONLY valid JSON, no markdown:
{{
  "identified_pain": "1-2 sentences on the workflow pain this firm is feeling right now - reference the buying stage, signals, peer activity. This is the M-E-D-D-I-C 'I' - what hurts today.",
  "decision_criteria": "Start with the seed line above verbatim. Then add ONE sentence of firm-specific color (competitor context, AUM tier, or signal-driven nuance).",
  "metrics": "Pipe-separated bullet metrics ONLY. Format: 'Oak Hill: 6x ROI | $25T AUM using  | 1000+ use cases in production'. Pick 2-3 most relevant to this firm type. No prose.",
  "champion_eb": "ONE sentence identifying this contact as {meddic_role_label} and why they matter. If CONTACT RESEARCH is provided above, reference a SPECIFIC public activity (e.g. 'spoke at [venue] in [month] about [topic]' or 'posted on [date] about [topic]') — do NOT just repeat title+firm. If no research available, reference workflow ownership or political capital.",
  "objection": "The most likely objection from this specific contact/firm type, plus one sentence on how to handle it. Lead with data sovereignty if compliance-flagged.",
  "thread": "ONE sentence on multi-thread strategy: 'Pair [this role] with [complementary role] at [firm] - [reason].' If solo, say 'Solo-thread viable - find [role] to strengthen.'"
}}"""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = _call_claude_with_retry(
            client,
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
        for k in ("identified_pain", "decision_criteria", "metrics",
                  "champion_eb", "objection"):
            if not data.get(k):
                return None
            data[k] = str(data[k]).replace("—", "-").strip()
        if data.get("thread"):
            data["thread"] = str(data["thread"]).replace("—", "-").strip()
        data["decision_process"] = decision_process
        data["meddic_role"] = meddic_role
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

    # Claude MEDDIC classification — idempotent: only fire when not yet set.
    if not mock_mode:
        existing = _load_meddic_role(contact.get("id"))
        if not existing.get("role"):
            meddic = _classify_meddic_role(firm, contact)
            if meddic:
                _persist_meddic_role(contact.get("id"), meddic)

    return {
        **result,
        "score_id": score_id,
        "firm_id": firm["id"],
        "contact_id": contact["id"],
        "sections_used": sections_used,
        "scored_at": now_iso(),
        "model": "mock" if mock_mode else ANTHROPIC_MODEL,
    }
