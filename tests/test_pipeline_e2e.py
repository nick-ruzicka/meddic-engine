"""End-to-end pipeline test with synthetic signals.

Injects 5 fake signals (2 actionable, 3 noise) into the classifier
and digest formatter. Proves the full pipeline works before real data.
"""
import json
import os
import tempfile

# Temp DB before any imports
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DB_PATH"] = _tmp.name
_tmp.close()

from datetime import datetime, timezone
from competitive.collectors.base import RawSignal
from competitive.classifier.signal_classifier import classify
from competitive.classifier.predictive_score import score
from competitive.digest.weekly_digest import generate_digest
from competitive.digest.email_formatter import format_email


def _make_signal(competitor, source, signal_type, payload, raw_url=None, confidence=0.5):
    return RawSignal(
        competitor=competitor,
        source=source,
        signal_type=signal_type,
        payload=payload,
        observed_at=datetime.now(timezone.utc),
        raw_url=raw_url,
        confidence=confidence,
    )


# 5 synthetic signals: 2 real, 3 noise
SYNTHETIC_SIGNALS = [
    # 1. Harvey hiring PMM for financial services → hiring_signal
    _make_signal(
        competitor="harvey",
        source="jobs",
        signal_type="job_posting",
        payload={
            "title": "Director of Product Marketing — Financial Services",
            "location": "New York",
            "department": "Marketing",
            "url": "https://boards.greenhouse.io/harvey/123",
            "posting_date": "2026-04-15",
            "keywords": ["financial services", "go-to-market"],
            "job_category": "vertical_expansion",
        },
        raw_url="https://boards.greenhouse.io/harvey/123",
        confidence=0.8,
    ),
    # 2. AlphaSense new product page in sitemap → launch_signal
    _make_signal(
        competitor="alphasense",
        source="sitemap",
        signal_type="new_url",
        payload={
            "url": "https://www.alphasense.com/enterprise/credit-analysts",
            "page_type": "product",
            "change": "new",
            "old_hash": None,
            "new_hash": "abc123def456",
        },
        raw_url="https://www.alphasense.com/enterprise/credit-analysts",
        confidence=0.85,
    ),
    # 3. NOISE: F2 footer link change
    _make_signal(
        competitor="f2",
        source="sitemap",
        signal_type="content_change",
        payload={
            "url": "https://f2.ai/",
            "page_type": "homepage",
            "change": "modified",
            "old_hash": "aaa111",
            "new_hash": "bbb222",
        },
        raw_url="https://f2.ai/",
        confidence=0.3,
    ),
    # 4. NOISE: Keye generic recruiter posting
    _make_signal(
        competitor="keye",
        source="jobs",
        signal_type="job_posting",
        payload={
            "title": "Office Manager",
            "location": "San Francisco",
            "department": "Operations",
            "url": "https://keye.co/careers/456",
            "posting_date": "2026-04-16",
            "keywords": [],
            "job_category": "generic",
        },
        raw_url="https://keye.co/careers/456",
        confidence=0.2,
    ),
    # 5. NOISE: Blueflame industry trends blog
    _make_signal(
        competitor="blueflame",
        source="exa",
        signal_type="trending_mention",
        payload={
            "title": "AI Trends in Private Equity 2026",
            "url": "https://medium.com/blueflame-ai-trends",
            "published_at": "2026-04-10",
            "snippet": "A look at how AI is transforming PE due diligence...",
            "query_matched": "Blueflame private equity AI",
        },
        raw_url="https://medium.com/blueflame-ai-trends",
        confidence=0.3,
    ),
]


