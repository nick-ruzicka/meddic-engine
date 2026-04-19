"""Slack delivery layer for the  Competitive Signal Engine v2.

Two delivery paths:
1. Monday 9am digest — all actionable signals from the past 7 days.
2. Real-time alerts — immediate post for any signal scoring >0.85.

Both paths share the same SLACK_WEBHOOK_URL.
"""

import logging
import os
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

import requests

from competitive.classifier.base import ClassifiedSignal

logger = logging.getLogger(__name__)

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
SLACK_RUN_TRIGGER_URL = os.getenv("SLACK_RUN_TRIGGER_URL", "")

_CATEGORY_EMOJI = {
    "launch_signal": ":red_circle:",
    "hiring_signal": ":large_orange_circle:",
    "infrastructure_signal": ":large_blue_circle:",
    "content_signal": ":white_circle:",
}

_CATEGORY_LABEL = {
    "launch_signal": "LAUNCH",
    "hiring_signal": "HIRING",
    "infrastructure_signal": "INFRA",
    "content_signal": "CONTENT",
}

_THREAT_LABEL = {
    "high": "HIGH THREAT",
    "medium": "MEDIUM THREAT",
    "low": "LOW THREAT",
}


def _domain(url: str) -> str:
    """Extract the netloc from a URL for display."""
    if not url:
        return ""
    return urlparse(url).netloc or url


def format_digest_blocks(digest: dict) -> list[dict]:
    """Format the weekly digest dict as Slack Block Kit blocks.

    Args:
        digest: Output of weekly_digest.generate_digest().

    Returns:
        List of Slack Block Kit block dicts.
    """
    week = digest.get("week", "")
    actionable = digest.get("actionable_signals", 0)
    noise_count = digest.get("noise_filtered", 0)
    by_competitor = digest.get("by_competitor") or {}

    blocks: list[dict] = []

    # Header
    blocks.append({
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": f" Competitive Signals \u2014 Week of {week}",
        },
    })

    if actionable == 0:
        # No signals this week
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*No signals this week.*\n{noise_count} noise signals filtered",
            },
        })
    else:
        # Summary
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{actionable} leading indicator{'s' if actionable != 1 else ''}*"
                    f" across {len(by_competitor)} competitor{'s' if len(by_competitor) != 1 else ''}\n"
                    f"{noise_count} noise signal{'s' if noise_count != 1 else ''} filtered"
                ),
            },
        })

        blocks.append({"type": "divider"})

        # Per-competitor sections
        for competitor_slug, bucket in by_competitor.items():
            threat = bucket.get("threat", "low")
            threat_label = _THREAT_LABEL.get(threat, threat.upper())
            comp_signals = bucket.get("signals") or []

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{competitor_slug.upper()}* \u2014 {threat_label}",
                },
            })

            for sig in comp_signals:
                category = sig.get("category", "")
                emoji = _CATEGORY_EMOJI.get(category, ":white_circle:")
                label = _CATEGORY_LABEL.get(category, category.upper())

                payload = sig.get("payload") or {}
                url = (
                    sig.get("raw_url")
                    or payload.get("url")
                    or payload.get("title")
                    or sig.get("signal_type", "")
                )
                score = sig.get("predictive_score", 0.0)
                lead_time = sig.get("lead_time_estimate") or "unknown"
                takeaway = sig.get("tom_takeaway") or ""
                source_domain = _domain(url) if url and url.startswith("http") else ""

                # Build the signal text
                display_url = url or "(no url)"
                signal_lines = [
                    f"{emoji} *[{label}]* {display_url}",
                    f">Score: `{score:.2f}` \u00b7 Lead time: {lead_time}",
                ]
                if takeaway:
                    signal_lines.append(f">\u2192 {takeaway}")
                if url and url.startswith("http"):
                    signal_lines.append(f">Source: <{url}|{source_domain}>")

                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "\n".join(signal_lines),
                    },
                })

            blocks.append({"type": "divider"})

    # Footer with "Run now" button
    action_elements = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "Run Collect Now"},
            "style": "primary",
        }
    ]
    if SLACK_RUN_TRIGGER_URL:
        action_elements[0]["url"] = SLACK_RUN_TRIGGER_URL

    blocks.append({"type": "actions", "elements": action_elements})

    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": "MEDDIC Engine v2 \u00b7 Monitoring 6 competitors \u00b7 5 signal sources",
            }
        ],
    })

    return blocks


