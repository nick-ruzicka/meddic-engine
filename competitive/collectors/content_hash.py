"""Content hashing utilities for the sitemap collector.

Provides deterministic hashing for page content to detect meaningful changes
while ignoring ephemeral elements like timestamps and tracking parameters.
"""

import hashlib
import re


# ── Normalization ──────────────────────────────────────────────────────────────

# Patterns for content elements that change without meaningful page edits.
_TRACKING_PARAMS = re.compile(
    r"utm_[a-z]+=[^&\s\"']+",
    re.IGNORECASE,
)
_SESSION_IDS = re.compile(
    r"(?:session_?id|sid|sessiontoken|jsessionid)=[a-zA-Z0-9_\-]+",
    re.IGNORECASE,
)
# ISO timestamps: 2026-04-19T12:34:56Z, 2026-04-19 12:34:56, etc.
_TIMESTAMPS = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?",
)
# Standalone date strings like "April 19, 2026" or "Apr 19, 2026"
_DATE_STRINGS = re.compile(
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\s+\d{1,2},?\s+\d{4}",
    re.IGNORECASE,
)
# Numeric-only dates: 2026-04-19 or 04/19/2026
_NUMERIC_DATES = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\b",
)
# Cache-buster / nonce query params
_CACHE_BUSTERS = re.compile(
    r"(?:_=[0-9]+|v=[0-9]+|cachebuster=[^&\s\"']+|nonce=[a-zA-Z0-9]+)",
    re.IGNORECASE,
)


def normalize_for_hash(text: str) -> str:
    """Strip tracking params, timestamps, session IDs from text before hashing.

    This ensures that content-identical pages with different ephemeral elements
    (e.g. analytics params, rendered dates, cache busters) produce the same hash.
    """
    if not text:
        return ""

    normalized = text

    # Remove tracking and session parameters
    normalized = _TRACKING_PARAMS.sub("", normalized)
    normalized = _SESSION_IDS.sub("", normalized)
    normalized = _CACHE_BUSTERS.sub("", normalized)

    # Remove timestamps and date strings
    normalized = _TIMESTAMPS.sub("", normalized)
    normalized = _DATE_STRINGS.sub("", normalized)
    normalized = _NUMERIC_DATES.sub("", normalized)

    # Normalize whitespace and case
    normalized = " ".join(normalized.split()).lower()

    return normalized


# ── Hashing ────────────────────────────────────────────────────────────────────

def hash_content(text: str) -> str:
    """SHA256 of normalized text (strip whitespace, lowercase).

    Returns first 16 hex characters of the digest.
    Same content always produces the same hash; different content produces
    different hashes (with overwhelming probability).
    """
    normalized = normalize_for_hash(text)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return digest[:16]
