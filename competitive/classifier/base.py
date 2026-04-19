"""Base classes for signal classification.

The classifier consumes RawSignals from collectors and produces
ClassifiedSignals with category, predictive score, lead time, and
a one-sentence takeaway for Tom.
"""

from dataclasses import dataclass
from competitive.collectors.base import RawSignal


@dataclass
class ClassifiedSignal:
    """A signal that has been classified with predictive metadata.

    Inherits all fields from the source RawSignal, plus classification fields.
    """

    # Original signal fields
    competitor: str
    source: str
    signal_type: str
    payload: dict
    observed_at: object  # datetime
    raw_url: str
    confidence: float

    # Classification fields
    category: str = ""              # "launch_signal" | "hiring_signal" |
                                    # "infrastructure_signal" | "content_signal" | "noise"
    predictive_score: float = 0.0   # 0.0-1.0, higher = more likely to predict a real move
    lead_time_estimate: str = ""    # "immediate" | "2-4 weeks" | "60-90 days"
    tom_takeaway: str = ""          # one sentence, sales-angle framing

    @classmethod
    def from_raw(cls, raw: RawSignal, **classification) -> "ClassifiedSignal":
        """Create a ClassifiedSignal from a RawSignal plus classification fields."""
        return cls(
            competitor=raw.competitor,
            source=raw.source,
            signal_type=raw.signal_type,
            payload=raw.payload,
            observed_at=raw.observed_at,
            raw_url=raw.raw_url,
            confidence=raw.confidence,
            **classification,
        )
