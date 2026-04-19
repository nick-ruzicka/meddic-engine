"""Tests for the Slack delivery layer (competitive/digest/slack_delivery.py)."""

import os
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from competitive.classifier.base import ClassifiedSignal
from competitive.digest.weekly_digest import generate_digest
from competitive.digest.slack_delivery import (
    format_digest_blocks,
    format_alert_blocks,
    send_to_slack,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _classified(
    competitor: str = "rogo",
    category: str = "launch_signal",
    predictive_score: float = 0.9,
    signal_type: str = "new_url",
    source: str = "sitemap",
    raw_url: str = "https://rogo.ai/enterprise/credit-analysts",
    lead_time: str = "2-4 weeks",
    takeaway: str = "Rogo is preparing a credit analyst product.",
    payload: dict = None,
) -> ClassifiedSignal:
    return ClassifiedSignal(
        competitor=competitor,
        source=source,
        signal_type=signal_type,
        payload=payload or {"url": raw_url, "page_type": "product"},
        observed_at=datetime(2026, 4, 14, 9, 0, 0, tzinfo=timezone.utc),
        raw_url=raw_url,
        confidence=0.8,
        category=category,
        predictive_score=predictive_score,
        lead_time_estimate=lead_time,
        tom_takeaway=takeaway,
    )


WEEK_START = "April 14, 2026"


def _make_digest(signals=None):
    if signals is None:
        signals = [
            _classified("rogo", "launch_signal", 0.92),
            _classified("alphasense", "hiring_signal", 0.75,
                        raw_url="https://alphasense.com/jobs/senior-engineer",
                        takeaway="AlphaSense is scaling engineering headcount."),
        ]
    return generate_digest(signals, WEEK_START)


# ---------------------------------------------------------------------------
# format_digest_blocks tests
# ---------------------------------------------------------------------------

class TestFormatDigestBlocksStructure:
    def test_returns_list_of_dicts(self):
        """Result is a list and every item is a dict with a 'type' key."""
        digest = _make_digest()
        blocks = format_digest_blocks(digest)
        assert isinstance(blocks, list)
        assert len(blocks) > 0
        for block in blocks:
            assert isinstance(block, dict)
            assert "type" in block

    def test_has_header_block(self):
        """First block must be a header containing the week date."""
        digest = _make_digest()
        blocks = format_digest_blocks(digest)
        header = blocks[0]
        assert header["type"] == "header"
        assert "April 14, 2026" in header["text"]["text"]

    def test_has_divider(self):
        """At least one divider block separates sections."""
        digest = _make_digest()
        blocks = format_digest_blocks(digest)
        types = [b["type"] for b in blocks]
        assert "divider" in types

    def test_has_actions_block(self):
        """There is an actions block with a 'Run Collect Now' button."""
        digest = _make_digest()
        blocks = format_digest_blocks(digest)
        action_blocks = [b for b in blocks if b["type"] == "actions"]
        assert len(action_blocks) >= 1
        button_texts = [
            el["text"]["text"]
            for ab in action_blocks
            for el in ab.get("elements", [])
            if el.get("type") == "button"
        ]
        assert "Run Collect Now" in button_texts

    def test_has_context_footer(self):
        """Last context block mentions Signal Engine v2."""
        digest = _make_digest()
        blocks = format_digest_blocks(digest)
        context_blocks = [b for b in blocks if b["type"] == "context"]
        assert len(context_blocks) >= 1
        footer_text = context_blocks[-1]["elements"][0]["text"]
        assert "Signal Engine v2" in footer_text

    def test_summary_contains_actionable_count(self):
        """Summary section mentions the count of actionable signals."""
        digest = _make_digest()
        blocks = format_digest_blocks(digest)
        # The summary section follows the header
        section_blocks = [b for b in blocks if b["type"] == "section"]
        assert len(section_blocks) > 0
        summary_text = section_blocks[0]["text"]["text"]
        # Should mention "leading indicator"
        assert "leading indicator" in summary_text


class TestFormatDigestBlocksIncludesCompetitor:
    def test_competitor_name_appears_in_blocks(self):
        """A competitor's name (uppercased) appears somewhere in the block text."""
        digest = _make_digest()
        blocks = format_digest_blocks(digest)
        all_text = " ".join(
            b["text"]["text"]
            for b in blocks
            if b["type"] == "section" and "text" in b
        )
        assert "ROGO" in all_text

    def test_multiple_competitors_all_present(self):
        """All competitors with signals appear in block text."""
        digest = _make_digest()
        blocks = format_digest_blocks(digest)
        all_text = " ".join(
            b["text"]["text"]
            for b in blocks
            if b["type"] == "section" and "text" in b
        )
        assert "ROGO" in all_text
        assert "ALPHASENSE" in all_text

    def test_signal_score_appears(self):
        """The predictive score value appears in a signal section block."""
        digest = _make_digest()
        blocks = format_digest_blocks(digest)
        all_text = " ".join(
            b["text"]["text"]
            for b in blocks
            if b["type"] == "section" and "text" in b
        )
        assert "0.92" in all_text

    def test_category_emoji_present(self):
        """The launch_signal emoji appears for launch signals."""
        digest = _make_digest()
        blocks = format_digest_blocks(digest)
        all_text = " ".join(
            b["text"]["text"]
            for b in blocks
            if b["type"] == "section" and "text" in b
        )
        assert ":red_circle:" in all_text


# ---------------------------------------------------------------------------
# format_alert_blocks tests
# ---------------------------------------------------------------------------

class TestFormatAlertBlocksStructure:
    def test_returns_list_of_dicts(self):
        """Result is a list of dicts with 'type' keys."""
        signal = _classified(predictive_score=0.93)
        blocks = format_alert_blocks(signal)
        assert isinstance(blocks, list)
        assert all(isinstance(b, dict) and "type" in b for b in blocks)

    def test_has_header_block(self):
        """First block is a header indicating a high-confidence signal."""
        signal = _classified(predictive_score=0.93)
        blocks = format_alert_blocks(signal)
        assert blocks[0]["type"] == "header"
        assert "High-Confidence Signal" in blocks[0]["text"]["text"]

    def test_has_section_block(self):
        """There is at least one section block with mrkdwn text."""
        signal = _classified(predictive_score=0.93)
        blocks = format_alert_blocks(signal)
        sections = [b for b in blocks if b["type"] == "section"]
        assert len(sections) >= 1
        assert sections[0]["text"]["type"] == "mrkdwn"

    def test_has_actions_block(self):
        """An actions block with 'View Source' and 'Run Collect Now' buttons."""
        signal = _classified(predictive_score=0.93)
        blocks = format_alert_blocks(signal)
        action_blocks = [b for b in blocks if b["type"] == "actions"]
        assert len(action_blocks) >= 1
        button_texts = [
            el["text"]["text"]
            for ab in action_blocks
            for el in ab.get("elements", [])
            if el.get("type") == "button"
        ]
        assert "View Source" in button_texts
        assert "Run Collect Now" in button_texts

    def test_has_context_block(self):
        """A context block mentions Signal Engine v2."""
        signal = _classified(predictive_score=0.93)
        blocks = format_alert_blocks(signal)
        context_blocks = [b for b in blocks if b["type"] == "context"]
        assert len(context_blocks) >= 1
        assert "Signal Engine v2" in context_blocks[-1]["elements"][0]["text"]


class TestFormatAlertBlocksIncludesScore:
    def test_score_appears_in_section(self):
        """The predictive score appears in the section text."""
        signal = _classified(predictive_score=0.93)
        blocks = format_alert_blocks(signal)
        sections = [b for b in blocks if b["type"] == "section"]
        all_text = " ".join(s["text"]["text"] for s in sections)
        assert "0.93" in all_text

    def test_competitor_in_section(self):
        """The competitor name appears in the alert section text."""
        signal = _classified(competitor="rogo", predictive_score=0.91)
        blocks = format_alert_blocks(signal)
        sections = [b for b in blocks if b["type"] == "section"]
        all_text = " ".join(s["text"]["text"] for s in sections)
        assert "rogo" in all_text

    def test_takeaway_in_section(self):
        """The tom_takeaway appears (bolded) in the section text."""
        signal = _classified(takeaway="Rogo is preparing a credit analyst product.")
        blocks = format_alert_blocks(signal)
        sections = [b for b in blocks if b["type"] == "section"]
        all_text = " ".join(s["text"]["text"] for s in sections)
        assert "Rogo is preparing a credit analyst product." in all_text

    def test_lead_time_in_section(self):
        """The lead time appears in the section text."""
        signal = _classified(lead_time="2-4 weeks")
        blocks = format_alert_blocks(signal)
        sections = [b for b in blocks if b["type"] == "section"]
        all_text = " ".join(s["text"]["text"] for s in sections)
        assert "2-4 weeks" in all_text

    def test_source_url_in_section(self):
        """The raw_url appears in the section text as a Slack link."""
        signal = _classified(raw_url="https://rogo.ai/enterprise/credit-analysts")
        blocks = format_alert_blocks(signal)
        sections = [b for b in blocks if b["type"] == "section"]
        all_text = " ".join(s["text"]["text"] for s in sections)
        assert "rogo.ai" in all_text


# ---------------------------------------------------------------------------
# send_to_slack tests
# ---------------------------------------------------------------------------

class TestSendToSlackNoWebhook:
    def test_returns_false_when_webhook_not_set(self):
        """When SLACK_WEBHOOK_URL is empty/unset, send_to_slack returns False."""
        with patch.dict(os.environ, {}, clear=False):
            import competitive.digest.slack_delivery as mod
            original = mod.SLACK_WEBHOOK_URL
            mod.SLACK_WEBHOOK_URL = ""
            try:
                result = send_to_slack([{"type": "section", "text": {"type": "mrkdwn", "text": "test"}}])
                assert result is False
            finally:
                mod.SLACK_WEBHOOK_URL = original

    def test_no_http_call_when_webhook_not_set(self):
        """When SLACK_WEBHOOK_URL is empty, requests.post is never called."""
        with patch("competitive.digest.slack_delivery.requests.post") as mock_post:
            import competitive.digest.slack_delivery as mod
            original = mod.SLACK_WEBHOOK_URL
            mod.SLACK_WEBHOOK_URL = ""
            try:
                send_to_slack([])
                mock_post.assert_not_called()
            finally:
                mod.SLACK_WEBHOOK_URL = original


class TestSendToSlackSuccess:
    def test_returns_true_on_200(self):
        """When requests.post returns 200, send_to_slack returns True."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("competitive.digest.slack_delivery.requests.post", return_value=mock_resp):
            import competitive.digest.slack_delivery as mod
            original = mod.SLACK_WEBHOOK_URL
            mod.SLACK_WEBHOOK_URL = "https://hooks.slack.com/fake"
            try:
                result = send_to_slack([{"type": "section"}])
                assert result is True
            finally:
                mod.SLACK_WEBHOOK_URL = original

    def test_posts_to_correct_url(self):
        """requests.post is called with the configured webhook URL."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        webhook = "https://hooks.slack.com/services/T000/B000/fake"
        with patch("competitive.digest.slack_delivery.requests.post", return_value=mock_resp) as mock_post:
            import competitive.digest.slack_delivery as mod
            original = mod.SLACK_WEBHOOK_URL
            mod.SLACK_WEBHOOK_URL = webhook
            try:
                send_to_slack([{"type": "divider"}])
                call_args = mock_post.call_args
                assert call_args[0][0] == webhook
            finally:
                mod.SLACK_WEBHOOK_URL = original


class TestSendToSlackFailure:
    def test_returns_false_on_500(self):
        """When requests.post returns 500, send_to_slack returns False."""
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        with patch("competitive.digest.slack_delivery.requests.post", return_value=mock_resp):
            import competitive.digest.slack_delivery as mod
            original = mod.SLACK_WEBHOOK_URL
            mod.SLACK_WEBHOOK_URL = "https://hooks.slack.com/fake"
            try:
                result = send_to_slack([{"type": "section"}])
                assert result is False
            finally:
                mod.SLACK_WEBHOOK_URL = original

    def test_returns_false_on_4xx(self):
        """When requests.post returns 400, send_to_slack returns False."""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Bad Request"
        with patch("competitive.digest.slack_delivery.requests.post", return_value=mock_resp):
            import competitive.digest.slack_delivery as mod
            original = mod.SLACK_WEBHOOK_URL
            mod.SLACK_WEBHOOK_URL = "https://hooks.slack.com/fake"
            try:
                result = send_to_slack([{"type": "section"}])
                assert result is False
            finally:
                mod.SLACK_WEBHOOK_URL = original


# ---------------------------------------------------------------------------
# Empty digest edge case
# ---------------------------------------------------------------------------

class TestEmptyDigestHandled:
    def test_empty_digest_produces_valid_blocks(self):
        """An empty digest (no signals) still produces a valid block list."""
        digest = generate_digest([], WEEK_START)
        blocks = format_digest_blocks(digest)
        assert isinstance(blocks, list)
        assert len(blocks) > 0
        for block in blocks:
            assert isinstance(block, dict)
            assert "type" in block

    def test_empty_digest_has_no_signals_message(self):
        """Empty digest blocks contain a 'No signals' message."""
        digest = generate_digest([], WEEK_START)
        blocks = format_digest_blocks(digest)
        all_text = " ".join(
            b["text"]["text"]
            for b in blocks
            if b["type"] == "section" and "text" in b
        )
        assert "No signals" in all_text

    def test_empty_digest_still_has_header_and_footer(self):
        """Even with no signals, header and actions/context footer are present."""
        digest = generate_digest([], WEEK_START)
        blocks = format_digest_blocks(digest)
        types = [b["type"] for b in blocks]
        assert "header" in types
        assert "actions" in types
        assert "context" in types

    def test_all_noise_digest_shows_no_signals(self):
        """A digest where every signal is noise still shows 'No signals'."""
        signals = [
            _classified("rogo", "noise", 0.1, lead_time="", takeaway=""),
            _classified("f2", "noise", 0.1, lead_time="", takeaway=""),
        ]
        digest = generate_digest(signals, WEEK_START)
        blocks = format_digest_blocks(digest)
        all_text = " ".join(
            b["text"]["text"]
            for b in blocks
            if b["type"] == "section" and "text" in b
        )
        assert "No signals" in all_text
