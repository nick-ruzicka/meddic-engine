"""Weekly digest generator.

Aggregates classified signals for a given week, groups them by
competitor, and computes a threat level per competitor.
"""

from competitive.classifier.base import ClassifiedSignal

# ---------------------------------------------------------------------------
# Threat level thresholds (based on highest predictive_score in competitor bucket)
# ---------------------------------------------------------------------------

_THREAT_HIGH = 0.7
_THREAT_MEDIUM = 0.4


def _threat_level(signals: list[ClassifiedSignal]) -> str:
    if not signals:
        return "low"
    top_score = max(s.predictive_score for s in signals)
    if top_score >= _THREAT_HIGH:
        return "high"
    if top_score >= _THREAT_MEDIUM:
        return "medium"
    return "low"


def _signal_to_dict(s: ClassifiedSignal) -> dict:
    return {
        "category": s.category,
        "signal_type": s.signal_type,
        "source": s.source,
        "predictive_score": s.predictive_score,
        "lead_time_estimate": s.lead_time_estimate,
        "sales_takeaway": s.sales_takeaway,
        "raw_url": s.raw_url,
        "payload": s.payload,
        "observed_at": s.observed_at.isoformat() if hasattr(s.observed_at, "isoformat") else str(s.observed_at),
    }


def generate_digest(signals: list[ClassifiedSignal], week_start: str) -> dict:
    """Generate a structured weekly digest dict from classified signals.

    Args:
        signals:    All ClassifiedSignals for the week (already scored).
        week_start: ISO date string or human-readable label, e.g. "April 14, 2026".

    Returns:
        Structured dict with totals and per-competitor groupings.
    """
    actionable = [s for s in signals if s.category != "noise"]
    noise = [s for s in signals if s.category == "noise"]

    # Group actionable signals by competitor, sorted by score desc
    by_competitor: dict[str, dict] = {}
    for s in sorted(actionable, key=lambda x: x.predictive_score, reverse=True):
        comp = s.competitor
        if comp not in by_competitor:
            by_competitor[comp] = {"threat": "", "signals": []}
        by_competitor[comp]["signals"].append(_signal_to_dict(s))

    # Assign threat level per competitor
    comp_signal_map: dict[str, list[ClassifiedSignal]] = {}
    for s in actionable:
        comp_signal_map.setdefault(s.competitor, []).append(s)

    for comp, bucket in by_competitor.items():
        bucket["threat"] = _threat_level(comp_signal_map.get(comp, []))

    # Sort competitors by threat level (high > medium > low)
    _order = {"high": 0, "medium": 1, "low": 2}
    by_competitor = dict(
        sorted(by_competitor.items(), key=lambda kv: _order.get(kv[1]["threat"], 3))
    )

    return {
        "week": week_start,
        "total_signals": len(signals),
        "actionable_signals": len(actionable),
        "noise_filtered": len(noise),
        "by_competitor": by_competitor,
    }
