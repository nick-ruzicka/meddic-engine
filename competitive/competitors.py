"""Competitor registry and URL verification for the competitive intelligence module.

`COMPETITORS_V1` — 5 tier-1 competitors tracked immediately.
`COMPETITORS_V2` — 10 additional tier 2-3 competitors staged for future ingestion.

Each entry is a 5-tuple: (slug, name, url, tier, positioning)

Public API
----------
seed_competitors(v2=False)
    Upsert all V1 competitors (and optionally V2) into the DB.

verify_urls(slugs=None)
    HEAD-request each competitor URL, update url_ok in the DB, and log warnings
    for any that return a non-2xx status or raise a connection error.
"""

import logging
from typing import Optional

import requests

from competitive.models import (
    get_all_competitors,
    get_competitor,
    init_competitive_db,
    upsert_competitor,
)

logger = logging.getLogger(__name__)

_UA = "CIBot/1.0"
_TIMEOUT = 10  # seconds

# ── Competitor registries ──────────────────────────────────────────────────────

# Tier-1: immediate targets, highest competitive relevance to .
COMPETITORS_V1: list[tuple] = [
    ("alphasense", "AlphaSense", "https://www.alphasense.com", 1, "Incumbent, acquired Tegus 2024"),
    ("rogo", "Rogo", "https://rogo.ai", 1, "Dominant in sell-side IB, $75M Series C"),
    ("f2", "F2.ai", "https://f2.ai", 1, "Deterministic spreadsheet computation, explicit  challenger"),
    ("blueflame", "Blueflame AI", "https://blueflame.ai", 1, "Acquired by Datasite, embedded in VDR"),
    ("keye", "Keye", "https://keye.co", 1, "YC F24, Odin co-pilot, built by investors"),
]

# Tier 2-3: monitored competitors staged for future ingestion rounds.
COMPETITORS_V2: list[tuple] = [
    ("aumni", "Aumni", "https://aumni.fund", 2, "VC portfolio analytics, acquired by JPMorgan"),
    ("luminance", "Luminance", "https://www.luminance.com", 2, "AI contract review, legal focus"),
    ("kensho", "Kensho", "https://kensho.com", 2, "S&P Global AI, NLP for financial data"),
    ("visible-alpha", "Visible Alpha", "https://visiblealpha.com", 2, "Consensus estimates platform"),
    ("tegus", "Tegus", "https://www.tegus.com", 2, "Expert network + primary research, acquired by AlphaSense"),
    ("grata", "Grata", "https://grata.com", 3, "Private company intelligence for deal sourcing"),
    ("oria", "Oria", "https://oria.ai", 3, "AI for credit underwriting"),
    ("hyperplane", "Hyperplane", "https://hyperplane.co", 3, "Behavioral data platform for financial services"),
    ("daloopa", "Daloopa", "https://www.daloopa.com", 3, "AI-driven financial model automation"),
    ("quasr", "Quasr", "https://quasr.io", 3, "Regulatory intelligence and compliance AI"),
]


# ── Seeding ────────────────────────────────────────────────────────────────────

def seed_competitors(v2: bool = False) -> None:
    """Upsert competitor records into the DB.

    Parameters
    ----------
    v2 : bool
        When True, also seed COMPETITORS_V2 in addition to COMPETITORS_V1.
    """
    entries = list(COMPETITORS_V1)
    if v2:
        entries = entries + list(COMPETITORS_V2)

    for slug, name, url, tier, positioning in entries:
        upsert_competitor(slug, name, url, tier=tier, positioning=positioning)
        logger.debug("Seeded competitor: %s (%s)", slug, name)

    logger.info(
        "seed_competitors complete: %d entries seeded (v2=%s).",
        len(entries),
        v2,
    )


# ── URL verification ───────────────────────────────────────────────────────────

def verify_urls(slugs: Optional[list[str]] = None) -> dict[str, bool]:
    """HEAD-request each competitor URL and update url_ok in the DB.

    Parameters
    ----------
    slugs : list[str] or None
        Restrict verification to these slugs. When None, all competitors in
        the DB are verified.

    Returns
    -------
    dict[str, bool]
        Mapping of slug -> True (reachable) / False (failed).
    """
    session = requests.Session()
    session.headers.update({"User-Agent": _UA})

    if slugs is not None:
        rows = [get_competitor(s) for s in slugs]
        rows = [r for r in rows if r is not None]
    else:
        rows = get_all_competitors()

    results: dict[str, bool] = {}

    for row in rows:
        slug = row["slug"]
        url = row["url"]

        if not url:
            logger.warning("Competitor %s has no URL — skipping.", slug)
            results[slug] = False
            upsert_competitor(
                slug, row["name"], url or "",
                tier=row["tier"],
                positioning=row["positioning"],
                url_ok=0,
            )
            continue

        ok = _check_url(session, slug, url)
        results[slug] = ok

        upsert_competitor(
            slug, row["name"], url,
            tier=row["tier"],
            positioning=row["positioning"],
            url_ok=1 if ok else 0,
        )

    return results


def _check_url(session: requests.Session, slug: str, url: str) -> bool:
    """Return True if a HEAD request to `url` succeeds with a 2xx or 3xx status."""
    try:
        resp = session.head(url, timeout=_TIMEOUT, allow_redirects=True)
        if resp.status_code < 400:
            logger.debug("URL OK [%d]: %s (%s)", resp.status_code, url, slug)
            return True
        else:
            logger.warning(
                "URL check failed for %s (%s): HTTP %d", slug, url, resp.status_code
            )
            return False
    except requests.RequestException as exc:
        logger.warning("URL check error for %s (%s): %s", slug, url, exc)
        return False