def format_alert_blocks(signal: ClassifiedSignal) -> list[dict]:
    """Format a single high-score signal as an immediate alert.

    Args:
        signal: A ClassifiedSignal with predictive_score > 0.85.

    Returns:
        List of Slack Block Kit block dicts.
    """
    category = signal.category or ""
    emoji = _CATEGORY_EMOJI.get(category, ":white_circle:")
    label = _CATEGORY_LABEL.get(category, category.upper())
    competitor = signal.competitor or ""
    takeaway = signal.tom_takeaway or ""
    score = signal.predictive_score
    lead_time = signal.lead_time_estimate or "unknown"
    raw_url = signal.raw_url or ""
    source_domain = _domain(raw_url) if raw_url else ""

    # Compute time_ago from observed_at
    now = datetime.now(timezone.utc)
    try:
        observed = signal.observed_at
        if hasattr(observed, "tzinfo") and observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        delta = now - observed
        total_seconds = int(delta.total_seconds())
        if total_seconds < 60:
            time_ago = "just now"
        elif total_seconds < 3600:
            mins = total_seconds // 60
            time_ago = f"{mins} minute{'s' if mins != 1 else ''} ago"
        elif total_seconds < 86400:
            hrs = total_seconds // 3600
            time_ago = f"{hrs} hour{'s' if hrs != 1 else ''} ago"
        else:
            days = total_seconds // 86400
            time_ago = f"{days} day{'s' if days != 1 else ''} ago"
    except Exception:
        time_ago = "recently"

    section_text_lines = [
        f"{emoji} *[{label}]* {competitor}",
        f"*{takeaway}*" if takeaway else "",
        "",
        f">Score: `{score:.2f}` \u00b7 Lead time: {lead_time}",
    ]
    if raw_url:
        section_text_lines.append(f">Source: <{raw_url}|{source_domain}>")

    section_text = "\n".join(line for line in section_text_lines if line != "").strip()

    action_elements = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "View Source"},
        },
    ]
    if raw_url:
        action_elements[0]["url"] = raw_url

    run_button: dict = {
        "type": "button",
        "text": {"type": "plain_text", "text": "Run Collect Now"},
        "style": "primary",
    }
    if SLACK_RUN_TRIGGER_URL:
        run_button["url"] = SLACK_RUN_TRIGGER_URL
    action_elements.append(run_button)

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "High-Confidence Signal Detected"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": section_text},
        },
        {
            "type": "actions",
            "elements": action_elements,
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Detected {time_ago} \u00b7 MEDDIC Engine v2",
                }
            ],
        },
    ]

    return blocks


def send_to_slack(blocks: list[dict]) -> bool:
    """POST blocks to SLACK_WEBHOOK_URL.

    Args:
        blocks: List of Slack Block Kit block dicts.

    Returns:
        True on success, False otherwise.
    """
    if not SLACK_WEBHOOK_URL:
        logger.warning("SLACK_WEBHOOK_URL not set \u2014 skipping delivery")
        return False
    try:
        resp = requests.post(SLACK_WEBHOOK_URL, json={"blocks": blocks}, timeout=10)
    except requests.RequestException as exc:
        logger.error("Slack delivery failed with exception: %s", exc)
        return False
    if resp.status_code == 200:
        logger.info("Slack message sent successfully")
        return True
    logger.error("Slack delivery failed: %d %s", resp.status_code, resp.text)
    return False


def send_digest(digest: dict) -> bool:
    """Format digest as Block Kit blocks and send to Slack.

    Args:
        digest: Output of weekly_digest.generate_digest().

    Returns:
        True on success, False otherwise.
    """
    blocks = format_digest_blocks(digest)
    return send_to_slack(blocks)


def send_alert(signal: ClassifiedSignal) -> bool:
    """Format a single high-score signal as an alert and send to Slack.

    Args:
        signal: A ClassifiedSignal (typically with predictive_score > 0.85).

    Returns:
        True on success, False otherwise.
    """
    blocks = format_alert_blocks(signal)
    return send_to_slack(blocks)
