"""Tests for the weekly digest generator and email formatter."""

import pytest
from datetime import datetime, timezone

from competitive.collectors.base import RawSignal
from competitive.classifier.base import ClassifiedSignal
from competitive.classifier.signal_classifier import classify
from competitive.classifier.predictive_score import score as apply_score
from competitive.digest.weekly_digest import generate_digest
from competitive.digest.email_formatter import format_email


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _classified(
    competitor: str,
    category: str,
    predictive_score: float = 0.7,
    signal_type: str = "new_url",
    source: str = "sitemap",
    raw_url: str = "https://example.com/signal",
    payload: dict = None,
    lead_time: str = "2-4 weeks",
    takeaway: str = "Watch this competitor.",
) -> ClassifiedSignal:
    return ClassifiedSignal(
        competitor=competitor,
        source=source,
        signal_type=signal_type,
        payload=payload or {"url": raw_url, "page_type": "product"},
        observed_at=datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc),
        raw_url=raw_url,
        confidence=0.5,
        category=category,
        predictive_score=predictive_score,
        lead_time_estimate=lead_time,
        tom_takeaway=takeaway,
    )


WEEK_START = "April 14, 2026"


# ---------------------------------------------------------------------------
# Digest grouping tests
# ---------------------------------------------------------------------------

class TestDigestGrouping:
    def test_groups_signals_by_competitor(self):
        """Digest should group signals under the correct competitor key."""
        signals = [
            _classified("rogo", "launch_signal", predictive_score=0.9),
            _classified("rogo", "hiring_signal", predictive_score=0.7,
                        signal_type="job_posting", source="jobs"),
            _classified("f2", "content_signal", predictive_score=0.5),
        ]
        digest = generate_digest(signals, WEEK_START)
        assert "rogo" in digest["by_competitor"]
        assert "f2" in digest["by_competitor"]
        assert len(digest["by_competitor"]["rogo"]["signals"]) == 2
        assert len(digest["by_competitor"]["f2"]["signals"]) == 1

    def test_total_counts(self):
        """total_signals, actionable_signals, noise_filtered are correct."""
        signals = [
            _classified("rogo", "launch_signal", predictive_score=0.8),
            _classified("rogo", "noise", predictive_score=0.2,
                        lead_time="", takeaway=""),
            _classified("f2", "noise", predictive_score=0.1,
                        lead_time="", takeaway=""),
        ]
        digest = generate_digest(signals, WEEK_START)
        assert digest["total_signals"] == 3
        assert digest["actionable_signals"] == 1
        assert digest["noise_filtered"] == 2

    def test_noise_excluded_from_competitor_buckets(self):
        """Noise signals should not appear in by_competitor."""
        signals = [
            _classified("rogo", "noise", predictive_score=0.1,
                        lead_time="", takeaway=""),
        ]
        digest = generate_digest(signals, WEEK_START)
        assert "rogo" not in digest["by_competitor"]

    def test_week_label_preserved(self):
        """The week label should appear verbatim in the digest."""
        digest = generate_digest([], WEEK_START)
        assert digest["week"] == WEEK_START

    def test_threat_levels_assigned(self):
        """Threat level is high when top score >= 0.7, medium >= 0.4, else low."""
        signals = [
            _classified("rogo", "launch_signal", predictive_score=0.9),
            _classified("f2", "content_signal", predictive_score=0.5),
            _classified("keye", "hiring_signal", predictive_score=0.3),
        ]
        digest = generate_digest(signals, WEEK_START)
        assert digest["by_competitor"]["rogo"]["threat"] == "high"
        assert digest["by_competitor"]["f2"]["threat"] == "medium"
        assert digest["by_competitor"]["keye"]["threat"] == "low"

    def test_signals_sorted_by_score_desc(self):
        """Signals within a competitor bucket are sorted by predictive_score descending."""
        signals = [
            _classified("rogo", "hiring_signal", predictive_score=0.6,
                        signal_type="job_posting", source="jobs",
                        raw_url="https://example.com/low"),
            _classified("rogo", "launch_signal", predictive_score=0.9,
                        raw_url="https://example.com/high"),
        ]
        digest = generate_digest(signals, WEEK_START)
        scores = [s["predictive_score"] for s in digest["by_competitor"]["rogo"]["signals"]]
        assert scores == sorted(scores, reverse=True)

    def test_empty_signals_produces_correct_structure(self):
        """Empty signals list still returns a well-formed digest."""
        digest = generate_digest([], WEEK_START)
        assert digest["total_signals"] == 0
        assert digest["actionable_signals"] == 0
        assert digest["noise_filtered"] == 0
        assert digest["by_competitor"] == {}


# ---------------------------------------------------------------------------
# Email formatter tests
# ---------------------------------------------------------------------------

