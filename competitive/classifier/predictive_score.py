"""Predictive scoring for classified signals.

Applies a formulaic score on top of the base confidence and assigns
a lead_time_estimate based on signal category and source.
"""

from competitive.classifier.base import ClassifiedSignal

# ---------------------------------------------------------------------------
# Scoring constants
# ---------------------------------------------------------------------------

_BOOST_LAUNCH = 0.2
_BOOST_HIRING = 0.1
_BOOST_INFRA = 0.1
_PENALTY_NOISE = 0.3


# ---------------------------------------------------------------------------
# Lead time lookup
# ---------------------------------------------------------------------------

def _lead_time(signal: ClassifiedSignal) -> str:
    category = signal.category
    source = signal.source

    if category == "launch_signal":
        if source == "github":
            return "4-8 weeks"
        return "2-4 weeks"

    if category == "hiring_signal":
        return "60-90 days"

    if category == "infrastructure_signal":
        return "2-4 weeks"

    if category == "content_signal":
        return "immediate"

    # noise or unknown
    return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score(signal: ClassifiedSignal) -> float:
    """Compute and return the predictive score for a classified signal.

    Also mutates signal.lead_time_estimate as a side effect so callers
    don't need to call _lead_time separately.

    Score formula:
        base  = signal.confidence
        +0.2  if launch_signal
        +0.1  if hiring_signal or infrastructure_signal
        -0.3  if noise
        capped to [0.0, 1.0]
    """
    base = signal.confidence
    category = signal.category

    if category == "launch_signal":
        base += _BOOST_LAUNCH
    elif category in ("hiring_signal", "infrastructure_signal"):
        base += _BOOST_HIRING
    elif category == "noise":
        base -= _PENALTY_NOISE

    final = max(0.0, min(1.0, base))

    # Side-effect: populate lead_time on the signal object too
    signal.lead_time_estimate = _lead_time(signal)
    signal.predictive_score = final

    return final
