"""Earnings transcript collector — Exa search + Claude Haiku extraction.

Finds the latest earnings call transcripts for publicly traded target firms
and extracts AI/technology-relevant quotes with speaker attribution. Stores
each extracted mention as a signal_type='earnings' row.

Sources (priority order): fool.com, seekingalpha.com, marketscreener.com.
For transcripts exceeding Exa's text limit, falls back to WebFetch-style
direct page retrieval.

Env: EXA_API_KEY (required), ANTHROPIC_API_KEY (required for extraction).
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

HAIKU_MODEL = "claude-haiku-4-5-20251001"
MAX_TRANSCRIPT_CHARS = 50000

EXTRACTION_PROMPT = """Extract from this earnings call transcript any mentions of:
- AI, artificial intelligence, machine learning, automation
- Technology investments, digital transformation, engineering hiring
- Data infrastructure, document processing, due diligence technology

For each mention, return a JSON array. Each element:
{{
  "speaker": "Full Name, Title",
  "context": "prepared_remarks" or "analyst_qa",
  "quote": "exact verbatim quote, max 25 words, use ... for truncation",
  "strategic_intent": "one sentence: what this signals about the firm's AI posture"
}}

Rules:
- Only extract VERBATIM quotes from the transcript. Never paraphrase.
- Max 5 mentions per transcript (pick the most strategic).
- If no AI/tech mentions found, return an empty array: []
- Return ONLY valid JSON, no markdown.

