"""enrichment/exa_enricher.py

Discover senior named contacts at ICP firms by scraping their public
team/leadership pages, then pass the HTML/text to Claude Haiku for
name+title extraction. Optionally hit a direct URL for firms whose
team page we already know.

Public API:
    discover_team_members(domain, firm_hint="") -> list[dict]
    discover_from_url(url)                       -> list[dict]
    enrich_firm(conn, firm_id, source="exa_team_page") -> int
    enrich_missing_firms(conn, firm_names=None)  -> dict

Each discovered contact is:
    {"name": str, "title": str, "source": str}

Env: EXA_API_KEY, ANTHROPIC_API_KEY
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

HAIKU_MODEL = "claude-haiku-4-5-20251001"
HTTP_TIMEOUT = 20
UA = "Mozilla/5.0 (MEDDIC Engine research@.com)"

_EXTRACT_PROMPT = (
    "You are extracting senior leadership from a company team/leadership page. "
    "From the text below, return a JSON array of {\"name\": ..., \"title\": ...} "
    "for every named individual whose title indicates senior leadership "
    "(CTO, CIO, CEO, CFO, President, Managing Director, Partner, Principal, "
    "Head of, Chief, Founder, General Counsel, Vice Chairman, Senior Advisor). "
    "Skip junior roles, associates, analysts, assistants, and non-people items. "
    "Return ONLY the JSON array. No markdown, no prose, no code fences. "
    "If no one matches, return []."
)


# ─────────────────────────────────────────────────────────────────────────────
# Claude extraction
# ─────────────────────────────────────────────────────────────────────────────

def _claude_extract(text: str) -> list[dict]:
    """Run Haiku on a chunk of page text; return normalized {name,title} list."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key or not text.strip():
        return []
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic SDK not installed")
        return []

    # Cap tokens — team pages can be long. Claude Haiku handles ~100k easily,
    # but cost scales; 20k chars is plenty for most team pages.
    snippet = text[:20_000]

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=2000,
            temperature=0,
            system=_EXTRACT_PROMPT,
            messages=[{"role": "user", "content": snippet}],
        )
        raw = "".join(getattr(b, "text", "") for b in resp.content).strip()
    except Exception as e:
        logger.warning(f"claude extract failed: {e}")
        return []

    # Strip any code fence Claude might add despite instructions
    raw = re.sub(r"^```(?:json)?\s*|```\s*$", "", raw, flags=re.M).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Try to salvage — find the first [ ... ] substring
        m = re.search(r"\[[\s\S]*\]", raw)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []

    if not isinstance(data, list):
        return []

    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        title = (item.get("title") or "").strip()
        if name and len(name) > 3 and len(name) < 80:
            out.append({"name": name, "title": title})
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Exa search → team page discovery
# ─────────────────────────────────────────────────────────────────────────────

def discover_team_members(domain: str, firm_hint: str = "") -> list[dict]:
    """Use Exa to find team/leadership pages on a domain, extract named seniors."""
    key = os.getenv("EXA_API_KEY")
    if not key or not domain:
        logger.warning("EXA_API_KEY not set or no domain — discover returning []")
        return []
    try:
        from exa_py import Exa
    except ImportError:
        logger.warning("exa-py not installed — discover returning []")
        return []

    exa = Exa(api_key=key)
    query = (
        f"{firm_hint} team leadership people professionals site:{domain}"
        if firm_hint else
        f"team OR leadership OR professionals OR people site:{domain}"
    )
    try:
        res = exa.search_and_contents(query, num_results=3, text=True)
    except Exception as e:
        logger.warning(f"exa search failed for {domain}: {e}")
        return []

    all_candidates: list[dict] = []
    for r in (getattr(res, "results", None) or []):
        text = getattr(r, "text", "") or ""
        if not text:
            continue
        logger.info(f"exa: extracting from {getattr(r, 'url', '?')[:60]} ({len(text)} chars)")
        # LinkedIn URLs might appear in the raw text Exa returned
        li_urls = _extract_linkedin_urls(text)
        members = _claude_extract(text)
        for m in members:
            m["source"] = "exa_team_page"
            m["source_url"] = getattr(r, "url", "") or ""
            li = _match_linkedin(m["name"], li_urls)
            if li:
                m["linkedin_url"] = li
        all_candidates.extend(members)

    return _dedupe_by_name(all_candidates)


def discover_from_url(url: str, source: str = "team_page_direct") -> list[dict]:
    """Fetch a specific team-page URL directly; extract named seniors.
    Uses BeautifulSoup to strip markup before handing to Claude."""
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=HTTP_TIMEOUT)
    except requests.RequestException as e:
        logger.warning(f"direct fetch failed {url}: {e}")
        return []
    if r.status_code != 200:
        logger.warning(f"direct fetch {url} → {r.status_code}")
        return []

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "html.parser")
        for t in soup(["script", "style", "noscript"]):
            t.decompose()
        text = soup.get_text("\n", strip=True)
    except ImportError:
        text = re.sub(r"<[^>]+>", " ", r.text)

    # Pull LinkedIn URLs from the raw HTML *before* we strip markup
    li_urls = _extract_linkedin_urls(r.text)

    members = _claude_extract(text)
    for m in members:
        m["source"] = source
        m["source_url"] = url
        li = _match_linkedin(m["name"], li_urls)
        if li:
            m["linkedin_url"] = li
    return _dedupe_by_name(members)



# ─────────────────────────────────────────────────────────────────────────────
# LinkedIn URL extraction — scan raw HTML for linkedin.com/in/<slug>, match
# candidates to slugs by first+last name token overlap.
# ─────────────────────────────────────────────────────────────────────────────

_LINKEDIN_RE = re.compile(
    r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9\-\._~%]+/?",
    re.IGNORECASE,
)

