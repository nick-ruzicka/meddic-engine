"""Hiring signal collector — intentionally minimal.

Hiring posts are supplementary to the primary press/social/SEC channels;
the scaffolding lives here so `main.py` can wire all collector types
uniformly. A follow-up revision would scrape Greenhouse / Lever /
LinkedIn Jobs for AI/ML roles at firms listed in
`config.yaml:linkedin.target_domains` and emit rows with
`signal_type='hiring'`.
"""

import logging

logger = logging.getLogger(__name__)


def collect(config: dict) -> list[dict]:
    logger.info("hiring_collector: no-op collector (supplementary channel)")
    return []
