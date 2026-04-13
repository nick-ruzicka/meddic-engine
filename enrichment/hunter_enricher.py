"""enrichment/hunter_enricher.py

Contact enrichment — multi-source waterfall.

Public API:
    find_email(name, domain)                    -> dict   (Hunter finder)
    verify_email(email)                         -> dict   (Hunter verifier)
    find_contact(name, domain, existing_email)  -> dict   (waterfall)
    enrich_contacts(limit=50)                   -> int

Waterfall order in find_contact():
    1. Hunter.io email finder
    2. Pattern guess + Hunter verifier
    3. (future) Apollo.io, Proxycurl, Exa — see slots below

Every source returns the same shape:
    {"email": str|None, "score": int, "verified": bool, "source": str}

Adding a new source = add one elif block in find_contact(). That's it.

Rate limits: Hunter free tier is 25 requests/month; sleep 0.5s between
calls as a soft guard.
"""

from __future__ import annotations

import logging
import os
import time

import requests
from dotenv import load_dotenv

from database import get_db
from utils.helpers import load_config, now_iso

load_dotenv()

logger = logging.getLogger(__name__)

BASE_URL = "https://api.hunter.io/v2"
TIMEOUT = 10
SLEEP_BETWEEN = 0.5

_EMPTY_FINDER = {"email": None, "score": 0, "verified": False, "source": "hunter_finder"}


def _api_key() -> str | None:
    return os.getenv("HUNTER_API_KEY")


# ─────────────────────────────────────────────────────────────────────────────
# Finder + Verifier
# ─────────────────────────────────────────────────────────────────────────────

