"""Base classes for competitive signal collectors.

All collectors implement the Collector ABC and emit RawSignal instances.
The classifier layer consumes RawSignals and produces ClassifiedSignals.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class RawSignal:
    """A raw signal detected by a collector, before classification."""

    competitor: str        # slug, e.g. "alphasense"
    source: str            # collector name: "sitemap" | "jobs" | "dns" | "github" | "exa"
    signal_type: str       # "new_url" | "job_posting" | "new_subdomain" |
                           # "commit_burst" | "new_repo" | "star_spike" |
                           # "trending_mention" | "content_change" | "url_removed"
    payload: dict          # type-specific data (see SPEC.md Payload Schemas)
    observed_at: datetime  # when the collector detected it
    raw_url: Optional[str] = None   # link to the source
    confidence: float = 0.5         # 0.0-1.0, set by collector


class Collector(ABC):
    """Abstract base for all signal collectors.

    Each collector monitors one signal source (sitemap, jobs, DNS, GitHub, Exa)
    across all competitors. It must implement:
    - collect(): run once per competitor per day, return new signals
    - baseline(): one-time capture of current state for future diffing
    """

    name: str  # "sitemap", "jobs", "dns", "github", "exa"

    @abstractmethod
    def collect(self, competitor: dict) -> list[RawSignal]:
        """Run once per competitor per day. Return raw signals detected since last run.

        Args:
            competitor: dict from competitors.yaml with keys:
                slug, name, domain, sitemap_url, jobs_url, jobs_source,
                dns_root, github_orgs, exa_queries

        Returns:
            List of RawSignal instances. Empty list if no new signals.
        """

    @abstractmethod
    def baseline(self, competitor: dict) -> None:
        """One-time: record current state as baseline for future diffs.

        Called once per competitor when the system is first set up.
        After baseline, collect() only returns NEW signals (diffs from baseline).
        """
