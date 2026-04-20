"""enrichment/contact_researcher.py

Pull contact-specific research (personal posts, speaking, press) via Exa
and summarize activity via Claude Haiku. Persists to `contacts.research_json`
and `contacts.last_activity_at`. Also extracts Twitter handle when we find
a twitter.com URL tied to this contact.

Public API:
    research_contact(contact: dict, firm: dict, conn) -> dict | None
    run_research_pass(conn, limit=50) -> dict   # counters + breakdown

Env: EXA_API_KEY, ANTHROPIC_API_KEY
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

HAIKU_MODEL = os.getenv("ANTHROPIC_BRIEF_MODEL", "claude-haiku-4-5-20251001")

# Twitter/X handle-URL pattern. Path segments like "search", "home", "i",
# "hashtag" are reserved and not real handles.
_HANDLE_RX = re.compile(r"(?:twitter|x)\.com/([^/?#\s]+)", re.IGNORECASE)
_HANDLE_RESERVED = {"search", "hashtag", "i", "home", "explore",
                    "notifications", "messages", "compose", "settings",
                    "login", "signup", "share", "intent"}


# ─────────────────────────────────────────────────────────────────────────────
# Low-level Exa client
# ─────────────────────────────────────────────────────────────────────────────

def _exa_client():
    key = os.getenv("EXA_API_KEY")
    if not key:
        return None
    try:
        from exa_py import Exa
        return Exa(api_key=key)
    except ImportError:
        logger.warning("exa-py not installed — contact researcher disabled")
        return None


def _exa_search(exa, query: str, num_results: int, start_date: str,
                with_contents: bool) -> list:
    """Call Exa once. `with_contents` chooses search vs search_and_contents."""
    try:
        if with_contents:
            res = exa.search_and_contents(
                query, num_results=num_results,
                start_published_date=start_date,
                text=True, highlights=True,
            )
        else:
            res = exa.search(
                query, num_results=num_results,
                start_published_date=start_date,
            )
        return list(getattr(res, "results", None) or [])
    except Exception as e:
        logger.warning(f"Exa call failed ({query[:60]}…): {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Result filtering — keep contact-level signal only, drop firm-level noise
# ─────────────────────────────────────────────────────────────────────────────

def _name_in_title_or_highlight(name: str, result) -> bool:
    """Keep a result only if the contact name appears in the title OR in the
    first 200 chars of the first highlight. Body-only matches (firm press
    releases that happen to list the contact) are rejected."""
    if not name:
        return False
    n = name.lower()
    title = (getattr(result, "title", "") or "").lower()
    if n in title:
        return True
    # Highlights may be a list[str]; check first 200 chars of first one.
    highlights = getattr(result, "highlights", None) or []
    if highlights:
        first = (highlights[0] or "").lower()
        if n in first[:200]:
            return True
    return False


def _result_date(result) -> str:
    """Best-effort published date string."""
    for attr in ("published_date", "publishedDate", "date"):
        v = getattr(result, attr, None)
        if v:
            return str(v)[:10]
    return ""


def _result_snippet(result) -> str:
    highlights = getattr(result, "highlights", None) or []
    if highlights:
        return (highlights[0] or "")[:240]
    text = getattr(result, "text", "") or ""
    return text[:240]


def _normalize(result, signal_type: str) -> dict:
    return {
        "title": (getattr(result, "title", "") or "").strip(),
        "url": getattr(result, "url", "") or "",
        "date": _result_date(result),
        "snippet": _result_snippet(result),
        "signal_type": signal_type,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Twitter handle extraction
# ─────────────────────────────────────────────────────────────────────────────

def _maybe_extract_handle(url: str) -> Optional[str]:
    if not url:
        return None
    m = _HANDLE_RX.search(url)
    if not m:
        return None
    handle = m.group(1).lstrip("@").strip()
    # Strip trailing slashes / status suffixes like "jsmith/status/123"
    handle = handle.split("/")[0]
    if not handle or handle.lower() in _HANDLE_RESERVED:
        return None
    # Plausible handle sanity check (twitter handles are 1–15 chars, alnum+_)
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", handle):
        return None
    return handle


# ─────────────────────────────────────────────────────────────────────────────
# Activity summary via Haiku
# ─────────────────────────────────────────────────────────────────────────────

def _summarize_activity(contact_name: str, items: list[dict]) -> str:
    """One-sentence Haiku summary. Returns '' on any failure."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key or not items:
        return ""
    # Keep the prompt cheap: top 3 items, title+date+snippet.
    top = items[:3]
    lines = [
        f"- [{it.get('signal_type')}] {it.get('date') or '?'}: "
        f"{it.get('title')} — {it.get('snippet', '')[:160]}"
        for it in top
    ]
    user = (
        f"Contact: {contact_name}\n\nRecent public results:\n"
        + "\n".join(lines)
        + "\n\nIn one sentence (max 30 words), summarize this person's "
          "recent public activity. Be specific — name the topic, the venue, "
          "and date if relevant. Do not invent details."
    )
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=120,
            temperature=0.2,
            system=("You summarize a person's recent public activity in one "
                    "factual sentence. Never invent details."),
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(getattr(b, "text", "") for b in resp.content).strip()
        # Collapse to single line, strip quotes.
        return re.sub(r"\s+", " ", text).strip().strip('"').strip("'")
    except Exception as e:
        logger.debug(f"activity summary failed for {contact_name}: {e}")
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Per-contact research
# ─────────────────────────────────────────────────────────────────────────────

