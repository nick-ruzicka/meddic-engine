"""Email formatter for the weekly competitive digest.

Produces plain-text email output matching the format described in SPEC.md.
"""

from typing import Any

_CATEGORY_LABEL = {
    "launch_signal": "LAUNCH",
    "hiring_signal": "HIRING",
    "infrastructure_signal": "INFRA",
    "content_signal": "CONTENT",
    "noise": "NOISE",
}

_THREAT_LABEL = {
    "high": "HIGH THREAT",
    "medium": "MEDIUM THREAT",
    "low": "LOW THREAT",
}


def _format_signal_block(sig: dict[str, Any]) -> str:
    """Format a single signal as an indented block."""
    label = _CATEGORY_LABEL.get(sig.get("category", ""), "SIGNAL")
    payload = sig.get("payload") or {}
    url = (
        payload.get("url")
        or sig.get("raw_url")
        or payload.get("title")
        or sig.get("signal_type", "")
    )
    lead_time = sig.get("lead_time_estimate") or "unknown"
    takeaway = sig.get("sales_takeaway") or ""
    source_info = sig.get("source", "")
    observed = sig.get("observed_at", "")
    if observed and len(observed) >= 10:
        # "2026-04-14T..." → "Apr 14"
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(observed.replace("Z", "+00:00"))
            source_info += f" ({dt.strftime('%b %-d')})"
        except (ValueError, TypeError):
            pass

    lines = [
        f"  [{label}] {url}",
        f"           Lead time: {lead_time}",
    ]
    if takeaway:
        # Wrap takeaway at ~70 chars with indentation
        wrapped = _wrap_text(takeaway, width=70, indent="             ")
        lines.append(f"           → {wrapped}")
    if source_info:
        lines.append(f"           Source: {source_info}")

    return "\n".join(lines)


def _wrap_text(text: str, width: int = 70, indent: str = "") -> str:
    """Simple word-wrap that indents continuation lines."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = (current + " " + word).strip()
    if current:
        lines.append(current)
    return ("\n" + indent).join(lines)


def format_email(digest: dict) -> str:
    """Return plain-text email matching the SPEC.md Digest Format.

    Args:
        digest: Output of weekly_digest.generate_digest().

    Returns:
        Multi-line plain-text string suitable for sending as an email body.
    """
    week = digest.get("week", "")
    total = digest.get("total_signals", 0)
    actionable = digest.get("actionable_signals", 0)
    noise_filtered = digest.get("noise_filtered", 0)
    by_competitor = digest.get("by_competitor") or {}

    if actionable == 0:
        return (
            f" COMPETITIVE SIGNALS — Week of {week}\n"
            "No signals this week.\n"
        )

    lines = [
        f" COMPETITIVE SIGNALS — Week of {week}",
        f"{actionable} leading indicator{'s' if actionable != 1 else ''} across {len(by_competitor)} competitor{'s' if len(by_competitor) != 1 else ''}",
        "",
        "---",
        "",
    ]

    for competitor_slug, bucket in by_competitor.items():
        threat = bucket.get("threat", "low")
        threat_label = _THREAT_LABEL.get(threat, threat.upper())
        comp_signals = bucket.get("signals") or []

        # Use slug as display name (could be enriched later)
        display_name = competitor_slug.upper()
        lines.append(f"{display_name} [{threat_label}]")

        for sig in comp_signals:
            lines.append(_format_signal_block(sig))
            lines.append("")

        lines.append("")

    lines.append("---")
    if noise_filtered:
        lines.append(
            f"{noise_filtered} noise signal{'s' if noise_filtered != 1 else ''} filtered. Full log: data/meddic.db"
        )

    return "\n".join(lines)