TRANSCRIPT ({ticker}, {quarter}):
{transcript}"""


def _exa_client():
    api_key = os.getenv("EXA_API_KEY")
    if not api_key:
        logger.warning("earnings: EXA_API_KEY not set")
        return None
    from exa_py import Exa
    return Exa(api_key=api_key)


def _anthropic_client():
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("earnings: ANTHROPIC_API_KEY not set")
        return None
    import anthropic
    return anthropic.Anthropic(api_key=api_key)


def _fetch_transcript(exa, ticker: str, firm_name: str,
                      sources: list[str]) -> dict | None:
    """Search Exa for the latest transcript. Returns {url, text, date} or None."""
    query = f"{firm_name} {ticker} earnings call transcript 2025 2026"

    # Try each source in priority order
    for source in sources:
        try:
            results = exa.search_and_contents(
                query, type="auto", num_results=1,
                text={"max_characters": MAX_TRANSCRIPT_CHARS},
                include_domains=[source],
            )
            if results.results:
                r = results.results[0]
                text = r.text or ""
                if len(text) < 500:
                    continue  # too short — likely paywall or stub
                return {
                    "url": r.url,
                    "text": text,
                    "date": r.published_date,
                    "source": source,
                }
        except Exception as e:
            logger.warning(f"earnings: {source} search failed for {ticker}: {e}")
            continue

    logger.info(f"earnings: no transcript found for {ticker} ({firm_name})")
    return None


def _extract_mentions(client, transcript_text: str, ticker: str,
                      quarter: str) -> list[dict]:
    """Use Claude Haiku to extract AI/tech mentions from transcript."""
    prompt = EXTRACTION_PROMPT.format(
        ticker=ticker,
        quarter=quarter,
        transcript=transcript_text[:45000],  # leave room for prompt overhead
    )
    try:
        resp = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(getattr(b, "text", "") for b in resp.content).strip()
        # Strip markdown fences
        raw = re.sub(r"^```(?:json)?\s*|```\s*$", "", raw, flags=re.M).strip()
        mentions = json.loads(raw)
        if not isinstance(mentions, list):
            return []
        return mentions
    except Exception as e:
        logger.error(f"earnings: extraction failed for {ticker}: {e}")
        return []


def _resolve_firm_id(firm_name: str, firms: list[dict]) -> int | None:
    """Match firm name to firms table. Prefers exact match, then shortest
    containing match (avoids 'KKR' resolving to 'KKR CREDIT ADVISORS')."""
    name_lower = firm_name.lower()
    candidates = []
    for f in firms:
        fn = f["name"].lower()
        if fn == name_lower:
            return f["id"]  # exact match
        if name_lower in fn or fn in name_lower:
            candidates.append(f)
    if candidates:
        # shortest name = most likely the parent entity
        candidates.sort(key=lambda f: len(f["name"]))
        return candidates[0]["id"]
    return None


def _infer_quarter(date_str: str | None) -> str:
    """Infer quarter label from transcript publish date."""
    if not date_str:
        return "recent"
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        # Earnings calls published in Jan/Feb are usually Q4 of prior year,
        # Apr/May → Q1, Jul/Aug → Q2, Oct/Nov → Q3
        month = dt.month
        year = dt.year
        if month <= 3:
            return f"Q4 {year - 1}"
        elif month <= 6:
            return f"Q1 {year}"
        elif month <= 9:
            return f"Q2 {year}"
        else:
            return f"Q3 {year}"
    except Exception:
        return "recent"


def collect(config: dict) -> list[dict]:
    """Main entry point — called by main.py pipeline."""
    exa = _exa_client()
    if exa is None:
        return []
    claude = _anthropic_client()
    if claude is None:
        return []

    earnings_cfg = config.get("earnings") or {}
    tickers = earnings_cfg.get("tickers") or {}
    sources = earnings_cfg.get("sources") or ["fool.com", "seekingalpha.com", "marketscreener.com"]

    if not tickers:
        logger.info("earnings: no tickers configured")
        return []

    # Load firm universe for ID resolution
    try:
        from database import get_db
        conn = get_db()
        firms = [dict(r) for r in conn.execute("SELECT id, name FROM firms").fetchall()]
        # Check existing earnings signals to avoid re-processing same URLs
        existing_urls = set(
            r[0] for r in conn.execute(
                "SELECT source_url FROM signals WHERE signal_type = 'earnings' AND source_url IS NOT NULL"
            ).fetchall()
        )
        conn.close()
    except Exception as e:
        logger.warning(f"earnings: could not load firms: {e}")
        firms = []
        existing_urls = set()

    signals: list[dict] = []

    for ticker, firm_name in tickers.items():
        logger.info(f"earnings: processing {ticker} ({firm_name})")

        transcript = _fetch_transcript(exa, ticker, firm_name, sources)
        if not transcript:
            continue

        if transcript["url"] in existing_urls:
            logger.info(f"earnings: {ticker} transcript already processed, skipping")
            continue

        quarter = _infer_quarter(transcript["date"])
        mentions = _extract_mentions(claude, transcript["text"], ticker, quarter)

        if not mentions:
            logger.info(f"earnings: no AI/tech mentions in {ticker} {quarter}")
            continue

        firm_id = _resolve_firm_id(firm_name, firms)
        transcript_date = transcript["date"]
        if transcript_date and "T" in transcript_date:
            transcript_date = transcript_date[:19]  # trim timezone for consistency

        for m in mentions:
            speaker = m.get("speaker", "Unknown")
            quote = m.get("quote", "")
            context = m.get("context", "")
            intent = m.get("strategic_intent", "")

            # Build signal content with full attribution
            content = (
                f'{speaker} ({context.replace("_", " ")}): "{quote}" '
                f"— {intent}"
            )

            signals.append({
                "firm_id": firm_id,
                "contact_id": None,
                "signal_type": "earnings",
                "signal_subtype": "ai_technology",
                "content": content,
                "source_url": transcript["url"],
                "author_handle": None,
                "author_name": speaker,
                "signal_date": transcript_date,
                "buying_stage": "deploying" if any(
                    kw in content.lower()
                    for kw in ["deploying", "deployed", "rolling out", "production"]
                ) else "evaluating" if any(
                    kw in content.lower()
                    for kw in ["evaluating", "piloting", "testing", "exploring"]
                ) else "exploring",
                "raw_data": json.dumps({
                    "ticker": ticker,
                    "quarter": quarter,
                    "source": transcript["source"],
                    "speaker": speaker,
                    "context": context,
                    "quote": quote,
                    "strategic_intent": intent,
                    "transcript_url": transcript["url"],
                    "transcript_date": transcript["date"],
                }),
            })

        logger.info(
            f"earnings: {ticker} {quarter} → {len(mentions)} AI/tech signals "
            f"from {transcript['source']}"
        )

    logger.info(f"earnings: total {len(signals)} signals from {len(tickers)} tickers")
    return signals
