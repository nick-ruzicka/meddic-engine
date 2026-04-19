"""Rule-based signal classifier.

Takes a list of RawSignals and applies deterministic rules (matching
signal_type + payload fields against the taxonomy in SPEC.md) to
produce ClassifiedSignals with a category and a sales-angle takeaway.
"""

import re
from datetime import datetime, timezone

from competitive.collectors.base import RawSignal
from competitive.classifier.base import ClassifiedSignal

# ---------------------------------------------------------------------------
# Keyword helpers
# ---------------------------------------------------------------------------

_LAUNCH_KEYWORDS = {"launch", "launches", "launched", "announces", "announced",
                    "now available", "introducing", "releases", "released"}

_COMPARISON_KEYWORDS = {"vs", "comparison", "alternative", "alternatives",
                        "compare", "migrate", "migration"}

_LAUNCH_RELATED_JOB_KEYWORDS = {"go-to-market", "gtm", "launch", "announcement",
                                  "product marketing"}

_VERTICAL_EXPANSION_KEYWORDS = {
    "private credit", "private equity", "hedge fund", "asset management",
    "financial services", "investment banking", "enterprise", "wealth management",
}


def _text_contains_any(text: str, keywords: set[str]) -> bool:
    """Return True if any keyword appears in text (case-insensitive)."""
    lower = text.lower()
    return any(kw in lower for kw in keywords)


def _url_contains_comparison(url: str) -> bool:
    parts = url.lower().split("/")
    return any(_text_contains_any(p, _COMPARISON_KEYWORDS) for p in parts)


# ---------------------------------------------------------------------------
# Takeaway templates
# ---------------------------------------------------------------------------

def _make_takeaway(category: str, signal: RawSignal) -> str:
    competitor = signal.payload.get("competitor_name") or signal.competitor
    payload = signal.payload

    if category == "launch_signal":
        page_type = payload.get("page_type", "")
        subdomain = payload.get("subdomain", "")
        repo = payload.get("repo", "")
        snippet = payload.get("snippet") or payload.get("title") or ""

        if page_type:
            product_type = page_type
        elif subdomain:
            product_type = subdomain
        elif repo:
            product_type = repo
        else:
            product_type = "new product"

        lead_time = "2-4 weeks"
        if signal.source == "github":
            lead_time = "4-8 weeks"

        return (
            f"{competitor} appears to be preparing a {product_type} launch. "
            f"Watch for announcement in {lead_time}."
        )

    if category == "hiring_signal":
        title = payload.get("title", "engineers")
        keywords = payload.get("keywords") or []
        verticals = [k for k in keywords if _text_contains_any(k, _VERTICAL_EXPANSION_KEYWORDS)]
        if verticals:
            what_it_means = f"expansion into {verticals[0]}"
        else:
            what_it_means = "strategic growth"
        return (
            f"{competitor} is hiring {title} — "
            f"signals {what_it_means} in 60-90 days."
        )

    if category == "infrastructure_signal":
        subdomain = payload.get("subdomain", "new infrastructure")
        repo = payload.get("repo", "")
        what = subdomain or repo or "new developer infrastructure"
        return (
            f"{competitor} stood up {what} — "
            f"likely building API/platform capabilities for 2-4 weeks out."
        )

    if category == "content_signal":
        title = payload.get("title") or payload.get("url") or "comparison content"
        return (
            f"{competitor} published {title}. "
            f"Brief your AEs on rebuttal points."
        )

    # noise
    return ""


# ---------------------------------------------------------------------------
# Main classifier
# ---------------------------------------------------------------------------

