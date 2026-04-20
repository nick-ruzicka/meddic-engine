"""collectors/twitter_collector.py

Collects Twitter/X signals via TwitterAPI.io (third-party proxy, not the
official Twitter API). Two modes:

    1. Account monitoring — recent tweets from handles in config.twitter.monitored_accounts
    2. Keyword search    — queries built from config.twitter.keywords × firm_context_filters

Classification is keyword-based and intentionally cheap — downstream scoring
via Claude does the heavy lifting. This layer just tags and normalizes.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Iterable

import requests
from dateutil import parser as dateutil_parser
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

BASE_URL = "https://api.twitterapi.io"
TIMEOUT = 15
RECENT_DAYS = 7
ACCOUNT_TWEET_COUNT = 20
SEARCH_MAX_RESULTS = 10
TERMS_PER_CATEGORY = 3


# ─────────────────────────────────────────────────────────────────────────────
# Classification — pure keyword matching
# ─────────────────────────────────────────────────────────────────────────────

_COMPETITORS = ("alphasense", "bloomberg", "rogo", "stack ai")
_NEGATIVE = ("expensive", "doesn't", "doesnt", "limited", "limitation",
             "slow", "clunky", "frustrat", "overpriced")


def classify(text: str) -> str:
    """Return one of: competitor_frustration | evaluation | transformation | pain."""
    t = (text or "").lower()

    # competitor_frustration — negative sentiment near a competitor mention
    if any(c in t for c in _COMPETITORS) and any(n in t for n in _NEGATIVE):
        return "competitor_frustration"
    if "piloting" in t or "evaluating" in t or "testing " in t:
        return "evaluation"
    if "ai-first" in t or "deploying" in t or "transformation" in t:
        return "transformation"
    return "pain"


def infer_buying_stage(text: str) -> str:
    t = (text or "").lower()
    if any(k in t for k in ("production", "deployed", "rolled out", "firm-wide", "firmwide")):
        return "deploying"
    if any(k in t for k in ("piloting", "evaluating", "testing ", "exploring")):
        return "evaluating"
    return "exploring"


# ─────────────────────────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────────────────────────

def _api_key() -> str | None:
    return os.getenv("TWITTER_API_KEY")


def _get(path: str, params: dict) -> dict | None:
    try:
        r = requests.get(
            BASE_URL + path,
            params=params,
            headers={"X-API-Key": _api_key() or ""},
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            logger.warning(f"twitter {path} → {r.status_code} {r.text[:200]}")
            return None
        return r.json()
    except requests.RequestException as e:
        logger.warning(f"twitter {path} request failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Tweet normalization
# ─────────────────────────────────────────────────────────────────────────────

def _as_iso(raw_date: str | None) -> str:
    if not raw_date:
        return ""
    try:
        dt = dateutil_parser.parse(raw_date)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat(timespec="seconds")
    except Exception:
        return raw_date


def _is_recent(iso_date: str, days: int = RECENT_DAYS) -> bool:
    if not iso_date:
        return True  # can't tell — keep and let scorer decide
    try:
        dt = dateutil_parser.parse(iso_date)
        delta = datetime.now(timezone.utc) - dt
        return delta.days <= days
    except Exception:
        return True


def _load_firms() -> list[dict]:
    """Load firms (id, name) from DB for content-based resolution."""
    try:
        from database import get_db
        conn = get_db()
        rows = [dict(r) for r in conn.execute("SELECT id, name FROM firms").fetchall()]
        conn.close()
        return rows
    except Exception as e:
        logger.warning(f"twitter: could not load firms table: {e}")
        return []


def _build_handle_index(firm_handles_cfg: dict, firms: list[dict]) -> dict[str, int]:
    """Map lowercase handle → firm_id using config 'handle: firm_name' pairs."""
    name_to_id = {(f.get("name") or "").lower(): f["id"] for f in firms}
    index: dict[str, int] = {}
    for handle, firm_name in (firm_handles_cfg or {}).items():
        fid = name_to_id.get((firm_name or "").lower())
        if fid:
            index[handle.lower().lstrip("@")] = fid
    return index


def _resolve_firm_from_content(text: str, firms: list[dict]) -> int | None:
    """Same approach as exa_collector._resolve_firm — substring match."""
    if not text:
        return None
    lo = text.lower()
    for f in firms:
        name = (f.get("name") or "").lower().strip()
        if name and len(name) >= 4 and name in lo:
            return f["id"]
    return None


def _resolve_firm(handle: str, text: str,
                  handle_index: dict[str, int],
                  firms: list[dict]) -> int | None:
    """Handle lookup first, then fuzzy content match."""
    if handle:
        fid = handle_index.get(handle.lower().lstrip("@"))
        if fid:
            return fid
    return _resolve_firm_from_content(text, firms)


def _normalize(tweet: dict) -> dict | None:
    """Turn a TwitterAPI.io tweet into a signal dict. Returns None if unusable."""
    tid = str(tweet.get("id") or tweet.get("tweet_id") or "").strip()
    text = tweet.get("text") or tweet.get("full_text") or ""
    if not tid or not text:
        return None

    author = tweet.get("author") or tweet.get("user") or {}
    handle = (author.get("userName") or author.get("screen_name")
              or tweet.get("username") or "").lstrip("@")
    display = author.get("name") or tweet.get("author_name") or handle

    created = tweet.get("createdAt") or tweet.get("created_at") or tweet.get("date")
    iso = _as_iso(created)

    return {
        "_tid":          tid,
        "signal_type":    "twitter",
        "signal_subtype": classify(text),
        "content":        text[:500],
        "source_url":     f"https://twitter.com/{handle}/status/{tid}" if handle else f"https://twitter.com/i/web/status/{tid}",
        "author_handle":  handle,
        "author_name":    display,
        "signal_date":    iso,
        "buying_stage":   infer_buying_stage(text),
        "firm_id":        None,
        "contact_id":     None,
        "raw_data":       json.dumps(tweet, default=str),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Collection modes
# ─────────────────────────────────────────────────────────────────────────────

def _iter_tweets(payload: dict | None) -> Iterable[dict]:
    """TwitterAPI.io returns tweets under various keys. Handle all shapes:
       - advanced_search:  {"tweets": [...]}
       - user/last_tweets: {"status":"success","data":{"tweets":[...]}}
       - legacy:           {"data": [...]} or {"results": [...]}
    """
    if not payload:
        return []
    # Flat list at top level
    for key in ("tweets", "results", "statuses"):
        v = payload.get(key)
        if isinstance(v, list):
            return v
    # Nested under `data`
    data = payload.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("tweets", "results"):
            v = data.get(key)
            if isinstance(v, list):
                return v
    if isinstance(payload, list):
        return payload
    return []


def _collect_accounts(handles: list[str],
                      handle_index: dict[str, int],
                      firms: list[dict]) -> list[dict]:
    out = []
    for handle in handles:
        handle = handle.lstrip("@")
        payload = _get("/twitter/user/last_tweets",
                       {"userName": handle, "count": ACCOUNT_TWEET_COUNT})
        for tweet in _iter_tweets(payload):
            sig = _normalize(tweet)
            if not sig:
                continue
            if not _is_recent(sig["signal_date"]):
                continue
            if not sig["author_handle"]:
                sig["author_handle"] = handle
                sig["source_url"] = f"https://twitter.com/{handle}/status/{sig['_tid']}"
            sig["firm_id"] = _resolve_firm(sig["author_handle"], sig["content"],
                                           handle_index, firms)
            sig["buying_stage"] = infer_buying_stage(sig["content"])
            out.append(sig)
        logger.info(f"twitter:accounts {handle} → {sum(1 for _ in _iter_tweets(payload))} raw")
    return out


def _build_queries(keywords: dict, context_filters: list[str]) -> list[tuple[str, str]]:
    """Return a list of (category, query) pairs for search."""
    queries: list[tuple[str, str]] = []
    # Pick one context filter per query, rotating, to keep query count reasonable
    ctx_list = context_filters or [""]
    for category, terms in (keywords or {}).items():
        if not terms:
            continue
        picked = terms[:TERMS_PER_CATEGORY]
        for i, term in enumerate(picked):
            ctx = ctx_list[i % len(ctx_list)]
            q = f'"{term}" "{ctx}"' if ctx else f'"{term}"'
            queries.append((category, q))
    return queries


def _collect_search(keywords: dict, context_filters: list[str],
                    handle_index: dict[str, int],
                    firms: list[dict]) -> list[dict]:
    out = []
    for category, query in _build_queries(keywords, context_filters):
        # /twitter/search/recent was deprecated → advanced_search returns same shape
        # under `tweets` key. queryType=Latest orders by recency.
        payload = _get("/twitter/tweet/advanced_search",
                       {"query": query, "queryType": "Latest"})
        n_raw = 0
        for tweet in _iter_tweets(payload):
            n_raw += 1
            sig = _normalize(tweet)
            if not sig:
                continue
            # Trust explicit category hint over keyword classification if text is ambiguous
            if sig["signal_subtype"] == "pain" and category != "pain":
                sig["signal_subtype"] = category
            sig["firm_id"] = _resolve_firm(sig["author_handle"], sig["content"],
                                           handle_index, firms)
            sig["buying_stage"] = infer_buying_stage(sig["content"])
            out.append(sig)
        logger.info(f"twitter:search [{category}] '{query}' → {n_raw} raw")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Public entrypoint
# ─────────────────────────────────────────────────────────────────────────────

def collect(config: dict) -> list[dict]:
    if not _api_key():
        logger.warning("TWITTER_API_KEY not set — twitter_collector returning []")
        return []

    twitter_cfg = (config or {}).get("twitter") or {}
    handles = twitter_cfg.get("monitored_accounts") or []
    keywords = twitter_cfg.get("keywords") or {}
    ctx = twitter_cfg.get("firm_context_filters") or []
    firm_handles_cfg = twitter_cfg.get("firm_handles") or {}

    firms = _load_firms()
    handle_index = _build_handle_index(firm_handles_cfg, firms)
    logger.info(f"twitter: firm resolution ready — "
                f"{len(handle_index)} handles mapped, {len(firms)} firms for fuzzy match")

    try:
        accounts = _collect_accounts(handles, handle_index, firms)
    except Exception as e:
        logger.exception(f"twitter account mode failed: {e}")
        accounts = []

    try:
        search = _collect_search(keywords, ctx, handle_index, firms)
    except Exception as e:
        logger.exception(f"twitter search mode failed: {e}")
        search = []

    # Dedup by tweet id — keep first occurrence (account mode takes priority)
    seen: set[str] = set()
    deduped: list[dict] = []
    for sig in accounts + search:
        tid = sig.pop("_tid", None)
        if not tid or tid in seen:
            continue
        seen.add(tid)
        deduped.append(sig)

    logger.info(f"twitter_collector: {len(deduped)} unique signals "
                f"(accounts={len(accounts)}, search={len(search)})")
    return deduped
