"""Sitemap diff collector for the  Competitive Signal Engine v2.

Detects new URLs, removed URLs, and content changes in competitor sitemaps
by comparing the current sitemap against a stored baseline.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from database import get_db
from competitive.collectors.base import Collector, RawSignal
from competitive.collectors.content_hash import hash_content
from competitive.ingestion import (
    _fetch,
    _fetch_via_curl,
    parse_sitemap_xml,
    extract_text_from_html,
    classify_page_type,
)

logger = logging.getLogger(__name__)

# Minimum content length — shorter pages are SPA shells with no meaningful content.
_MIN_CONTENT_CHARS = 200

# SQL to create the baseline table.
_CREATE_BASELINE_TABLE = """
CREATE TABLE IF NOT EXISTS ci_sitemap_baseline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    competitor TEXT NOT NULL,
    url TEXT NOT NULL,
    content_hash TEXT,
    page_type TEXT,
    last_seen TEXT DEFAULT (datetime('now')),
    UNIQUE(competitor, url)
)
"""


class SitemapCollector(Collector):
    """Collector that diffs competitor sitemaps to detect URL and content changes."""

    name = "sitemap"

    def __init__(self) -> None:
        self._ensure_table()

    # ── Private helpers ────────────────────────────────────────────────────────

    def _ensure_table(self) -> None:
        """Create ci_sitemap_baseline if it doesn't exist."""
        conn = get_db()
        conn.execute(_CREATE_BASELINE_TABLE)
        conn.commit()
        conn.close()

    def _fetch_page_content(self, url: str) -> Optional[str]:
        """Fetch a URL and return extracted text, or None if fetch failed / too short."""
        html = _fetch(url)
        if html is None:
            html = _fetch_via_curl(url)
        if html is None:
            return None
        text = extract_text_from_html(html)
        if len(text.strip()) < _MIN_CONTENT_CHARS:
            logger.debug("Skipping %s — only %d chars (SPA shell)", url, len(text.strip()))
            return None
        return text

    def _fetch_sitemap_urls(self, competitor: dict) -> list[dict]:
        """Fetch and parse the competitor sitemap. Returns list of {"loc": url, ...}."""
        sitemap_url = competitor.get("sitemap_url")
        if not sitemap_url:
            logger.warning("No sitemap_url for %s", competitor.get("slug"))
            return []

        xml_text = _fetch(sitemap_url)
        if xml_text is None:
            xml_text = _fetch_via_curl(sitemap_url)
        if xml_text is None:
            logger.warning("Could not fetch sitemap for %s at %s", competitor.get("slug"), sitemap_url)
            return []

        entries = parse_sitemap_xml(xml_text)

        # Some sitemaps are sitemap indices — recurse one level to collect child sitemaps.
        expanded: list[dict] = []
        for entry in entries:
            loc = entry.get("loc", "")
            if loc.endswith(".xml"):
                # Looks like a child sitemap — fetch and parse it.
                child_xml = _fetch(loc)
                if child_xml is None:
                    child_xml = _fetch_via_curl(loc)
                if child_xml:
                    child_entries = parse_sitemap_xml(child_xml)
                    # Only add non-.xml entries from the child.
                    expanded.extend(e for e in child_entries if not e.get("loc", "").endswith(".xml"))
                # Don't add the sitemap index entry itself.
            else:
                expanded.append(entry)

        return expanded

    def _load_baseline(self, competitor_slug: str) -> dict[str, str]:
        """Return {url: content_hash} for the competitor from the baseline table."""
        conn = get_db()
        rows = conn.execute(
            "SELECT url, content_hash FROM ci_sitemap_baseline WHERE competitor = ?",
            (competitor_slug,),
        ).fetchall()
        conn.close()
        return {row["url"]: row["content_hash"] for row in rows}

    def _upsert_baseline_row(self, competitor_slug: str, url: str, content_hash: str, page_type: str) -> None:
        """Insert or update a single baseline row."""
        conn = get_db()
        conn.execute(
            """
            INSERT INTO ci_sitemap_baseline (competitor, url, content_hash, page_type, last_seen)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(competitor, url) DO UPDATE SET
                content_hash = excluded.content_hash,
                page_type    = excluded.page_type,
                last_seen    = excluded.last_seen
            """,
            (competitor_slug, url, content_hash, page_type),
        )
        conn.commit()
        conn.close()

    def _mark_baseline_seen(self, competitor_slug: str, url: str) -> None:
        """Update last_seen timestamp for a baseline row without changing the hash."""
        conn = get_db()
        conn.execute(
            "UPDATE ci_sitemap_baseline SET last_seen = datetime('now') WHERE competitor = ? AND url = ?",
            (competitor_slug, url),
        )
        conn.commit()
        conn.close()

    # ── Public interface ───────────────────────────────────────────────────────

    def baseline(self, competitor: dict) -> None:
        """One-time: fetch all sitemap URLs, scrape each page, store as baseline.

        Skips pages with <200 chars content (SPA shells).
        Rate limiting is handled by the underlying _fetch() function (1 req/sec per domain).
        """
        slug = competitor["slug"]
        logger.info("[sitemap] baseline: starting for %s", slug)

        entries = self._fetch_sitemap_urls(competitor)
        logger.info("[sitemap] baseline: found %d sitemap URLs for %s", len(entries), slug)

        stored = 0
        for entry in entries:
            url = entry.get("loc", "")
            if not url:
                continue

            text = self._fetch_page_content(url)
            if text is None:
                continue

            content_hash = hash_content(text)
            page_type = classify_page_type(url)
            self._upsert_baseline_row(slug, url, content_hash, page_type)
            stored += 1

        logger.info("[sitemap] baseline: stored %d pages for %s", stored, slug)

    def collect(self, competitor: dict) -> list[RawSignal]:
        """Diff current sitemap against baseline, emit signals, update baseline.

        Signal types:
        - new_url (confidence 0.8): URL in sitemap but not in baseline
        - content_change (confidence 0.6): URL in both but hash differs
        - url_removed (confidence 0.4): URL in baseline but not in current sitemap

        Rate limiting is handled by the underlying _fetch() function (1 req/sec per domain).
        """
        slug = competitor["slug"]
        logger.info("[sitemap] collect: starting for %s", slug)

        # Load stored baseline.
        baseline = self._load_baseline(slug)

        # Fetch current sitemap.
        entries = self._fetch_sitemap_urls(competitor)
        current_urls: set[str] = set()

        signals: list[RawSignal] = []
        now = datetime.now(timezone.utc)

        for entry in entries:
            url = entry.get("loc", "")
            if not url:
                continue
            current_urls.add(url)

            if url not in baseline:
                # New URL — fetch and store it.
                text = self._fetch_page_content(url)
                if text is None:
                    continue

                content_hash = hash_content(text)
                page_type = classify_page_type(url)
                self._upsert_baseline_row(slug, url, content_hash, page_type)

                signals.append(RawSignal(
                    competitor=slug,
                    source=self.name,
                    signal_type="new_url",
                    payload={
                        "url": url,
                        "page_type": page_type,
                        "change": "new",
                        "old_hash": None,
                        "new_hash": content_hash,
                    },
                    observed_at=now,
                    raw_url=url,
                    confidence=0.8,
                ))
                logger.debug("[sitemap] new_url: %s", url)

            else:
                # URL exists in baseline — check for content change.
                text = self._fetch_page_content(url)
                if text is None:
                    # Can't fetch, just update last_seen.
                    self._mark_baseline_seen(slug, url)
                    continue

                new_hash = hash_content(text)
                old_hash = baseline[url]
                page_type = classify_page_type(url)

                if new_hash != old_hash:
                    # Content changed.
                    self._upsert_baseline_row(slug, url, new_hash, page_type)

                    signals.append(RawSignal(
                        competitor=slug,
                        source=self.name,
                        signal_type="content_change",
                        payload={
                            "url": url,
                            "page_type": page_type,
                            "change": "modified",
                            "old_hash": old_hash,
                            "new_hash": new_hash,
                        },
                        observed_at=now,
                        raw_url=url,
                        confidence=0.6,
                    ))
                    logger.debug("[sitemap] content_change: %s", url)
                else:
                    self._mark_baseline_seen(slug, url)

        # Detect removed URLs — in baseline but not in current sitemap.
        for url in baseline:
            if url not in current_urls:
                page_type = classify_page_type(url)
                signals.append(RawSignal(
                    competitor=slug,
                    source=self.name,
                    signal_type="url_removed",
                    payload={
                        "url": url,
                        "page_type": page_type,
                        "change": "removed",
                        "old_hash": baseline[url],
                        "new_hash": None,
                    },
                    observed_at=now,
                    raw_url=url,
                    confidence=0.4,
                ))
                logger.debug("[sitemap] url_removed: %s", url)

        logger.info(
            "[sitemap] collect: %d signals for %s (%d new, %d changed, %d removed)",
            len(signals),
            slug,
            sum(1 for s in signals if s.signal_type == "new_url"),
            sum(1 for s in signals if s.signal_type == "content_change"),
            sum(1 for s in signals if s.signal_type == "url_removed"),
        )

        return signals
