"""
utils/helpers.py
Shared utilities: config loading, date helpers, signal freshness calculation.
"""
from __future__ import annotations

import yaml
import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from dateutil import parser as dateutil_parser

logger = logging.getLogger(__name__)

_config_cache: dict[str, dict] = {}


def load_config(config_path: str = "config/config.yaml") -> dict:
    """Load and cache the YAML config file, keyed by resolved path."""
    key = str(Path(config_path).resolve())
    if key not in _config_cache:
        with open(config_path, "r") as f:
            _config_cache[key] = yaml.safe_load(f)
    return _config_cache[key]


def calculate_freshness_days(signal_date_str: str) -> int:
    """
    Calculate how many days ago a signal occurred.
    Accepts ISO format, Twitter date strings, or common formats.
    Returns 999 if date cannot be parsed (treat as stale).
    """
    if not signal_date_str:
        return 999
    try:
        signal_dt = dateutil_parser.parse(signal_date_str)
        if signal_dt.tzinfo is None:
            signal_dt = signal_dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = now - signal_dt
        return max(0, delta.days)
    except Exception as e:
        logger.warning(f"Could not parse date '{signal_date_str}': {e}")
        return 999


def freshness_score(days: int) -> float:
    """
    Convert days-since-signal into a 0-100 freshness score.
    Under 7 days: 90-100
    7-30 days: 60-89
    30-90 days: 30-59
    90+ days: 0-29
    """
    if days <= 7:
        return 100 - (days * 1.5)
    elif days <= 30:
        return 89 - ((days - 7) * 1.26)
    elif days <= 90:
        return 59 - ((days - 30) * 0.48)
    else:
        return max(0, 29 - ((days - 90) * 0.1))


def safe_json(obj) -> str:
    """Safely serialize to JSON string."""
    try:
        return json.dumps(obj, default=str)
    except Exception:
        return "{}"


def now_iso() -> str:
    """Return current UTC time as ISO string."""
    return datetime.now(timezone.utc).isoformat()


def truncate(text: str, max_chars: int = 500) -> str:
    """Truncate text for storage, preserving whole words where possible."""
    if not text or len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "..."


def sanitize_untrusted(text: str | None, max_chars: int = 150) -> str:
    """Strip non-printable chars and truncate. For external content (Twitter,
    press) before interpolating into LLM prompts."""
    return "".join(ch for ch in (text or "") if ch.isprintable())[:max_chars]