class TestClassifierE2E:
    def test_classify_separates_actionable_from_noise(self):
        classified = classify(SYNTHETIC_SIGNALS)
        for s in classified:
            score(s)

        actionable = [s for s in classified if s.category != "noise"]
        noise = [s for s in classified if s.category == "noise"]

        assert len(actionable) == 2, f"Expected 2 actionable, got {len(actionable)}: {[s.category for s in classified]}"
        assert len(noise) == 3, f"Expected 3 noise, got {len(noise)}"

    def test_harvey_classified_as_hiring(self):
        classified = classify(SYNTHETIC_SIGNALS)
        harvey = [s for s in classified if s.competitor == "harvey"][0]
        assert harvey.category == "hiring_signal"

    def test_alphasense_classified_as_launch(self):
        classified = classify(SYNTHETIC_SIGNALS)
        alpha = [s for s in classified if s.competitor == "alphasense"][0]
        assert alpha.category == "launch_signal"

    def test_noise_signals_classified_correctly(self):
        classified = classify(SYNTHETIC_SIGNALS)
        f2 = [s for s in classified if s.competitor == "f2"][0]
        keye = [s for s in classified if s.competitor == "keye"][0]
        blueflame = [s for s in classified if s.competitor == "blueflame"][0]
        assert f2.category == "noise"
        assert keye.category == "noise"
        assert blueflame.category == "noise"

    def test_predictive_scores_are_sane(self):
        classified = classify(SYNTHETIC_SIGNALS)
        for s in classified:
            score(s)

        actionable = [s for s in classified if s.category != "noise"]
        noise = [s for s in classified if s.category == "noise"]

        # Actionable should have higher scores than noise
        max_noise_score = max(s.predictive_score for s in noise)
        min_actionable_score = min(s.predictive_score for s in actionable)
        assert min_actionable_score > max_noise_score, \
            f"Actionable min {min_actionable_score} should exceed noise max {max_noise_score}"

    def test_lead_times_assigned(self):
        classified = classify(SYNTHETIC_SIGNALS)
        for s in classified:
            score(s)

        actionable = [s for s in classified if s.category != "noise"]
        for s in actionable:
            assert s.lead_time_estimate != "", f"{s.competitor} {s.category} missing lead_time"

    def test_sales_takeaways_not_empty(self):
        classified = classify(SYNTHETIC_SIGNALS)
        actionable = [s for s in classified if s.category != "noise"]
        for s in actionable:
            assert s.sales_takeaway != "", f"{s.competitor} {s.category} missing sales_takeaway"
            assert len(s.sales_takeaway) > 20, f"Takeaway too short: {s.sales_takeaway}"


class TestDigestE2E:
    def test_digest_groups_by_competitor(self):
        classified = classify(SYNTHETIC_SIGNALS)
        for s in classified:
            score(s)

        actionable = [s for s in classified if s.category != "noise"]
        digest = generate_digest(actionable, "April 14, 2026")

        assert digest["actionable_signals"] == 2
        assert "harvey" in digest["by_competitor"] or "alphasense" in digest["by_competitor"]

    def test_email_formatter_produces_readable_output(self):
        classified = classify(SYNTHETIC_SIGNALS)
        for s in classified:
            score(s)

        actionable = [s for s in classified if s.category != "noise"]
        digest = generate_digest(actionable, "April 14, 2026")
        email = format_email(digest)

        assert " COMPETITIVE SIGNALS" in email
        assert "April 14, 2026" in email
        assert "harvey" in email.lower() or "Harvey" in email
        assert "alphasense" in email.lower() or "AlphaSense" in email
        assert len(email) > 100  # not empty

    def test_empty_signals_handled(self):
        digest = generate_digest([], "April 14, 2026")
        email = format_email(digest)
        assert digest["actionable_signals"] == 0
        assert "no" in email.lower() or "0" in email

    def test_malformed_signal_doesnt_crash(self):
        """Signal with missing payload fields should not crash the pipeline."""
        broken = _make_signal(
            competitor="rogo",
            source="sitemap",
            signal_type="new_url",
            payload={},  # missing page_type, change, etc.
            confidence=0.5,
        )
        classified = classify([broken])
        for s in classified:
            score(s)
        # Should not raise
        digest = generate_digest(classified, "April 14, 2026")
        email = format_email(digest)
        assert isinstance(email, str)
