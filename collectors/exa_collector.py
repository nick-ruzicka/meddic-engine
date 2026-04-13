"""collectors/exa_collector.py

Press-signal collector via Exa neural search. Two modes:

MODE 1 — Firm-specific: one Exa query per firm in the firms table,
hunting for recent AI/DD/automation coverage naming that firm.

MODE 2 — Industry buying signals: a fixed set of queries surfacing
evaluation/deployment announcements across PE, IB, hedge, credit.

Each hit normalizes into a signal row matching the `signals` table
schema used by the other collectors. Deduped by URL.

Env: EXA_API_KEY (required). Returns [] if unset so --collect doesn't fail.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

from collectors.twitter_collector import classify, infer_buying_stage

logger = logging.getLogger(__name__)

# Start date for recency filter. Configurable via env; defaults to last ~9 months.
START_DATE_DEFAULT = "2025-07-01"
NUM_RESULTS_PER_FIRM  = 3
NUM_RESULTS_PER_QUERY = 5

BLACKLIST_PATTERNS = (
    "cookie", "table of contents", "united states securities",
    "accept all", "privacy policy", "terms of service",
    "sign in to view", "log in to", "please sign in",
    "join to see", "agree & join", "agree &amp; join",
)


def is_garbage(text: str) -> bool:
    """True if the signal body is boilerplate/login-wall/cookie-banner noise."""
    if not text or len(text.strip()) < 30:
        return True
    lo = text.lower()
    return any(p in lo for p in BLACKLIST_PATTERNS)


INDUSTRY_QUERIES = [
    "private equity firm deploying AI due diligence 2025 2026",
    "investment bank artificial intelligence workflow automation",
    "AlphaSense alternative AI document analysis finance",
    "PE firm CTO AI transformation announcement",
    "hedge fund machine learning infrastructure deployment",
]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _client():
    key = os.getenv("EXA_API_KEY")
    if not key:
        logger.warning("exa_collector: EXA_API_KEY not set — returning 0 signals")
        return None
    try:
        from exa_py import Exa
    except ImportError:
        logger.warning("exa_collector: exa-py not installed — returning 0 signals")
        return None
    return Exa(api_key=key)


def _resolve_firm(text: str, firms: list[dict]) -> Optional[int]:
    """Return firm_id if any firm name appears in text, else None."""
    if not text:
        return None
    lo = text.lower()
    for f in firms:
        name = (f.get("name") or "").lower().strip()
        if name and name in lo:
            return f["id"]
    return None


def _extract_text(result) -> str:
    """Pull readable text from an Exa result: highlights > text > title."""
    hl = getattr(result, "highlights", None) or []
    if hl:
        return hl[0] if isinstance(hl[0], str) else str(hl[0])
    txt = getattr(result, "text", "") or ""
    if txt:
        return txt[:500]
    return getattr(result, "title", "") or ""


_LANDING_PAGE_PATTERNS = [
    r"/news/?$", r"/press/?$", r"/insights/?$",
    r"/blog/?$", r"/media/?$", r"/resources/?$",
    r"/updates/?$", r"/newsroom/?$", r"/articles/?$",
    r"/people/?$", r"/team/?$", r"/about/?$",
]
_LANDING_RX = re.compile("|".join(_LANDING_PAGE_PATTERNS), re.IGNORECASE)


def is_landing_page(url: str) -> bool:
    """A URL whose path stops at a section root (no deep article slug)."""
    if not url:
        return False
    return bool(_LANDING_RX.search(url))


def _to_signal(result, firms: list[dict]) -> Optional[dict]:
    url = getattr(result, "url", None)
    if not url:
        return None
    if is_landing_page(url):
        return None
    title = getattr(result, "title", "") or ""
    body  = _extract_text(result)
    hay   = f"{title}\n{body}"
    if is_garbage(hay):
        return None
    firm_id = _resolve_firm(hay, firms)
    published = getattr(result, "published_date", None)
    return {
        "firm_id":        firm_id,
        "contact_id":     None,
        "signal_type":    "press",
        "signal_subtype": classify(hay),
        "content":        (body or title)[:400],
        "source_url":     url,
        "author_handle":  None,
        "author_name":    getattr(result, "author", None) or None,
        "signal_date":    (published or "")[:10] if published else None,
        "freshness_days": None,
        "buying_stage":   infer_buying_stage(hay),
        "raw_data":       json.dumps({
            "title": title,
            "url":   url,
            "score": getattr(result, "score", None),
        }),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Modes
# ─────────────────────────────────────────────────────────────────────────────

def _search_firm(exa, firm_name: str, start_date: str):
    query = (
        f'"{firm_name}" artificial intelligence OR "machine learning" '
        f'OR "AI deployment" OR "due diligence automation" '
        f'OR "data infrastructure"'
    )
    try:
        return exa.search_and_contents(
            query,
            num_results=NUM_RESULTS_PER_FIRM,
            start_published_date=start_date,
            text=True,
            highlights={"num_sentences": 3},
        )
    except Exception as e:
        logger.warning(f"exa: firm search failed for {firm_name}: {e}")
        return None


def _search_industry(exa, query: str, start_date: str):
    try:
        return exa.search_and_contents(
            query,
            num_results=NUM_RESULTS_PER_QUERY,
            start_published_date=start_date,
            text=True,
            highlights={"num_sentences": 3},
        )
    except Exception as e:
        logger.warning(f"exa: industry search failed for '{query[:40]}…': {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Public entrypoint (matches collector interface used by main.py)
# ─────────────────────────────────────────────────────────────────────────────

def collect(config: dict) -> list[dict]:
    exa = _client()
    if exa is None:
        return []

    start_date = (config.get("exa") or {}).get("start_date") or START_DATE_DEFAULT

    # Load firm universe for firm resolution.
    try:
        from database import get_db
        conn = get_db()
        firms = [dict(r) for r in conn.execute(
            "SELECT id, name FROM firms"
        ).fetchall()]
        conn.close()
    except Exception as e:
        logger.warning(f"exa: could not load firms table: {e}")
        firms = []

    signals: list[dict] = []
    seen_urls: set[str] = set()

    def _consume(resp, label: str):
        if not resp:
            return 0
        results = getattr(resp, "results", None) or []
        added = 0
        for r in results:
            url = getattr(r, "url", None)
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            sig = _to_signal(r, firms)
            if sig:
                signals.append(sig)
                added += 1
        logger.info(f"exa: {label} → {added} new signals")
        return added

    # Mode 1 — firm-specific
    for f in firms:
        name = f.get("name")
        if not name:
            continue
        _consume(_search_firm(exa, name, start_date), f"firm '{name}'")

    # Mode 2 — industry buying signals
    for q in INDUSTRY_QUERIES:
        _consume(_search_industry(exa, q, start_date), f"industry '{q[:40]}…'")

    logger.info(f"exa_collector: {len(signals)} unique press signals "
                f"(across {len(firms)} firms + {len(INDUSTRY_QUERIES)} industry queries)")
    return signals
