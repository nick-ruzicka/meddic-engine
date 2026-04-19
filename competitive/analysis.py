"""Claude Analysis Layer for competitive intelligence.

Provides:
- Prompt builders (pure functions, no API calls)
- JSON parsers for Claude responses
- Claude API functions for generating briefs, trajectories, and signals
"""

import json
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

import anthropic

from competitive.models import (
    save_brief,
    save_trajectory,
    save_signal,
    get_latest_brief,
    get_latest_trajectory,
    get_pages,
    get_news,
)

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

MODEL = os.getenv("COMPETITIVE_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = 2000
TEMPERATURE = 0
PAGE_CONTENT_LIMIT = 3000

BRIEF_CACHE_DAYS = 7
TRAJECTORY_CACHE_DAYS = 30

_SYSTEM_CONTEXT = """You are a competitive intelligence analyst for , an enterprise AI platform for institutional finance (PE, IB, asset management, credit funds). 's flagship product is Matrix, which processes unstructured documents (financial filings, contracts, research reports, legal documents) to answer complex multi-step questions at scale. Major customers include KKR, Blackstone, Carlyle, Centerview, and BlackRock.  differentiates through deep document reasoning, precision for high-stakes workflows, and its focus on institutional finance use cases rather than general-purpose AI."""

BRIEF_SYSTEM_PROMPT = f"""{_SYSTEM_CONTEXT}

Your task is to analyze a competitor and produce a structured competitive intelligence brief.

Respond with ONLY a JSON object (no markdown wrapping, no explanation). The JSON must have exactly these fields:
- positioning_self: string — how the competitor describes themselves
- positioning_actual: string — what they actually do / who they actually serve
- target_icp: string — their ideal customer profile
- pricing_signals: string — any pricing information or signals
- key_differentiation: string — their main differentiating claims
- weakness_vs_: string — where they are weaker than 
- strength_vs_: string — where they are stronger than 
- recent_moves: string — notable recent product/go-to-market moves
- threat_level: string — one of: low | medium | high | critical
- threat_reasoning: string — brief explanation of the threat level"""

TRAJECTORY_SYSTEM_PROMPT = f"""{_SYSTEM_CONTEXT}

Your task is to analyze a competitor's trajectory over time based on their blog posts and news articles.

Respond with ONLY a JSON object (no markdown wrapping, no explanation). The JSON must have exactly these fields:
- eras: array of objects, each with: period (string), theme (string), description (string)
- inflection_points: array of strings describing key strategic shifts
- trajectory_summary: string — overall directional narrative"""

SIGNAL_SYSTEM_PROMPT = f"""{_SYSTEM_CONTEXT}

Your task is to classify a piece of content about a competitor as a competitive signal.

Respond with ONLY a JSON object (no markdown wrapping, no explanation). The JSON must have exactly these fields:
- signal_type: one of: product-launch | customer-win | funding | positioning-shift | hiring | cosmetic | other
- relevance: one of: high | medium | low
- summary: string — 1-2 sentence summary of the signal and its competitive implications for """


# ── Prompt Builders ────────────────────────────────────────────────────────────

def build_brief_prompt(competitor_name: str, pages: list, news: list) -> str:
    """Build user message for Call A (competitive brief).

    Args:
        competitor_name: Display name of the competitor
        pages: List of dicts with url, content, page_type keys
        news: List of dicts with title, url, published_at, snippet keys

    Returns:
        Formatted user message string
    """
    lines = [f"Analyze {competitor_name} as a competitor to .\n"]

    lines.append("## Website pages\n")
    if pages:
        for page in pages:
            url = page.get("url", "")
            page_type = page.get("page_type", "")
            content = page.get("content", "") or ""
            content_truncated = content[:PAGE_CONTENT_LIMIT]
            lines.append(f"### [{page_type}] {url}")
            lines.append(content_truncated)
            lines.append("")
    else:
        lines.append("(no pages available)\n")

    lines.append("## News and announcements\n")
    if news:
        for item in news:
            title = item.get("title", "")
            url = item.get("url", "")
            published_at = item.get("published_at", "")
            snippet = item.get("snippet", "") or ""
            lines.append(f"### {title}")
            lines.append(f"URL: {url}")
            lines.append(f"Published: {published_at}")
            lines.append(snippet)
            lines.append("")
    else:
        lines.append("(no news available)\n")

    return "\n".join(lines)


def build_trajectory_prompt(competitor_name: str, pages: list, news: list) -> str:
    """Build user message for Call B (trajectory analysis).

    Only includes blog-type pages and news items. Items are sorted
    chronologically (oldest first). Items with "unknown" or missing dates
    sort first.

    Args:
        competitor_name: Display name of the competitor
        pages: List of dicts with url, content, page_type, lastmod keys
        news: List of dicts with title, url, published_at, snippet keys

    Returns:
        Formatted user message string
    """
    # Filter to blog-type pages only
    blog_pages = [p for p in pages if p.get("page_type") == "blog"]

    def date_sort_key(date_str):
        """Return sort key: unknown/empty dates sort first (smallest)."""
        if not date_str or date_str.lower() == "unknown":
            return ""
        return date_str

    # Sort pages chronologically (oldest first)
    blog_pages_sorted = sorted(blog_pages, key=lambda p: date_sort_key(p.get("lastmod", "")))

    # Sort news chronologically (oldest first)
    news_sorted = sorted(news, key=lambda n: date_sort_key(n.get("published_at", "")))

    lines = [f"Analyze the trajectory of {competitor_name} over time based on their blog posts and news.\n"]

    lines.append("## Blog posts\n")
    if blog_pages_sorted:
        for page in blog_pages_sorted:
            url = page.get("url", "")
            lastmod = page.get("lastmod", "unknown")
            content = page.get("content", "") or ""
            content_truncated = content[:PAGE_CONTENT_LIMIT]
            lines.append(f"### {url} (date: {lastmod})")
            lines.append(content_truncated)
            lines.append("")
    else:
        lines.append("(no blog posts available)\n")

    lines.append("## News and announcements\n")
    if news_sorted:
        for item in news_sorted:
            title = item.get("title", "")
            url = item.get("url", "")
            published_at = item.get("published_at", "unknown")
            snippet = item.get("snippet", "") or ""
            lines.append(f"### {title} (date: {published_at})")
            lines.append(f"URL: {url}")
            lines.append(snippet)
            lines.append("")
    else:
        lines.append("(no news available)\n")

    return "\n".join(lines)


# ── JSON Parsers ───────────────────────────────────────────────────────────────

def _extract_json_from_text(text: str) -> str:
    """Strip markdown code blocks if present and return raw JSON string."""
    text = text.strip()
    # Handle ```json...``` or ```...``` blocks
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        return match.group(1).strip()
    return text


BRIEF_REQUIRED_FIELDS = {
    "positioning_self",
    "positioning_actual",
    "target_icp",
    "pricing_signals",
    "key_differentiation",
    "weakness_vs_",
    "strength_vs_",
    "recent_moves",
    "threat_level",
    "threat_reasoning",
}

TRAJECTORY_REQUIRED_FIELDS = {
    "eras",
    "inflection_points",
    "trajectory_summary",
}


def parse_brief_json(text: str) -> dict:
    """Parse Claude response for a competitive brief.

    Handles markdown code blocks as a safety net. Validates required fields.

    Args:
        text: Raw text from Claude response

    Returns:
        Parsed dict with all required brief fields

    Raises:
        ValueError: If text cannot be parsed as JSON or required fields are missing
        json.JSONDecodeError: If extracted text is not valid JSON
    """
    raw = _extract_json_from_text(text)
    data = json.loads(raw)

    missing = BRIEF_REQUIRED_FIELDS - set(data.keys())
    if missing:
        raise ValueError(f"Brief JSON missing required fields: {sorted(missing)}")

    return data


def parse_trajectory_json(text: str) -> dict:
    """Parse Claude response for a trajectory analysis.

    Handles markdown code blocks as a safety net. Validates required fields.

    Args:
        text: Raw text from Claude response

    Returns:
        Parsed dict with all required trajectory fields

    Raises:
        ValueError: If text cannot be parsed as JSON or required fields are missing
        json.JSONDecodeError: If extracted text is not valid JSON
    """
    raw = _extract_json_from_text(text)
    data = json.loads(raw)

    missing = TRAJECTORY_REQUIRED_FIELDS - set(data.keys())
    if missing:
        raise ValueError(f"Trajectory JSON missing required fields: {sorted(missing)}")

    return data


# ── Claude API Functions ───────────────────────────────────────────────────────

def _get_client() -> anthropic.Anthropic:
    """Create and return an Anthropic client."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    return anthropic.Anthropic(api_key=api_key)


def _is_fresh(generated_at_str: Optional[str], max_age_days: int) -> bool:
    """Return True if generated_at is within max_age_days of now."""
    if not generated_at_str:
        return False
    try:
        generated_at = datetime.fromisoformat(generated_at_str)
        # Make timezone-aware if needed
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - generated_at
        return age < timedelta(days=max_age_days)
    except (ValueError, TypeError):
        return False


def generate_brief(
    competitor_slug: str,
    competitor_name: str,
    force: bool = False,
) -> Optional[dict]:
    """Generate a competitive brief for a competitor (Call A).

    Caches results for 7 days. Skips generation if a fresh brief exists
    unless force=True.

    Args:
        competitor_slug: Unique slug identifier for the competitor
        competitor_name: Display name of the competitor
        force: If True, regenerate even if a fresh brief exists

    Returns:
        Parsed brief dict, or None if generation failed
    """
    # Check cache
    if not force:
        existing = get_latest_brief(competitor_slug)
        if existing and _is_fresh(existing["generated_at"], BRIEF_CACHE_DAYS):
            logger.info(f"Skipping brief for {competitor_slug}: fresh cache found")
            return json.loads(existing["brief_json"])

    # Load data
    pages = get_pages(competitor_slug)
    news = get_news(competitor_slug)

    # Convert sqlite3.Row objects to dicts if needed
    pages = [dict(p) for p in pages]
    news = [dict(n) for n in news]

    if not pages and not news:
        logger.warning(f"No pages or news found for {competitor_slug}, skipping brief")
        return None

    # Build prompt
    user_message = build_brief_prompt(competitor_name, pages, news)

    # Call Claude
    try:
        client = _get_client()
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
            system=BRIEF_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        response_text = response.content[0].text
    except Exception as e:
        logger.error(f"Claude API error generating brief for {competitor_slug}: {e}")
        return None

    # Parse response
    try:
        brief_data = parse_brief_json(response_text)
    except (ValueError, json.JSONDecodeError) as e:
        logger.error(f"Failed to parse brief JSON for {competitor_slug}: {e}")
        return None

    # Save to DB
    try:
        save_brief(competitor_slug, brief_data, model=MODEL)
        logger.info(f"Brief saved for {competitor_slug}")
    except Exception as e:
        logger.error(f"Failed to save brief for {competitor_slug}: {e}")

    return brief_data


def generate_trajectory(
    competitor_slug: str,
    competitor_name: str,
    force: bool = False,
) -> Optional[dict]:
    """Generate a trajectory analysis for a competitor (Call B).

    Caches results for 30 days. Only uses blog pages and news.

    Args:
        competitor_slug: Unique slug identifier for the competitor
        competitor_name: Display name of the competitor
        force: If True, regenerate even if a fresh trajectory exists

    Returns:
        Parsed trajectory dict, or None if generation failed
    """
    # Check cache
    if not force:
        existing = get_latest_trajectory(competitor_slug)
        if existing and _is_fresh(existing["generated_at"], TRAJECTORY_CACHE_DAYS):
            logger.info(f"Skipping trajectory for {competitor_slug}: fresh cache found")
            return json.loads(existing["trajectory_json"])

    # Load data
    pages = get_pages(competitor_slug)
    news = get_news(competitor_slug)

    # Convert sqlite3.Row objects to dicts if needed
    pages = [dict(p) for p in pages]
    news = [dict(n) for n in news]

    # Filter to blog pages only
    blog_pages = [p for p in pages if p.get("page_type") == "blog"]

    if not blog_pages and not news:
        logger.warning(f"No blog pages or news found for {competitor_slug}, skipping trajectory")
        return None

    # Build prompt
    user_message = build_trajectory_prompt(competitor_name, pages, news)

    # Call Claude
    try:
        client = _get_client()
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
            system=TRAJECTORY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        response_text = response.content[0].text
    except Exception as e:
        logger.error(f"Claude API error generating trajectory for {competitor_slug}: {e}")
        return None

    # Parse response
    try:
        trajectory_data = parse_trajectory_json(response_text)
    except (ValueError, json.JSONDecodeError) as e:
        logger.error(f"Failed to parse trajectory JSON for {competitor_slug}: {e}")
        return None

    # Save to DB
    try:
        save_trajectory(competitor_slug, trajectory_data, model=MODEL)
        logger.info(f"Trajectory saved for {competitor_slug}")
    except Exception as e:
        logger.error(f"Failed to save trajectory for {competitor_slug}: {e}")

    return trajectory_data


def detect_signals(
    competitor_slug: str,
    competitor_name: str,
    positioning: str,
) -> int:
    """Detect competitive signals from pages/news fetched today.

    For each new item fetched today, calls Claude to classify it as a signal.

    Args:
        competitor_slug: Unique slug identifier for the competitor
        competitor_name: Display name of the competitor
        positioning: Current competitor positioning (for context)

    Returns:
        Count of signals detected and saved
    """
    today = datetime.now(timezone.utc).date().isoformat()

    pages = get_pages(competitor_slug)
    news = get_news(competitor_slug)

    pages = [dict(p) for p in pages]
    news = [dict(n) for n in news]

    # Find items fetched today
    new_items = []

    for page in pages:
        fetched_at = page.get("fetched_at", "")
        if fetched_at and fetched_at.startswith(today):
            new_items.append({
                "type": "page",
                "url": page.get("url", ""),
                "content": (page.get("content", "") or "")[:PAGE_CONTENT_LIMIT],
                "page_type": page.get("page_type", ""),
            })

    for item in news:
        fetched_at = item.get("fetched_at", "")
        if fetched_at and fetched_at.startswith(today):
            new_items.append({
                "type": "news",
                "url": item.get("url", ""),
                "title": item.get("title", ""),
                "snippet": item.get("snippet", "") or "",
                "published_at": item.get("published_at", ""),
            })

    if not new_items:
        logger.info(f"No new items today for {competitor_slug}")
        return 0

    client = _get_client()
    signal_count = 0

    for item in new_items:
        # Build content for classification
        if item["type"] == "page":
            content_text = (
                f"Competitor: {competitor_name}\n"
                f"Current positioning: {positioning}\n"
                f"Page type: {item['page_type']}\n"
                f"URL: {item['url']}\n\n"
                f"Content:\n{item['content']}"
            )
        else:
            content_text = (
                f"Competitor: {competitor_name}\n"
                f"Current positioning: {positioning}\n"
                f"News title: {item['title']}\n"
                f"URL: {item['url']}\n"
                f"Published: {item['published_at']}\n\n"
                f"Snippet:\n{item['snippet']}"
            )

        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=500,
                temperature=TEMPERATURE,
                system=SIGNAL_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content_text}],
            )
            response_text = response.content[0].text
            raw = _extract_json_from_text(response_text)
            signal_data = json.loads(raw)

            signal_type = signal_data.get("signal_type", "other")
            relevance_str = signal_data.get("relevance", "low")
            summary = signal_data.get("summary", "")

            # Convert relevance string to float
            relevance_map = {"high": 0.9, "medium": 0.6, "low": 0.3}
            relevance = relevance_map.get(relevance_str, 0.3)

            save_signal(
                competitor_slug=competitor_slug,
                signal_type=signal_type,
                summary=summary,
                relevance=relevance,
                category=signal_type,
                source_url=item["url"],
            )
            signal_count += 1
            logger.info(f"Signal saved for {competitor_slug}: {signal_type} ({relevance_str})")

        except Exception as e:
            logger.error(f"Failed to classify signal for {competitor_slug} item {item['url']}: {e}")
            continue

    return signal_count
