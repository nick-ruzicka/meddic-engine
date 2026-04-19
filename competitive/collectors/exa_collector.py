"""Exa trending mention collector.

Uses the Exa neural search API to find new mentions of competitors
across news, forums, and Reddit. Compares against a baseline of
previously-seen URLs to surface net-new signals.

Requires EXA_API_KEY env var. Skips gracefully if not set.
"""

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from competitive.collectors.base import Collector, RawSignal
from database import get_db

logger = logging.getLogger(__name__)

_CREATE_BASELINE = """
CREATE TABLE IF NOT EXISTS ci_exa_baseline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    competitor TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    first_seen TEXT DEFAULT (datetime('now')),
    UNIQUE(competitor, url)
)
"""


def _ensure_table(conn) -> None:
    conn.execute(_CREATE_BASELINE)
    conn.commit()


def _get_exa_client():
    """Return an Exa client, or None if EXA_API_KEY is not set."""
    api_key = os.getenv("EXA_API_KEY")
    if not api_key:
        logger.warning("EXA_API_KEY not set — skipping Exa collector")
        return None
    try:
        from exa_py import Exa  # noqa: PLC0415
        return Exa(api_key=api_key)
    except ImportError:
        logger.error("exa_py not installed — skipping Exa collector")
        return None


def _run_queries(exa, queries: list[str]) -> list[dict]:
    """Run all exa_queries for a competitor and return deduplicated results."""
    seen_urls: set[str] = set()
    results: list[dict] = []
    for query in queries:
        try:
            response = exa.search(
                query,
                num_results=10,
                use_autoprompt=False,
                type="neural",
            )
            for item in response.results:
                url = item.url
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                results.append({
                    "url": url,
                    "title": getattr(item, "title", None) or "",
                    "published_date": getattr(item, "published_date", None),
                    "snippet": getattr(item, "text", None) or "",
                    "query_matched": query,
                })
        except Exception as exc:  # noqa: BLE001
            logger.warning("Exa query %r failed: %s", query, exc)
    return results


def _confidence_from_published(published_date: Optional[str]) -> float:
    """Return confidence based on how recently the article was published."""
    if not published_date:
        return 0.3
    try:
        pub_dt = datetime.fromisoformat(published_date.replace("Z", "+00:00"))
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - pub_dt
        if age <= timedelta(days=7):
            return 0.8
        if age <= timedelta(days=30):
            return 0.5
        return 0.3
    except (ValueError, TypeError):
        return 0.3


class ExaCollector(Collector):
    """Collector that surfaces new trending mentions via the Exa API."""

    name = "exa"

    # ------------------------------------------------------------------
    # baseline()
    # ------------------------------------------------------------------

    def baseline(self, competitor: dict) -> None:
        """Record all currently-visible Exa results as the baseline state.

        After calling this, collect() will only return URLs that are NEW
        relative to what was seen here.
        """
        exa = _get_exa_client()
        if exa is None:
            return

        queries: list[str] = competitor.get("exa_queries") or []
        if not queries:
            return

        slug: str = competitor["slug"]
        results = _run_queries(exa, queries)

        with get_db() as conn:
            _ensure_table(conn)
            for item in results:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO ci_exa_baseline
                        (competitor, url, title)
                    VALUES (?, ?, ?)
                    """,
                    (slug, item["url"], item["title"]),
                )
            conn.commit()

        logger.info(
            "exa baseline: stored %d URLs for %s", len(results), slug
        )

    # ------------------------------------------------------------------
    # collect()
    # ------------------------------------------------------------------

    def collect(self, competitor: dict) -> list[RawSignal]:
        """Return RawSignals for URLs not previously seen in the baseline."""
        exa = _get_exa_client()
        if exa is None:
            return []

        queries: list[str] = competitor.get("exa_queries") or []
        if not queries:
            return []

        slug: str = competitor["slug"]
        results = _run_queries(exa, queries)

        with get_db() as conn:
            _ensure_table(conn)

            # Fetch existing baseline URLs for this competitor
            rows = conn.execute(
                "SELECT url FROM ci_exa_baseline WHERE competitor = ?",
                (slug,),
            ).fetchall()
            known_urls: set[str] = {row[0] for row in rows}

            signals: list[RawSignal] = []
            now = datetime.now(timezone.utc)

            for item in results:
                url = item["url"]
                if url in known_urls:
                    continue

                # New URL — emit a signal
                confidence = _confidence_from_published(item.get("published_date"))
                signal = RawSignal(
                    competitor=slug,
                    source="exa",
                    signal_type="trending_mention",
                    payload={
                        "title": item["title"],
                        "url": url,
                        "published_at": item.get("published_date") or "",
                        "snippet": item.get("snippet") or "",
                        "query_matched": item.get("query_matched") or "",
                    },
                    observed_at=now,
                    raw_url=url,
                    confidence=confidence,
                )
                signals.append(signal)

                # Add to baseline so we don't re-emit on next run
                conn.execute(
                    """
                    INSERT OR IGNORE INTO ci_exa_baseline
                        (competitor, url, title)
                    VALUES (?, ?, ?)
                    """,
                    (slug, url, item["title"]),
                )

            conn.commit()

        logger.info(
            "exa collect: %d new signals for %s", len(signals), slug
        )
        return signals
