"""Keyword-based classifier for job postings.

classify_job(title, description) -> dict with:
  - category: "vertical_expansion" | "launch_related" | "engineering_surge" |
               "enterprise_readiness" | "generic"
  - keywords_matched: list of matched keywords
  - confidence: 0.0-1.0

Note: "engineering_surge" is detected at the batch level in jobs_collector.py,
not per individual posting. classify_job() will never return engineering_surge
on its own — that category is injected by the collector after counting.
"""

KEYWORD_SETS = {
    "vertical_expansion": [
        "private credit",
        "private equity",
        "asset management",
        "hedge fund",
        "investment bank",
        "wealth management",
    ],
    "launch_related": [
        "product marketing",
        "go-to-market",
        "launch",
        "gtm",
        "developer relations",
        "technical writer",
    ],
    "enterprise_readiness": [
        "security engineer",
        "compliance",
        "soc 2",
        "fedramp",
        "iso 27001",
    ],
}

# Confidence values per category (engineering_surge set by collector)
CONFIDENCE_MAP = {
    "vertical_expansion": 0.8,
    "launch_related": 0.7,
    "engineering_surge": 0.9,
    "enterprise_readiness": 0.6,
    "generic": 0.2,
}


def classify_job(title: str, description: str = "") -> dict:
    """Classify a single job posting by keyword matching.

    Args:
        title: The job posting title.
        description: Optional job description text.

    Returns:
        dict with keys: category, keywords_matched, confidence
    """
    combined = f"{title} {description}".lower()

    # Priority order: vertical_expansion, launch_related, enterprise_readiness
    # First category with any match wins.
    for category, keywords in KEYWORD_SETS.items():
        matched = [kw for kw in keywords if kw in combined]
        if matched:
            return {
                "category": category,
                "keywords_matched": matched,
                "confidence": CONFIDENCE_MAP[category],
            }

    return {
        "category": "generic",
        "keywords_matched": [],
        "confidence": CONFIDENCE_MAP["generic"],
    }
