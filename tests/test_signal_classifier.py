"""Tests for the rule-based signal classifier and predictive scorer."""

import pytest
from datetime import datetime, timezone

from competitive.collectors.base import RawSignal
from competitive.classifier.signal_classifier import classify
from competitive.classifier.predictive_score import score


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _raw(signal_type: str, payload: dict, confidence: float = 0.5,
         source: str = "sitemap") -> RawSignal:
    return RawSignal(
        competitor="rogo",
        source=source,
        signal_type=signal_type,
        payload=payload,
        observed_at=datetime.now(timezone.utc),
        raw_url=payload.get("url"),
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Classification tests
# ---------------------------------------------------------------------------

class TestLaunchSignal:
    def test_new_product_url(self):
        """new_url with page_type=product → launch_signal."""
        signal = _raw(
            "new_url",
            {"url": "https://rogo.ai/product/portfolio-analytics", "page_type": "product"},
        )
        [classified] = classify([signal])
        assert classified.category == "launch_signal"

    def test_new_platform_url(self):
        """new_url with page_type=platform → launch_signal."""
        signal = _raw(
            "new_url",
            {"url": "https://rogo.ai/platform/data", "page_type": "platform"},
        )
        [classified] = classify([signal])
        assert classified.category == "launch_signal"

    def test_commit_burst_is_launch(self):
        """commit_burst signals always map to launch_signal."""
        signal = _raw(
            "commit_burst",
            {"org": "rogo", "repo": "portfolio-engine", "count": 12, "period": "7d"},
            source="github",
        )
        [classified] = classify([signal])
        assert classified.category == "launch_signal"

    def test_trending_mention_announces(self):
        """trending_mention with 'announces' in snippet → launch_signal."""
        signal = _raw(
            "trending_mention",
            {
                "title": "Rogo announces new PE module",
                "snippet": "Rogo announces a major new product today.",
                "url": "https://techcrunch.com/rogo-announces",
            },
            source="exa",
        )
        [classified] = classify([signal])
        assert classified.category == "launch_signal"

    def test_app_subdomain_is_launch(self):
        """new_subdomain starting with 'app' → launch_signal."""
        signal = _raw(
            "new_subdomain",
            {"subdomain": "app.rogo.ai", "record_type": "A"},
            source="dns",
        )
        [classified] = classify([signal])
        assert classified.category == "launch_signal"

    def test_beta_subdomain_is_launch(self):
        """new_subdomain starting with 'beta' → launch_signal."""
        signal = _raw(
            "new_subdomain",
            {"subdomain": "beta.rogo.ai", "record_type": "CNAME"},
            source="dns",
        )
        [classified] = classify([signal])
        assert classified.category == "launch_signal"


class TestHiringSignal:
    def test_vertical_expansion_job(self):
        """job_posting with job_category=vertical_expansion → hiring_signal."""
        signal = _raw(
            "job_posting",
            {
                "title": "Solutions Engineer — Private Credit",
                "job_category": "vertical_expansion",
                "keywords": ["private credit", "enterprise"],
                "url": "https://jobs.ashby.com/rogo/se-pc",
            },
            source="jobs",
        )
        [classified] = classify([signal])
        assert classified.category == "hiring_signal"

    def test_launch_related_job(self):
        """job_posting with job_category=launch_related → hiring_signal."""
        signal = _raw(
            "job_posting",
            {
                "title": "Product Marketing Manager",
                "job_category": "launch_related",
                "keywords": ["go-to-market", "launch"],
                "url": "https://jobs.ashby.com/rogo/pmm",
            },
            source="jobs",
        )
        [classified] = classify([signal])
        assert classified.category == "hiring_signal"

    def test_inferred_vertical_expansion(self):
        """job_posting without explicit job_category but with vertical keywords → hiring_signal."""
        signal = _raw(
            "job_posting",
            {
                "title": "Enterprise AE — Financial Services",
                "keywords": ["financial services", "quota-carrying"],
                "url": "https://jobs.ashby.com/rogo/ae-fs",
            },
            source="jobs",
        )
        [classified] = classify([signal])
        assert classified.category == "hiring_signal"


class TestContentSignal:
    def test_comparison_blog_url(self):
        """new_url with blog page_type and 'vs' in URL → content_signal."""
        signal = _raw(
            "new_url",
            {
                "url": "https://rogo.ai/blog/rogo-vs-platform",
                "page_type": "blog",
            },
        )
        [classified] = classify([signal])
        assert classified.category == "content_signal"

    def test_alternative_blog_url(self):
        """new_url with blog page_type and 'alternative' in URL → content_signal."""
        signal = _raw(
            "new_url",
            {
                "url": "https://rogo.ai/blog/enterprise-ai-alternative",
                "page_type": "blog",
            },
        )
        [classified] = classify([signal])
        assert classified.category == "content_signal"

    def test_trending_mention_comparison(self):
        """trending_mention with comparison keyword → content_signal."""
        signal = _raw(
            "trending_mention",
            {
                "title": "Best Rogo alternatives for private equity",
                "snippet": "Looking for a Rogo alternative? Here are our picks.",
                "url": "https://g2.com/rogo-alternatives",
            },
            source="exa",
        )
        [classified] = classify([signal])
        assert classified.category == "content_signal"


class TestNoiseClassification:
    def test_generic_job_posting(self):
        """job_posting with job_category=generic → noise."""
        signal = _raw(
            "job_posting",
            {
                "title": "Office Manager",
                "job_category": "generic",
                "keywords": ["scheduling", "office"],
                "url": "https://jobs.ashby.com/rogo/om",
            },
            source="jobs",
        )
        [classified] = classify([signal])
        assert classified.category == "noise"

    def test_generic_blog_no_keywords(self):
        """new_url blog with no comparison keywords → noise."""
        signal = _raw(
            "new_url",
            {
                "url": "https://rogo.ai/blog/trends-in-private-equity-2026",
                "page_type": "blog",
            },
        )
        [classified] = classify([signal])
        assert classified.category == "noise"

    def test_star_spike_is_noise(self):
        """star_spike → noise."""
        signal = _raw(
            "star_spike",
            {"org": "rogo", "repo": "some-lib", "count": 50, "period": "7d"},
            source="github",
        )
        [classified] = classify([signal])
        assert classified.category == "noise"

    def test_trending_mention_no_keywords(self):
        """trending_mention with no launch or comparison keywords → noise."""
        signal = _raw(
            "trending_mention",
            {
                "title": "Rogo quarterly recap",
                "snippet": "General industry discussion about AI in finance.",
                "url": "https://medium.com/rogo-recap",
            },
            source="exa",
        )
        [classified] = classify([signal])
        assert classified.category == "noise"


# ---------------------------------------------------------------------------
# Predictive scoring tests
# ---------------------------------------------------------------------------

class TestPredictiveScoring:
    def test_launch_signal_boosted(self):
        """launch_signal gets +0.2 boost on top of base confidence."""
        signal = _raw(
            "new_url",
            {"url": "https://rogo.ai/product/x", "page_type": "product"},
            confidence=0.5,
        )
        [classified] = classify([signal])
        result = score(classified)
        assert result == pytest.approx(0.7)  # 0.5 + 0.2

    def test_noise_penalized(self):
        """noise gets -0.3 penalty."""
        signal = _raw(
            "job_posting",
            {"title": "Recruiter", "job_category": "generic"},
            confidence=0.5,
            source="jobs",
        )
        [classified] = classify([signal])
        result = score(classified)
        assert result == pytest.approx(0.2)  # 0.5 - 0.3

    def test_hiring_signal_boost(self):
        """hiring_signal gets +0.1 boost."""
        signal = _raw(
            "job_posting",
            {
                "title": "Solutions Engineer — Private Credit",
                "job_category": "vertical_expansion",
                "keywords": ["private credit"],
            },
            confidence=0.5,
            source="jobs",
        )
        [classified] = classify([signal])
        result = score(classified)
        assert result == pytest.approx(0.6)  # 0.5 + 0.1

    def test_infrastructure_signal_boost(self):
        """infrastructure_signal gets +0.1 boost."""
        signal = _raw(
            "new_subdomain",
            {"subdomain": "api.rogo.ai", "record_type": "A"},
            confidence=0.5,
            source="dns",
        )
        [classified] = classify([signal])
        result = score(classified)
        assert result == pytest.approx(0.6)  # 0.5 + 0.1

    def test_score_capped_at_1(self):
        """Predictive score never exceeds 1.0."""
        signal = _raw(
            "new_url",
            {"url": "https://rogo.ai/product/x", "page_type": "product"},
            confidence=0.9,
        )
        [classified] = classify([signal])
        result = score(classified)
        assert result == pytest.approx(1.0)

    def test_score_floored_at_0(self):
        """Predictive score never goes below 0.0."""
        signal = _raw(
            "job_posting",
            {"title": "Janitor", "job_category": "generic"},
            confidence=0.1,
            source="jobs",
        )
        [classified] = classify([signal])
        result = score(classified)
        assert result == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Lead time tests
# ---------------------------------------------------------------------------

class TestLeadTimeAssignment:
    def test_launch_signal_sitemap(self):
        """launch_signal from sitemap → 2-4 weeks."""
        signal = _raw(
            "new_url",
            {"url": "https://rogo.ai/product/x", "page_type": "product"},
            source="sitemap",
        )
        [classified] = classify([signal])
        score(classified)
        assert classified.lead_time_estimate == "2-4 weeks"

    def test_launch_signal_github(self):
        """launch_signal from github → 4-8 weeks."""
        signal = _raw(
            "commit_burst",
            {"org": "rogo", "repo": "new-repo", "count": 10, "period": "7d"},
            source="github",
        )
        [classified] = classify([signal])
        score(classified)
        assert classified.lead_time_estimate == "4-8 weeks"

    def test_hiring_signal_lead_time(self):
        """hiring_signal → 60-90 days."""
        signal = _raw(
            "job_posting",
            {
                "title": "Solutions Engineer",
                "job_category": "vertical_expansion",
                "keywords": ["private credit"],
            },
            source="jobs",
        )
        [classified] = classify([signal])
        score(classified)
        assert classified.lead_time_estimate == "60-90 days"

    def test_content_signal_lead_time(self):
        """content_signal → immediate."""
        signal = _raw(
            "new_url",
            {"url": "https://rogo.ai/blog/rogo-vs-", "page_type": "blog"},
            source="sitemap",
        )
        [classified] = classify([signal])
        score(classified)
        assert classified.lead_time_estimate == "immediate"

    def test_infrastructure_signal_lead_time(self):
        """infrastructure_signal → 2-4 weeks."""
        signal = _raw(
            "new_subdomain",
            {"subdomain": "docs.rogo.ai", "record_type": "A"},
            source="dns",
        )
        [classified] = classify([signal])
        score(classified)
        assert classified.lead_time_estimate == "2-4 weeks"

    def test_noise_lead_time_empty(self):
        """noise → empty lead_time_estimate."""
        signal = _raw(
            "job_posting",
            {"title": "Office Manager", "job_category": "generic"},
            source="jobs",
        )
        [classified] = classify([signal])
        score(classified)
        assert classified.lead_time_estimate == ""
