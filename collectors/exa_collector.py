"""Exa press collector — STUB.

TODO: implement Exa semantic search over financial press using queries
from config.yaml:exa.queries, filtered to exa.preferred_domains and
exa.recency_days. Normalize into signal rows with signal_type='press'.
"""

import logging

logger = logging.getLogger(__name__)


def collect(config: dict) -> list[dict]:
    logger.info("exa_collector: stub — returning 0 signals")
    return []