def _extract_linkedin_urls(html: str) -> list[str]:
    """All LinkedIn profile URLs present in the HTML, deduped, trailing slash stripped."""
    if not html:
        return []
    seen: set[str] = set()
    urls: list[str] = []
    for m in _LINKEDIN_RE.finditer(html):
        u = m.group(0).rstrip("/").split("?")[0]
        key = u.lower()
        if key in seen:
            continue
        seen.add(key)
        urls.append(u)
    return urls


def _name_tokens(name: str) -> list[str]:
    """Lowercase alpha tokens ≥3 chars from a name; 'Brian P. Maury' → ['brian','maury']."""
    return [t for t in re.findall(r"[A-Za-z]{3,}", name or "") if t.lower() not in ("van","von","del","the","dr","mr","mrs","ms")]


def _match_linkedin(name: str, urls: list[str]) -> Optional[str]:
    """Pick the LinkedIn URL whose slug best overlaps with the person's name tokens.
    Requires at least 2 name tokens present to claim a match (avoids false positives)."""
    toks = [t.lower() for t in _name_tokens(name)]
    if len(toks) < 2:
        return None
    best = None
    best_score = 0
    for u in urls:
        slug = u.lower().split("/in/")[-1].rstrip("/").replace("-", " ").replace("_", " ")
        # Score = number of name tokens found in slug
        score = sum(1 for t in toks if t in slug)
        if score >= 2 and score > best_score:
            best = u
            best_score = score
    return best


def _dedupe_by_name(candidates: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for c in candidates:
        key = c["name"].lower().strip()
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# DB wiring
# ─────────────────────────────────────────────────────────────────────────────

_STOP_NAMES = {"our team", "the team", "leadership", "contact", "home"}


def _name_ok(n: str) -> bool:
    """Filter junk names like 'Our Team' or single-word strings."""
    if not n or n.lower() in _STOP_NAMES:
        return False
    tokens = [t for t in n.split() if t.isalpha()]
    return len(tokens) >= 2


def enrich_firm(conn, firm_id: int, candidates: Optional[list[dict]] = None,
                source_label: str = "exa_team_page") -> dict:
    """Insert new contacts for a firm from discovered candidates, run Hunter."""
    firm = conn.execute("SELECT id, name, domain FROM firms WHERE id = ?",
                        (firm_id,)).fetchone()
    if not firm:
        return {"firm_id": firm_id, "added": 0, "emails_found": 0, "candidates": 0}

    if candidates is None:
        candidates = discover_team_members(firm["domain"], firm_hint=firm["name"])

    added = emails_found = 0
    from enrichment.hunter_enricher import find_contact

    for c in candidates:
        if not _name_ok(c["name"]):
            continue
        # Skip if we already have this contact
        existing = conn.execute(
            "SELECT id FROM contacts WHERE firm_id = ? AND LOWER(name) = LOWER(?)",
            (firm_id, c["name"]),
        ).fetchone()
        if existing:
            continue

        # Try Hunter via waterfall
        email_result = find_contact(c["name"], firm["domain"] or "")
        email = email_result.get("email") or ""
        verified = 1 if (email_result.get("verified")
                         or (email and email_result.get("score", 0) >= 70)) else 0
        if verified:
            emails_found += 1

        conn.execute(
            """INSERT INTO contacts
               (firm_id, name, title, email, email_verified, email_source,
                linkedin_url, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (firm_id, c["name"], c.get("title", ""),
             email or None, verified,
             c.get("source", source_label) if verified else source_label,
             c.get("linkedin_url") or None,
             f"Discovered via {c.get('source', source_label)} "
             f"({c.get('source_url','')})"),
        )
        added += 1

    conn.commit()
    return {
        "firm_id": firm_id, "firm_name": firm["name"],
        "candidates": len(candidates),
        "added": added, "emails_found": emails_found,
    }


def enrich_missing_firms(conn, firm_names: Optional[list[str]] = None) -> dict:
    """Run Exa + Hunter on firms that have no verified-email contacts."""
    if firm_names:
        placeholders = ",".join("?" * len(firm_names))
        rows = conn.execute(
            f"SELECT id, name, domain FROM firms WHERE name IN ({placeholders})",
            firm_names,
        ).fetchall()
    else:
        rows = conn.execute("""
            SELECT f.id, f.name, f.domain
              FROM firms f
              LEFT JOIN contacts c ON c.firm_id = f.id AND c.email_verified = 1
             WHERE f.domain IS NOT NULL AND f.domain != ''
             GROUP BY f.id
            HAVING COUNT(c.id) < 2
             ORDER BY COUNT(c.id) ASC
        """).fetchall()

    summary = {"firms_processed": 0, "contacts_added": 0, "emails_found": 0,
               "per_firm": []}
    for firm in rows:
        print(f"→ {firm['name']} ({firm['domain']})")
        result = enrich_firm(conn, firm["id"])
        summary["firms_processed"] += 1
        summary["contacts_added"] += result["added"]
        summary["emails_found"]   += result["emails_found"]
        summary["per_firm"].append(result)
        print(f"  {result['candidates']} candidates → "
              f"{result['added']} added → {result['emails_found']} with email")
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--firm", nargs="*", help="firm names to target")
    ap.add_argument("--url", help="direct team-page URL")
    ap.add_argument("--firm-id", type=int)
    args = ap.parse_args(argv)

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, ROOT)
    from database import get_db
    conn = get_db()
    try:
        if args.url and args.firm_id:
            cands = discover_from_url(args.url)
            print(f"url returned {len(cands)} candidates")
            print(enrich_firm(conn, args.firm_id, cands, source_label="team_page_direct"))
        else:
            print(enrich_missing_firms(conn, args.firm))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
