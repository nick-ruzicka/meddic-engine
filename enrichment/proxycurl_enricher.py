"""enrichment/proxycurl_enricher.py

Proxycurl LinkedIn lookup — used as step 3 in the contact-enrichment
waterfall when Hunter + pattern-guess can't find a named executive.

Public API:
    search_by_role(domain, role_keywords) -> list[dict]

Each result has:
    {
        "full_name":    str,
        "title":        str,
        "linkedin_url": str,
        "domain":       str,     # echoed back for convenience
        "source":       "proxycurl",
    }

Auth: NINJA_PEAR_API_KEY env var (service rebranded from Proxycurl).
Falls back to PROXYCURL_API_KEY for backwards compatibility. Missing →
warn and return [].
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

ENDPOINT = "https://nubela.co/proxycurl/api/v2/linkedin/company/employees/search"
TIMEOUT = 20


def _api_key() -> str | None:
    return os.getenv("NINJA_PEAR_API_KEY") or os.getenv("PROXYCURL_API_KEY")


def _pick(d: dict, keys: list[str], default: Any = "") -> Any:
    for k in keys:
        if k in d and d[k]:
            return d[k]
    return default


def _normalize(hit: dict, domain: str) -> dict | None:
    """Proxycurl response shape varies by endpoint version — be flexible."""
    profile = hit.get("profile") or hit
    first = _pick(profile, ["first_name", "firstName"])
    last  = _pick(profile, ["last_name",  "lastName"])
    full  = _pick(profile, ["full_name",  "fullName", "name"]) or f"{first} {last}".strip()
    if not full:
        return None

    title = (_pick(profile, ["occupation", "job_title", "headline", "title"])
             or _pick(hit,  ["role_title", "title"]))

    linkedin_url = (_pick(hit, ["linkedin_profile_url", "profile_url", "url"])
                    or _pick(profile, ["public_profile_url", "url"]))

    return {
        "full_name":    full.strip(),
        "title":        (title or "").strip(),
        "linkedin_url": linkedin_url or "",
        "domain":       domain,
        "source":       "proxycurl",
    }


def search_by_role(domain: str, role_keywords: str, page_size: int = 3) -> list[dict]:
    """Search employees of a company (by domain) filtered by role keywords.

    Returns an empty list on any failure — callers should treat missing
    data the same as "no match" rather than exception-handling.
    """
    key = _api_key()
    if not key:
        logger.warning("NINJA_PEAR_API_KEY not set — proxycurl_enricher returning []")
        return []
    if not domain:
        return []

    body = {
        "company_domain":      domain,
        "role_search_keyword": role_keywords,
        "page_size":           page_size,
    }
    try:
        r = requests.post(
            ENDPOINT,
            json=body,
            headers={"Authorization": f"Bearer {key}"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        logger.warning(f"proxycurl request failed: {e}")
        return []

    if r.status_code != 200:
        logger.warning(f"proxycurl {r.status_code}: {r.text[:200]}")
        return []

    try:
        payload = r.json()
    except ValueError:
        logger.warning("proxycurl returned non-JSON response")
        return []

    # Response shape varies: try common keys for the hit list
    hits = (payload.get("employees") or payload.get("results")
            or payload.get("hits") or payload.get("employee_search_results")
            or payload if isinstance(payload, list) else [])

    out = []
    for hit in hits:
        norm = _normalize(hit, domain)
        if norm:
            out.append(norm)

    logger.info(f"proxycurl: {domain} → {len(out)} matches "
                f"(keywords='{role_keywords}')")
    return out
