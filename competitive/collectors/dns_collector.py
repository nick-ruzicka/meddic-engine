"""DNS/Subdomain collector for the  Competitive Signal Engine v2.

Uses Certificate Transparency logs via crt.sh to detect new subdomains
appearing for competitor domains — a leading indicator of new products,
documentation sites, or infrastructure changes.
"""

import logging
from datetime import datetime, timezone

import requests

from competitive.collectors.base import Collector, RawSignal
from database import get_db

logger = logging.getLogger(__name__)

CRT_SH_URL = "https://crt.sh/?q=%.{domain}&output=json"
CRT_SH_TIMEOUT = 30  # seconds — crt.sh can be slow

# Confidence scoring by subdomain prefix
_CONFIDENCE_RULES = [
    ({"docs", "api", "developer", "sdk"}, 0.9),
    ({"app", "beta", "staging", "v2"}, 0.85),
    ({"status", "security"}, 0.7),
]


def _score_subdomain(subdomain: str) -> float:
    """Return confidence score based on the leftmost label of the subdomain."""
    prefix = subdomain.split(".")[0].lower()
    for prefix_set, confidence in _CONFIDENCE_RULES:
        if prefix in prefix_set:
            return confidence
    return 0.4


def _fetch_crt_sh(domain: str) -> list[dict]:
    """Query crt.sh for all certificates issued for *.domain.

    Returns raw JSON array from crt.sh (each entry has name_value, not_before).
    Raises requests.RequestException on network/timeout failures.
    """
    url = CRT_SH_URL.format(domain=domain)
    resp = requests.get(url, timeout=CRT_SH_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _parse_subdomains(entries: list[dict], dns_root: str) -> set[str]:
    """Extract unique, clean subdomains from crt.sh JSON entries.

    Filters out:
    - Wildcard entries (*.domain.com)
    - The root domain itself
    - Any entry that doesn't end with dns_root (cross-domain SANs)
    """
    subdomains: set[str] = set()
    root = dns_root.lower()

    for entry in entries:
        raw = entry.get("name_value", "")
        # crt.sh sometimes returns newline-separated SANs in one name_value
        for name in raw.splitlines():
            name = name.strip().lower()
            if not name:
                continue
            # Skip wildcards
            if name.startswith("*"):
                continue
            # Must end with the root domain
            if not name.endswith(root):
                continue
            # Skip the root domain itself
            if name == root:
                continue
            subdomains.add(name)

    return subdomains


def _ensure_table(conn) -> None:
    """Create ci_dns_baseline if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ci_dns_baseline (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor TEXT NOT NULL,
            subdomain TEXT NOT NULL,
            first_seen TEXT DEFAULT (datetime('now')),
            last_seen TEXT DEFAULT (datetime('now')),
            UNIQUE(competitor, subdomain)
        )
    """)
    conn.commit()


def _load_baseline(conn, competitor_slug: str) -> set[str]:
    """Return the set of subdomains already in the baseline for this competitor."""
    rows = conn.execute(
        "SELECT subdomain FROM ci_dns_baseline WHERE competitor = ?",
        (competitor_slug,),
    ).fetchall()
    return {row["subdomain"] for row in rows}


def _upsert_subdomain(conn, competitor_slug: str, subdomain: str) -> None:
    """Insert a new subdomain or update last_seen if it already exists."""
    conn.execute(
        """
        INSERT INTO ci_dns_baseline (competitor, subdomain, first_seen, last_seen)
        VALUES (?, ?, datetime('now'), datetime('now'))
        ON CONFLICT(competitor, subdomain)
        DO UPDATE SET last_seen = datetime('now')
        """,
        (competitor_slug, subdomain),
    )


class DNSCollector(Collector):
    """Monitors competitor subdomains via Certificate Transparency (crt.sh)."""

    name = "dns"

    # ── baseline ────────────────────────────────────────────────────────────────

    def baseline(self, competitor: dict) -> None:
        """Record all currently-known subdomains as the baseline for this competitor.

        Safe to call multiple times — uses upsert so existing rows are not lost.
        """
        dns_root = competitor.get("dns_root") or competitor.get("domain")
        slug = competitor["slug"]

        logger.info("[dns] Baselining %s (root=%s)", slug, dns_root)

        try:
            entries = _fetch_crt_sh(dns_root)
        except Exception as exc:
            logger.warning("[dns] crt.sh request failed for %s during baseline: %s", slug, exc)
            return

        subdomains = _parse_subdomains(entries, dns_root)
        logger.info("[dns] Baseline: found %d subdomains for %s", len(subdomains), slug)

        conn = get_db()
        try:
            _ensure_table(conn)
            for subdomain in subdomains:
                _upsert_subdomain(conn, slug, subdomain)
            conn.commit()
        finally:
            conn.close()

    # ── collect ─────────────────────────────────────────────────────────────────

    def collect(self, competitor: dict) -> list[RawSignal]:
        """Detect new subdomains that have appeared since the last baseline/collect run.

        Returns one RawSignal(signal_type="new_subdomain") per new subdomain found.
        Updates last_seen for all known subdomains and inserts new ones into baseline.
        """
        dns_root = competitor.get("dns_root") or competitor.get("domain")
        slug = competitor["slug"]

        logger.info("[dns] Collecting subdomains for %s (root=%s)", slug, dns_root)

        try:
            entries = _fetch_crt_sh(dns_root)
        except Exception as exc:
            logger.warning("[dns] crt.sh request failed for %s: %s", slug, exc)
            return []

        current_subdomains = _parse_subdomains(entries, dns_root)

        conn = get_db()
        signals: list[RawSignal] = []
        try:
            _ensure_table(conn)
            known = _load_baseline(conn, slug)

            new_subdomains = current_subdomains - known
            logger.info(
                "[dns] %s: %d current, %d known, %d new",
                slug, len(current_subdomains), len(known), len(new_subdomains),
            )

            now = datetime.now(timezone.utc)

            for subdomain in sorted(new_subdomains):
                confidence = _score_subdomain(subdomain)
                signal = RawSignal(
                    competitor=slug,
                    source=self.name,
                    signal_type="new_subdomain",
                    payload={
                        "subdomain": subdomain,
                        "record_type": "A",          # crt.sh doesn't give record type
                        "first_seen": now.date().isoformat(),
                        "resolves_to": None,          # resolution not performed here
                    },
                    observed_at=now,
                    raw_url=f"https://crt.sh/?q={subdomain}",
                    confidence=confidence,
                )
                signals.append(signal)
                _upsert_subdomain(conn, slug, subdomain)

            # Update last_seen for all currently-seen subdomains (new + known)
            for subdomain in current_subdomains:
                conn.execute(
                    """
                    INSERT INTO ci_dns_baseline (competitor, subdomain, first_seen, last_seen)
                    VALUES (?, ?, datetime('now'), datetime('now'))
                    ON CONFLICT(competitor, subdomain)
                    DO UPDATE SET last_seen = datetime('now')
                    """,
                    (slug, subdomain),
                )

            conn.commit()
        finally:
            conn.close()

        return signals