def research_contact(contact: dict, firm: dict, conn, *,
                     counters: Optional[dict] = None) -> Optional[dict]:
    """Pull recent posts / speaking / press for a named contact at a firm.
    Persists to contacts.research_json + last_activity_at. Returns the dict
    or None if no results kept."""
    exa = _exa_client()
    if exa is None:
        return None

    name = (contact.get("name") or "").strip()
    firm_name = (firm.get("name") or "").strip()
    if not name or len(name.split()) < 2:
        return None

    counters = counters if counters is not None else {"exa": 0, "haiku": 0}

    # 1) Personal posts — recent AI/tech signal content mentioning the name + firm.
    q1 = (f'"{name}" "{firm_name}" AI OR "artificial intelligence" '
          f'OR "machine learning" OR "due diligence" OR technology')
    r1 = _exa_search(exa, q1, num_results=5,
                     start_date="2025-01-01", with_contents=True)
    counters["exa"] += 1

    # 2) Speaking — scoped to finance/industry venues to cut consumer noise.
    q2 = (f'"{name}" speaker OR keynote OR panel OR conference '
          f'site:reuters.com OR site:ft.com OR site:bloomberg.com '
          f'OR site:peievents.com OR site:privateequityinternational.com')
    r2 = _exa_search(exa, q2, num_results=3,
                     start_date="2024-01-01", with_contents=True)
    counters["exa"] += 1

    # 3) Press / interviews / bylines.
    q3 = (f'"{name}" "{firm_name}" interview OR "according to" '
          f'OR "said" OR bylined OR op-ed')
    r3 = _exa_search(exa, q3, num_results=3,
                     start_date="2024-01-01", with_contents=True)
    counters["exa"] += 1

    # Filter + normalize per bucket.
    def _bucket(raw, sig_type):
        out = []
        for r in raw:
            if not _name_in_title_or_highlight(name, r):
                continue
            out.append(_normalize(r, sig_type))
        return out

    recent_posts = _bucket(r1, "post")
    speaking     = _bucket(r2, "speaking")
    press        = _bucket(r3, "press")

    total = len(recent_posts) + len(speaking) + len(press)
    if total == 0:
        # Record the attempt as an empty payload so we don't re-query on the
        # next pass. Use a marker so we can distinguish from a never-queried
        # contact (research_json IS NULL).
        empty = {"recent_posts": [], "speaking": [], "press": [],
                 "last_activity_date": "", "activity_summary": "",
                 "researched_at": datetime.utcnow().isoformat(timespec="seconds")}
        conn.execute(
            "UPDATE contacts SET research_json=?, updated_at=datetime('now') WHERE id=?",
            (json.dumps(empty), contact["id"]),
        )
        return empty

    # Twitter handle extraction from any kept result URL. First valid handle wins.
    if not contact.get("twitter_handle"):
        for item in recent_posts + speaking + press:
            h = _maybe_extract_handle(item.get("url", ""))
            if h:
                conn.execute(
                    "UPDATE contacts SET twitter_handle=? WHERE id=? AND (twitter_handle IS NULL OR twitter_handle='')",
                    (h, contact["id"]),
                )
                break

    # Last activity = max date across all kept items.
    all_items = recent_posts + speaking + press
    dates = [it["date"] for it in all_items if it.get("date")]
    last_date = max(dates) if dates else ""

    # Activity summary — only if >=2 results total (saves Haiku calls on
    # thin-evidence contacts).
    summary = ""
    if total >= 2:
        summary = _summarize_activity(name, all_items)
        if summary:
            counters["haiku"] += 1

    payload = {
        "recent_posts": recent_posts,
        "speaking": speaking,
        "press": press,
        "last_activity_date": last_date,
        "activity_summary": summary,
        "researched_at": datetime.utcnow().isoformat(timespec="seconds"),
    }
    conn.execute(
        """UPDATE contacts
              SET research_json = ?,
                  last_activity_at = COALESCE(?, last_activity_at),
                  updated_at = datetime('now')
            WHERE id = ?""",
        (json.dumps(payload), last_date or None, contact["id"]),
    )
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# Batch pass — CLI entrypoint via main.py --research
# ─────────────────────────────────────────────────────────────────────────────