def find_email(name: str, domain: str) -> dict:
    key = _api_key()
    if not key or not name or not domain:
        return dict(_EMPTY_FINDER)

    try:
        r = requests.get(
            f"{BASE_URL}/email-finder",
            params={"full_name": name, "domain": domain, "api_key": key},
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            logger.warning(f"hunter finder {r.status_code}: {r.text[:200]}")
            return dict(_EMPTY_FINDER)
        data = (r.json() or {}).get("data") or {}
    except requests.RequestException as e:
        logger.warning(f"hunter finder request failed: {e}")
        return dict(_EMPTY_FINDER)

    email = data.get("email")
    score = int(data.get("score") or 0)
    status = (data.get("verification") or {}).get("status") or ""
    return {
        "email": email,
        "score": score,
        "verified": status == "valid",
        "source": "hunter_finder",
    }


def verify_email(email: str) -> dict:
    base = {"email": email, "score": 0, "verified": False, "source": "hunter_verifier"}
    key = _api_key()
    if not key or not email:
        return base

    try:
        r = requests.get(
            f"{BASE_URL}/email-verifier",
            params={"email": email, "api_key": key},
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            logger.warning(f"hunter verifier {r.status_code}: {r.text[:200]}")
            return base
        data = (r.json() or {}).get("data") or {}
    except requests.RequestException as e:
        logger.warning(f"hunter verifier request failed: {e}")
        return base

    return {
        "email": email,
        "score": int(data.get("score") or 0),
        "verified": (data.get("status") or "") == "valid",
        "source": "hunter_verifier",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Pattern guess — free fallback when Hunter finder whiffs
# ─────────────────────────────────────────────────────────────────────────────

_NAME_STRIP = (" (TBD)", " TBD", ",", ".")


def _clean_name(name: str) -> str:
    out = name or ""
    for s in _NAME_STRIP:
        out = out.replace(s, " ")
    # Drop titles/prefixes
    parts = [p for p in out.split() if p.lower() not in
             ("dr", "mr", "mrs", "ms", "prof", "sir", "the")]
    return " ".join(parts).strip()


def _name_parts(name: str) -> tuple[str, str] | None:
    clean = _clean_name(name)
    toks = [t for t in clean.replace("-", " ").split() if t.isalpha()]
    if len(toks) < 2:
        return None
    return toks[0].lower(), toks[-1].lower()


def _pattern_candidates(name: str, domain: str) -> list[str]:
    p = _name_parts(name)
    if not p:
        return []
    first, last = p
    fi = first[0]
    return [
        f"{first}.{last}@{domain}",
        f"{fi}{last}@{domain}",
        f"{first}{last}@{domain}",
        f"{first}_{last}@{domain}",
        f"{first}@{domain}",
        f"{last}@{domain}",
        f"{fi}.{last}@{domain}",
    ]


def pattern_guess(name: str, domain: str) -> dict:
    """Guess addresses from common patterns; verify each against Hunter.
    Returns the first one that verifies, or an empty result."""
    empty = {"email": None, "score": 0, "verified": False, "source": "pattern_guess"}
    if not _api_key() or not name or not domain:
        return empty

    for candidate in _pattern_candidates(name, domain):
        v = verify_email(candidate)
        if v["verified"]:
            return {
                "email": candidate,
                "score": v["score"],
                "verified": True,
                "source": "pattern_guess",
            }
        time.sleep(SLEEP_BETWEEN)
    return empty


# ─────────────────────────────────────────────────────────────────────────────
# Proxycurl step — LinkedIn role search, then Hunter-find on the resolved name
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_ROLE_KEYWORDS = "CTO CIO technology AI head"
_PROXYCURL_HUNTER_MIN = 60  # accept Hunter scores ≥ this on a Proxycurl-named hit


def _proxycurl_then_hunter(domain: str, min_conf: int) -> dict:
    """Step 3: Proxycurl finds a named senior exec, Hunter finds their email.
    Proxycurl API was sunset. NinjaPear (successor) has no equivalent role-search
    endpoint, so this step is a no-op. Kept as an extension point."""
    empty = {"email": None, "score": 0, "verified": False, "source": "proxycurl_disabled"}
    if not os.getenv("PROXYCURL_ENABLED"):
        return empty
    try:
        from enrichment.proxycurl_enricher import search_by_role
    except ImportError:
        return empty

    candidates = search_by_role(domain, _DEFAULT_ROLE_KEYWORDS, page_size=3)
    if not candidates:
        return empty

    for c in candidates:
        h = find_email(c["full_name"], domain)
        if h["email"] and (h["score"] >= _PROXYCURL_HUNTER_MIN or h.get("verified")):
            return {
                "email":    h["email"],
                "score":    h["score"],
                "verified": h.get("verified", False),
                "source":   "proxycurl+hunter",
                "proxycurl_name":  c["full_name"],
                "proxycurl_title": c["title"],
                "linkedin_url":    c["linkedin_url"],
            }
        time.sleep(SLEEP_BETWEEN)
    # No hunter match — still surface the Proxycurl name so the caller
    # can at least replace "TBD" with a real person
    top = candidates[0]
    return {
        "email":    None,
        "score":    0,
        "verified": False,
        "source":   "proxycurl_no_email",
        "proxycurl_name":  top["full_name"],
        "proxycurl_title": top["title"],
        "linkedin_url":    top["linkedin_url"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Waterfall
# ─────────────────────────────────────────────────────────────────────────────

def find_contact(name: str, domain: str, existing_email: str | None = None) -> dict:
    """Try sources in order; return on first success.
    A source 'succeeds' when it returns a verified email OR a Hunter-finder
    result with score >= min_confidence."""
    min_conf = _min_confidence()

    # 1. Hunter finder
    h = find_email(name, domain)
    if h["email"] and h["score"] >= min_conf:
        return {**h, "source": "hunter"}

    # 2. Pattern guess + verify
    p = pattern_guess(name, domain)
    if p["verified"]:
        return p

    # 3. Proxycurl — LinkedIn role search, then Hunter-find on the resolved name
    px = _proxycurl_then_hunter(domain, min_conf)
    if px["email"]:
        return px

    # 4. TODO: Apollo.io contact lookup
    # 5. TODO: Exa contact search

    # If we had a Hunter hit below threshold, return that as best-effort
    if h["email"]:
        return {**h, "source": "hunter_low_conf"}

    return {"email": None, "score": 0, "verified": False, "source": "none"}


# ─────────────────────────────────────────────────────────────────────────────
# Batch enrichment
# ─────────────────────────────────────────────────────────────────────────────

def _min_confidence() -> int:
    try:
        cfg = load_config()
        return int(((cfg.get("hunter") or {}).get("min_confidence")) or 70)
    except Exception:
        return 70


def _candidates(conn, limit: int) -> list[dict]:
    rows = conn.execute(
        """SELECT c.id, c.name, c.email, c.email_verified,
                  f.id AS firm_id, f.name AS firm_name, f.domain,
                  COALESCE(s.score, 0) AS score
           FROM contacts c
           JOIN firms f ON f.id = c.firm_id
           LEFT JOIN scores s ON s.contact_id = c.id
           WHERE (c.email IS NULL OR c.email = '' OR c.email_verified = 0)
             AND f.domain IS NOT NULL AND f.domain != ''
           ORDER BY score DESC, c.id ASC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def enrich_contacts(limit: int = 50) -> int:
    if not _api_key():
        logger.warning("HUNTER_API_KEY not set — skipping enrichment")
        return 0

    min_conf = _min_confidence()
    enriched = 0
    conn = get_db()
    try:
        candidates = _candidates(conn, limit)
        if not candidates:
            logger.info("hunter: no candidates needing enrichment")
            return 0

        logger.info(f"hunter: enriching up to {len(candidates)} contacts "
                    f"(min_confidence={min_conf})")

        for i, c in enumerate(candidates):
            name, domain = c["name"], c["domain"]
            result = find_contact(name, domain, c["email"])
            found_email = result["email"]
            found_score = result["score"]
            src = result["source"]
            accepted = (result["verified"]
                        or (found_email and found_score >= min_conf))

            if accepted and found_email:
                conn.execute(
                    """UPDATE contacts
                       SET email = ?, email_verified = 1,
                           email_source = ?, updated_at = ?
                       WHERE id = ?""",
                    (found_email, src, now_iso(), c["id"]),
                )
                conn.commit()
                enriched += 1
                logger.info(f"✓ [{src}] {found_email} (score {found_score}) — {name} @ {domain}")
            elif c["email"]:
                v = verify_email(c["email"])
                conn.execute(
                    """UPDATE contacts
                       SET email_verified = ?, updated_at = ?
                       WHERE id = ?""",
                    (1 if v["verified"] else 0, now_iso(), c["id"]),
                )
                conn.commit()
                if v["verified"]:
                    enriched += 1
                    logger.info(f"✓ verified existing {c['email']} (score {v['score']}) — {name}")
                else:
                    logger.info(f"✗ unverified {c['email']} (score {v['score']}) — {name}")
            else:
                logger.info(f"✗ No email found [{src}] — {name} @ {domain}")

            if i < len(candidates) - 1:
                time.sleep(SLEEP_BETWEEN)
    finally:
        conn.close()

    logger.info(f"hunter: enriched {enriched}/{len(candidates)} contacts")
    return enriched