def classify(signals: list[RawSignal]) -> list[ClassifiedSignal]:
    """Classify a list of RawSignals into ClassifiedSignals.

    Applies deterministic rules based on signal_type and payload contents.
    Does not call any LLM.
    """
    results: list[ClassifiedSignal] = []

    for signal in signals:
        category = _categorize(signal)
        takeaway = _make_takeaway(category, signal) if category != "noise" else ""

        classified = ClassifiedSignal.from_raw(
            signal,
            category=category,
            tom_takeaway=takeaway,
            # predictive_score and lead_time_estimate filled by predictive_score.py
            predictive_score=0.0,
            lead_time_estimate="",
        )
        results.append(classified)

    return results


def _categorize(signal: RawSignal) -> str:
    """Return the taxonomy category string for this signal."""
    stype = signal.signal_type
    payload = signal.payload

    # ------------------------------------------------------------------
    # new_url signals (from sitemap)
    # ------------------------------------------------------------------
    if stype == "new_url":
        page_type = (payload.get("page_type") or "").lower()
        url = (payload.get("url") or signal.raw_url or "").lower()

        if page_type in ("product", "platform"):
            return "launch_signal"

        if page_type == "blog":
            if _url_contains_comparison(url) or _text_contains_any(
                url, _COMPARISON_KEYWORDS
            ):
                return "content_signal"

        # Case study URLs
        if "case-study" in url or "case_study" in url or "customer" in url:
            return "content_signal"

        return "noise"

    # ------------------------------------------------------------------
    # job_posting signals (from jobs collector)
    # ------------------------------------------------------------------
    if stype == "job_posting":
        job_category = (payload.get("job_category") or "").lower()
        title = (payload.get("title") or "").lower()
        keywords = [k.lower() for k in (payload.get("keywords") or [])]
        combined = " ".join([title] + keywords)

        if job_category == "vertical_expansion":
            return "hiring_signal"

        if job_category == "launch_related":
            return "hiring_signal"

        if job_category == "generic":
            return "noise"

        # Infer from title/keywords when job_category is not set
        if _text_contains_any(combined, _LAUNCH_RELATED_JOB_KEYWORDS):
            return "hiring_signal"

        if _text_contains_any(combined, _VERTICAL_EXPANSION_KEYWORDS):
            return "hiring_signal"

        if any(term in title for term in (
            "developer relations", "devrel", "technical writer",
            "security engineer", "compliance engineer",
            "head of ", "solutions engineer",
        )):
            return "hiring_signal"

        # Generic / recruiter / office-manager type roles
        return "noise"

    # ------------------------------------------------------------------
    # new_subdomain signals (from DNS collector)
    # ------------------------------------------------------------------
    if stype == "new_subdomain":
        subdomain_full = (payload.get("subdomain") or "").lower()
        # Extract leftmost label, e.g. "docs" from "docs.rogo.ai"
        subdomain_label = subdomain_full.split(".")[0] if subdomain_full else ""

        if subdomain_label in ("docs", "api", "sdk", "developer", "dev", "status"):
            return "infrastructure_signal"

        if subdomain_label in ("app", "beta", "v2", "staging", "preview",
                                "product", "platform", "launch", "new"):
            return "launch_signal"

        return "noise"

    # ------------------------------------------------------------------
    # GitHub signals
    # ------------------------------------------------------------------
    if stype == "commit_burst":
        return "launch_signal"

    if stype == "new_repo":
        repo = (payload.get("repo") or "").lower()
        if any(term in repo for term in ("sdk", "api", "client", "lib")):
            return "infrastructure_signal"
        return "launch_signal"

    if stype == "star_spike":
        return "noise"

    # ------------------------------------------------------------------
    # Exa trending_mention signals
    # ------------------------------------------------------------------
    if stype == "trending_mention":
        snippet = (payload.get("snippet") or "").lower()
        title = (payload.get("title") or "").lower()
        combined = snippet + " " + title

        if _text_contains_any(combined, _LAUNCH_KEYWORDS):
            return "launch_signal"

        if _text_contains_any(combined, _COMPARISON_KEYWORDS):
            return "content_signal"

        return "noise"

    # ------------------------------------------------------------------
    # Everything else
    # ------------------------------------------------------------------
    return "noise"
