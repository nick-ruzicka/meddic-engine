"""Hiring signal collector — STUB.

TODO: scrape job boards (Greenhouse, Lever, LinkedIn Jobs) for AI/ML roles
at firms in config.yaml:linkedin.target_domains. Match titles against
config.yaml:target_titles. Normalize into signals with signal_type='hiring'.
"""

import logging

logger = logging.getLogger(__name__)


def collect(config: dict) -> list[dict]:
    logger.info("hiring_collector: stub — returning 0 signals")
    return []
