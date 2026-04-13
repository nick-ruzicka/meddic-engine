"""collectors/linkedin_collector.py

Collects LinkedIn signals via Apify actors. Two modes:

    1. Post search      — apify/linkedin-post-search-scraper  (keyword queries)
    2. Company posts    — apify/linkedin-company-posts-scraper (ICP domains)

Classification reuses the twitter_collector helpers — same semantic buckets.
Posts are filtered to those that contain at least one configured keyword,
since Apify actors return broader results than we can score cheaply.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import timezone
from typing import Iterable

from dateutil import parser as dateutil_parser
from dotenv import load_dotenv

from collectors.twitter_collector import classify, infer_buying_stage

load_dotenv()

logger = logging.getLogger(__name__)

SEARCH_ACTOR  = "apify/linkedin-post-search-scraper"
COMPANY_ACTOR = "apify/linkedin-company-posts-scraper"
ACTOR_TIMEOUT_SECS = 60

# ── Free-tier guardrails (override via env) ─────────────────────────────────
# Defaults tuned for Apify's $5/month free tier. Each actor.call() costs
# compute units, so we cap both the number of calls and the results per call.
MAX_SEARCH_QUERIES  = int(os.getenv("APIFY_MAX_SEARCH_QUERIES",  "2"))
MAX_COMPANY_DOMAINS = int(os.getenv("APIFY_MAX_COMPANY_DOMAINS", "3"))
SEARCH_MAX_RESULTS  = int(os.getenv("APIFY_SEARCH_MAX_RESULTS",  "10"))
COMPANY_MAX_RESULTS = int(os.getenv("APIFY_COMPANY_MAX_RESULTS", "5"))
ENABLE_COMPANY_MODE = os.getenv("APIFY_ENABLE_COMPANY_MODE", "true").lower() == "true"
ENABLE_SEARCH_MODE  = os.getenv("APIFY_ENABLE_SEARCH_MODE",  "true").lower() == "true"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _token() -> str | None:
    return os.getenv("APIFY_API_TOKEN")


def domain_to_slug(domain: str) -> str:
    """'blackstone.com' → 'blackstone', 'sub.aresmgmt.co.uk' → 'aresmgmt'."""
    if not domain:
        return ""
    host = domain.strip().lower().replace("https://", "").replace("http://", "")
    host = host.split("/")[0]
    if host.startswith("www."):
        host = host[4:]
    # Strip TLD(s) — pick the label just before the first '.'
    parts = host.split(".")
    return parts[0] if parts else host


def _as_iso(raw_date) -> str:
    if not raw_date:
        return ""
    try:
        dt = dateutil_parser.parse(str(raw_date))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat(timespec="seconds")
    except Exception:
        return str(raw_date)


def _post_text(item: dict) -> str:
    """Apify post items use different text keys across actors."""
    for k in ("text", "postText", "content", "description", "body"):
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _post_url(item: dict) -> str:
    for k in ("url", "postUrl", "link", "shareUrl"):
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _author(item: dict) -> tuple[str, str]:
    """Return (author_linkedin_url, author_name)."""
    author = item.get("author") or item.get("actor") or {}
    if isinstance(author, dict):
        url = (author.get("profileUrl") or author.get("url")
               or author.get("linkedinUrl") or "")
        name = (author.get("name") or author.get("fullName")
                or author.get("title") or "")
        return url, name
    # Flat fields
    return (item.get("authorUrl") or item.get("profileUrl") or "",
            item.get("authorName") or item.get("authorFullName") or "")


def _posted_at(item: dict) -> str:
    for k in ("postedAt", "postedDate", "date", "createdAt", "publishedAt"):
        v = item.get(k)
        if v:
            return _as_iso(v)
    return ""


def _matches_keywords(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    t = (text or "").lower()
    return any(k.lower() in t for k in keywords)


def _normalize(item: dict) -> dict | None:
    text = _post_text(item)
    url = _post_url(item)
    if not text or not url:
        return None
    author_url, author_name = _author(item)
    return {
        "_url":           url,
        "signal_type":    "linkedin",
        "signal_subtype": classify(text),
        "content":        text[:500],
        "source_url":     url,
        "author_handle":  author_url,
        "author_name":    author_name,
        "signal_date":    _posted_at(item),
        "buying_stage":   infer_buying_stage(text),
        "firm_id":        None,
        "contact_id":     None,
        "raw_data":       json.dumps(item, default=str),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Actor runner
# ─────────────────────────────────────────────────────────────────────────────

def _run_actor(client, actor_id: str, run_input: dict) -> Iterable[dict]:
    """Blocking actor call; yields dataset items. Empty on any failure."""
    try:
        run = client.actor(actor_id).call(
            run_input=run_input,
            timeout_secs=ACTOR_TIMEOUT_SECS,
        )
    except Exception as e:
        logger.warning(f"apify actor {actor_id} failed: {e}")
        return []

    dataset_id = (run or {}).get("defaultDatasetId")
    if not dataset_id:
        logger.warning(f"apify actor {actor_id}: no dataset id in run result")
        return []

    try:
        return list(client.dataset(dataset_id).iterate_items())
    except Exception as e:
        logger.warning(f"apify dataset {dataset_id} fetch failed: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Collection modes
# ─────────────────────────────────────────────────────────────────────────────

def _collect_search(client, queries: list[str], keywords: list[str]) -> list[dict]:
    out: list[dict] = []
    for q in queries:
        items = _run_actor(client, SEARCH_ACTOR, {
            "searchQuery": q,
            "maxResults": SEARCH_MAX_RESULTS,
            "datePosted": "past-month",
        })
        kept = 0
        for item in items:
            sig = _normalize(item)
            if not sig:
                continue
            if not _matches_keywords(sig["content"], keywords):
                continue
            out.append(sig)
            kept += 1
        logger.info(f"linkedin:search '{q}' → {kept} kept")
    return out


def _collect_companies(client, domains: list[str], keywords: list[str]) -> list[dict]:
    out: list[dict] = []
    for domain in domains:
        slug = domain_to_slug(domain)
        if not slug:
            continue
        company_url = f"https://www.linkedin.com/company/{slug}"
        items = _run_actor(client, COMPANY_ACTOR, {
            "companyUrls": [company_url],
            "maxResults": COMPANY_MAX_RESULTS,
        })
        kept = 0
        for item in items:
            sig = _normalize(item)
            if not sig:
                continue
            if not _matches_keywords(sig["content"], keywords):
                continue
            out.append(sig)
            kept += 1
        logger.info(f"linkedin:company {slug} → {kept} kept")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Public entrypoint
# ─────────────────────────────────────────────────────────────────────────────

def collect(config: dict) -> list[dict]:
    token = _token()
    if not token:
        logger.warning("APIFY_API_TOKEN not set — linkedin_collector returning []")
        return []

    try:
        from apify_client import ApifyClient
    except ImportError:
        logger.warning("apify-client not installed — linkedin_collector returning []")
        return []

    li_cfg   = (config or {}).get("linkedin") or {}
    queries  = (li_cfg.get("search_queries") or [])[:MAX_SEARCH_QUERIES]
    domains  = (li_cfg.get("target_domains")  or [])[:MAX_COMPANY_DOMAINS]
    keywords = li_cfg.get("keywords") or []

    logger.info(
        f"linkedin guardrails: search={len(queries)}×{SEARCH_MAX_RESULTS} "
        f"company={len(domains)}×{COMPANY_MAX_RESULTS} "
        f"(modes search={ENABLE_SEARCH_MODE} company={ENABLE_COMPANY_MODE})"
    )

    client = ApifyClient(token)

    search_sigs: list[dict] = []
    if ENABLE_SEARCH_MODE and queries:
        try:
            search_sigs = _collect_search(client, queries, keywords)
        except Exception as e:
            logger.exception(f"linkedin search mode failed: {e}")

    company_sigs: list[dict] = []
    if ENABLE_COMPANY_MODE and domains:
        try:
            company_sigs = _collect_companies(client, domains, keywords)
        except Exception as e:
            logger.exception(f"linkedin company mode failed: {e}")

    # Dedup by post URL — company mode takes priority (more targeted)
    seen: set[str] = set()
    deduped: list[dict] = []
    for sig in company_sigs + search_sigs:
        url = sig.pop("_url", None)
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(sig)

    logger.info(f"linkedin_collector: {len(deduped)} unique signals "
                f"(company={len(company_sigs)}, search={len(search_sigs)})")
    return deduped