def run_research_pass(conn, limit: int = 50, sleep_s: float = 0.5) -> dict:
    """Enrich up to `limit` tier-1 contacts that lack research_json, prioritizing
    already-scored ones so scoring pipelines pick up new intel on rescore."""
    rows = conn.execute(
        """
        SELECT c.id, c.name, c.title, c.twitter_handle, c.firm_id,
               f.name AS firm_name, f.firm_type, f.tier,
               (SELECT MAX(s.score) FROM scores s WHERE s.contact_id = c.id) AS score
          FROM contacts c
          JOIN firms f ON f.id = c.firm_id
         WHERE f.tier = 1
           AND c.email IS NOT NULL AND c.email != ''
           AND c.research_json IS NULL
           AND COALESCE(c.is_placeholder, 0) = 0
         ORDER BY
           CASE WHEN (SELECT MAX(s2.score) FROM scores s2
                       WHERE s2.contact_id = c.id) IS NOT NULL THEN 0 ELSE 1 END,
           (SELECT MAX(s3.score) FROM scores s3
              WHERE s3.contact_id = c.id) DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()

    counters = {"exa": 0, "haiku": 0}
    researched = 0
    empty = 0
    classified = 0
    # Import locally so this module stays usable without scoring deps.
    try:
        from scoring.contact_scorer import (
            _classify_meddic_role, _persist_meddic_role,
        )
    except Exception:
        _classify_meddic_role = _persist_meddic_role = None

    for r in rows:
        contact = dict(r)
        firm = {"id": r["firm_id"], "name": r["firm_name"],
                "firm_type": r["firm_type"]}
        try:
            payload = research_contact(contact, firm, conn, counters=counters)
            if payload is None:
                continue
            researched += 1
            total = (len(payload.get("recent_posts", []))
                     + len(payload.get("speaking", []))
                     + len(payload.get("press", [])))
            if total == 0:
                empty += 1
            conn.commit()

            # Classify MEDDIC role if not already done (idempotent).
            if _classify_meddic_role:
                existing = conn.execute(
                    "SELECT meddic_role FROM contacts WHERE id=?",
                    (contact["id"],),
                ).fetchone()
                if not (existing and existing["meddic_role"]):
                    res = _classify_meddic_role(firm, contact)
                    if res:
                        _persist_meddic_role(contact["id"], res)
                        counters["haiku"] += 1
                        classified += 1
        except Exception as e:
            logger.warning(f"research failed for {contact.get('name')}: {e}")
        time.sleep(sleep_s)

    return {
        "researched": researched,
        "empty": empty,
        "classified": classified,
        "exa_calls": counters["exa"],
        "haiku_calls": counters["haiku"],
        "selected": len(rows),
    }