class TestEmailFormatter:
    def test_produces_readable_output(self):
        """format_email returns a non-empty string with the week header."""
        signals = [
            _classified("rogo", "launch_signal", predictive_score=0.9,
                        takeaway="Rogo is shipping something big."),
            _classified("f2", "content_signal", predictive_score=0.5,
                        lead_time="immediate",
                        takeaway="Brief your AEs on F2 comparison content."),
        ]
        digest = generate_digest(signals, WEEK_START)
        email = format_email(digest)

        assert isinstance(email, str)
        assert len(email) > 0
        assert "April 14, 2026" in email
        assert "ROGO" in email.upper()
        assert "LAUNCH" in email.upper()

    def test_email_contains_takeaways(self):
        """Takeaway text should appear in the formatted email."""
        takeaway = "Rogo is preparing a portfolio analytics launch in 2-4 weeks."
        signals = [
            _classified("rogo", "launch_signal", predictive_score=0.9,
                        takeaway=takeaway),
        ]
        digest = generate_digest(signals, WEEK_START)
        email = format_email(digest)
        assert takeaway in email

    def test_email_contains_lead_time(self):
        """Lead time should appear in the formatted email."""
        signals = [
            _classified("rogo", "launch_signal", predictive_score=0.9,
                        lead_time="2-4 weeks"),
        ]
        digest = generate_digest(signals, WEEK_START)
        email = format_email(digest)
        assert "2-4 weeks" in email

    def test_empty_signals_produces_no_signals_message(self):
        """When there are no actionable signals, email says 'No signals this week'."""
        digest = generate_digest([], WEEK_START)
        email = format_email(digest)
        assert "No signals this week" in email

    def test_all_noise_produces_no_signals_message(self):
        """When all signals are noise, actionable count is 0 → 'No signals this week'."""
        signals = [
            _classified("rogo", "noise", predictive_score=0.1,
                        lead_time="", takeaway=""),
            _classified("f2", "noise", predictive_score=0.1,
                        lead_time="", takeaway=""),
        ]
        digest = generate_digest(signals, WEEK_START)
        email = format_email(digest)
        assert "No signals this week" in email

    def test_noise_filtered_count_in_footer(self):
        """Noise count should appear in the footer of the email."""
        signals = [
            _classified("rogo", "launch_signal", predictive_score=0.9),
            _classified("f2", "noise", predictive_score=0.1,
                        lead_time="", takeaway=""),
            _classified("keye", "noise", predictive_score=0.1,
                        lead_time="", takeaway=""),
        ]
        digest = generate_digest(signals, WEEK_START)
        email = format_email(digest)
        assert "2 noise signals filtered" in email

    def test_threat_label_appears_in_email(self):
        """Threat level label (HIGH THREAT / MEDIUM THREAT) appears next to competitor."""
        signals = [
            _classified("rogo", "launch_signal", predictive_score=0.9),
        ]
        digest = generate_digest(signals, WEEK_START)
        email = format_email(digest)
        assert "HIGH THREAT" in email

    def test_multiple_competitors_appear_in_order(self):
        """High-threat competitors appear before medium-threat in email."""
        signals = [
            _classified("f2", "content_signal", predictive_score=0.5),
            _classified("rogo", "launch_signal", predictive_score=0.9),
        ]
        digest = generate_digest(signals, WEEK_START)
        email = format_email(digest)
        rogo_pos = email.upper().find("ROGO")
        f2_pos = email.upper().find("F2")
        assert rogo_pos < f2_pos, "High-threat competitor should appear first"


# ---------------------------------------------------------------------------
# Integration: classify → score → digest → email
# ---------------------------------------------------------------------------

class TestEndToEndPipeline:
    def test_full_pipeline(self):
        """Smoke-test: classify raw signals → score → generate digest → format email."""
        now = datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc)

        raw_signals = [
            RawSignal(
                competitor="rogo",
                source="sitemap",
                signal_type="new_url",
                payload={"url": "https://rogo.ai/product/pe-analytics", "page_type": "product"},
                observed_at=now,
                raw_url="https://rogo.ai/product/pe-analytics",
                confidence=0.8,
            ),
            RawSignal(
                competitor="rogo",
                source="jobs",
                signal_type="job_posting",
                payload={
                    "title": "Solutions Engineer — Private Credit",
                    "job_category": "vertical_expansion",
                    "keywords": ["private credit"],
                    "url": "https://jobs.ashby.com/rogo/se-pc",
                },
                observed_at=now,
                raw_url="https://jobs.ashby.com/rogo/se-pc",
                confidence=0.6,
            ),
            RawSignal(
                competitor="f2",
                source="sitemap",
                signal_type="job_posting",
                payload={"title": "Office Manager", "job_category": "generic"},
                observed_at=now,
                raw_url=None,
                confidence=0.5,
            ),
        ]

        classified = classify(raw_signals)
        for sig in classified:
            apply_score(sig)

        digest = generate_digest(classified, WEEK_START)
        email = format_email(digest)

        # Basic assertions on the pipeline output
        assert digest["total_signals"] == 3
        assert digest["actionable_signals"] == 2
        assert digest["noise_filtered"] == 1
        assert "rogo" in digest["by_competitor"]
        assert "ROGO" in email
        assert "April 14, 2026" in email
        assert "1 noise signal filtered" in email
