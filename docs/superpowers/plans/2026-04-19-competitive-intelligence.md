# Competitive Intelligence Tab — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Competitive Intelligence tab to the MEDDIC Engine with three views — Briefs, Trajectories, and Live Signals — backed by a standalone ingestion + Claude analysis pipeline.

**Architecture:** Standalone Python pipeline (`competitive_intel.py`) ingests competitor websites via sitemap + homepage crawl + Exa web search, stores raw content in SQLite, then calls Claude to produce structured briefs and trajectory timelines. A separate JSON generator (`update_competitive.py`) runs on the existing 10-min cron. The UI is a single dark-theme HTML page with three sub-tabs, matching existing dashboard patterns.

**Tech Stack:** Python 3.9, SQLite (WAL), `requests` for HTTP, `exa-py` for web search, `anthropic` SDK for Claude calls, vanilla HTML/CSS/JS for the dashboard page.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `competitive/models.py` | DB schema (CREATE TABLE), connection helpers, insert/query functions |
| `competitive/ingestion.py` | Sitemap parsing, page fetching, Exa news search, rate limiting |
| `competitive/analysis.py` | Claude Call A (brief) and Call B (trajectory), signal detection |
| `competitive/competitors.py` | Competitor list constant, URL verification |
| `competitive_intel.py` (root) | CLI entrypoint — orchestrates ingest → analyze → detect signals |
| `scripts/update_competitive.py` | JSON generator — reads DB, writes `export/competitive_data.json` |
| `scripts/run_competitive.sh` | Shell wrapper for daily cron |
| `dashboard/routes.py` | Add `/competitive` route + whitelist `competitive_data.json` |
| `export/competitive.html` | UI page with three sub-tabs |
| `export/index.html` + 3 others | Add nav link to competitive tab |
| `tests/test_competitive.py` | Unit tests for models, ingestion parsing, analysis JSON parsing |

---

### Task 1: Database Schema — `competitive/models.py`

**Files:**
- Create: `competitive/__init__.py`
- Create: `competitive/models.py`
- Test: `tests/test_competitive.py`

- [ ] **Step 1: Create the competitive package**

```bash
mkdir -p competitive
touch competitive/__init__.py
```

- [ ] **Step 2: Write failing test for schema creation**

```python
# tests/test_competitive.py
"""Tests for the competitive intelligence module."""
import os
import sqlite3
import tempfile
import pytest

# Point DB at temp file before importing
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DB_PATH"] = _tmp.name
_tmp.close()

from competitive.models import init_competitive_db, get_db, upsert_competitor, get_competitor, get_all_competitors


class TestSchema:
    def setup_method(self):
        """Fresh DB for each test."""
        conn = get_db()
        for table in ("competitor_signals", "competitor_trajectories",
                      "competitor_briefs", "competitor_news",
                      "competitor_pages", "competitors"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.commit()
        conn.close()

    def test_init_creates_tables(self):
        init_competitive_db()
        conn = get_db()
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert "competitors" in tables
        assert "competitor_pages" in tables
        assert "competitor_news" in tables
        assert "competitor_briefs" in tables
        assert "competitor_trajectories" in tables
        assert "competitor_signals" in tables

    def test_init_is_idempotent(self):
        init_competitive_db()
        init_competitive_db()  # should not raise

    def test_upsert_competitor(self):
        init_competitive_db()
        upsert_competitor("f2", "F2.ai", "https://f2.ai", 1,
                          "Deterministic spreadsheet computation")
        row = get_competitor("f2")
        assert row is not None
        assert row["name"] == "F2.ai"
        assert row["tier"] == 1

    def test_get_all_competitors(self):
        init_competitive_db()
        upsert_competitor("f2", "F2.ai", "https://f2.ai", 1, "test")
        upsert_competitor("rogo", "Rogo", "https://rogo.ai", 1, "test2")
        all_c = get_all_competitors()
        assert len(all_c) == 2
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /Users/nicholasruzicka/Desktop/meddic-engine && python -m pytest tests/test_competitive.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'competitive.models'`

- [ ] **Step 4: Implement `competitive/models.py`**

```python
# competitive/models.py
"""Database schema and helpers for competitive intelligence tables."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from database import get_db  # reuse project's connection factory


def init_competitive_db():
    """Create competitive intelligence tables. Safe to call on every startup."""
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS competitors (
            slug        TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            url         TEXT NOT NULL,
            tier        INTEGER DEFAULT 1,
            positioning TEXT,
            url_ok      INTEGER DEFAULT 1,
            last_ingested TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS competitor_pages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor_slug TEXT NOT NULL REFERENCES competitors(slug),
            url             TEXT NOT NULL,
            page_type       TEXT,
            lastmod         TEXT,
            content         TEXT,
            fetched_at      TEXT DEFAULT (datetime('now')),
            UNIQUE(competitor_slug, url)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS competitor_news (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor_slug TEXT NOT NULL REFERENCES competitors(slug),
            title           TEXT,
            url             TEXT,
            source          TEXT,
            published_at    TEXT,
            snippet         TEXT,
            fetched_at      TEXT DEFAULT (datetime('now')),
            UNIQUE(competitor_slug, url)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS competitor_briefs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor_slug TEXT NOT NULL REFERENCES competitors(slug),
            brief_json      TEXT NOT NULL,
            generated_at    TEXT DEFAULT (datetime('now')),
            model           TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS competitor_trajectories (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor_slug TEXT NOT NULL REFERENCES competitors(slug),
            trajectory_json TEXT NOT NULL,
            generated_at    TEXT DEFAULT (datetime('now')),
            model           TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS competitor_signals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor_slug TEXT NOT NULL REFERENCES competitors(slug),
            signal_type     TEXT,
            summary         TEXT,
            relevance       TEXT,
            category        TEXT,
            source_url      TEXT,
            detected_at     TEXT DEFAULT (datetime('now'))
        )
    """)

    c.execute("CREATE INDEX IF NOT EXISTS idx_comp_pages_slug ON competitor_pages(competitor_slug)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_comp_news_slug ON competitor_news(competitor_slug)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_comp_briefs_slug ON competitor_briefs(competitor_slug)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_comp_signals_slug ON competitor_signals(competitor_slug)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_comp_signals_detected ON competitor_signals(detected_at)")

    conn.commit()
    conn.close()


def upsert_competitor(slug, name, url, tier, positioning):
    conn = get_db()
    conn.execute("""
        INSERT INTO competitors (slug, name, url, tier, positioning)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(slug) DO UPDATE SET
            name=excluded.name, url=excluded.url,
            tier=excluded.tier, positioning=excluded.positioning
    """, (slug, name, url, tier, positioning))
    conn.commit()
    conn.close()


def get_competitor(slug):
    conn = get_db()
    row = conn.execute("SELECT * FROM competitors WHERE slug=?", (slug,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_competitors(tier=None):
    conn = get_db()
    if tier:
        rows = conn.execute("SELECT * FROM competitors WHERE tier=? ORDER BY slug",
                            (tier,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM competitors ORDER BY slug").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_page(competitor_slug, url, content, page_type=None, lastmod=None):
    conn = get_db()
    conn.execute("""
        INSERT INTO competitor_pages (competitor_slug, url, content, page_type, lastmod)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(competitor_slug, url) DO UPDATE SET
            content=excluded.content, page_type=excluded.page_type,
            lastmod=excluded.lastmod, fetched_at=datetime('now')
    """, (competitor_slug, url, content, page_type, lastmod))
    conn.commit()
    conn.close()


def save_news(competitor_slug, title, url, source, published_at, snippet):
    conn = get_db()
    conn.execute("""
        INSERT INTO competitor_news (competitor_slug, title, url, source, published_at, snippet)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(competitor_slug, url) DO UPDATE SET
            title=excluded.title, snippet=excluded.snippet,
            published_at=excluded.published_at, fetched_at=datetime('now')
    """, (competitor_slug, title, url, source, published_at, snippet))
    conn.commit()
    conn.close()


def save_brief(competitor_slug, brief_json, model):
    conn = get_db()
    conn.execute("""
        INSERT INTO competitor_briefs (competitor_slug, brief_json, model)
        VALUES (?, ?, ?)
    """, (competitor_slug, brief_json, model))
    conn.commit()
    conn.close()


def save_trajectory(competitor_slug, trajectory_json, model):
    conn = get_db()
    conn.execute("""
        INSERT INTO competitor_trajectories (competitor_slug, trajectory_json, model)
        VALUES (?, ?, ?)
    """, (competitor_slug, trajectory_json, model))
    conn.commit()
    conn.close()


def get_latest_brief(competitor_slug):
    conn = get_db()
    row = conn.execute("""
        SELECT * FROM competitor_briefs
        WHERE competitor_slug=? ORDER BY generated_at DESC LIMIT 1
    """, (competitor_slug,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_latest_trajectory(competitor_slug):
    conn = get_db()
    row = conn.execute("""
        SELECT * FROM competitor_trajectories
        WHERE competitor_slug=? ORDER BY generated_at DESC LIMIT 1
    """, (competitor_slug,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_pages(competitor_slug):
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM competitor_pages
        WHERE competitor_slug=? ORDER BY fetched_at DESC
    """, (competitor_slug,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_news(competitor_slug):
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM competitor_news
        WHERE competitor_slug=? ORDER BY published_at DESC
    """, (competitor_slug,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recent_signals(days=30):
    conn = get_db()
    rows = conn.execute("""
        SELECT cs.*, c.name AS competitor_name, c.tier
        FROM competitor_signals cs
        JOIN competitors c ON c.slug = cs.competitor_slug
        WHERE cs.detected_at >= datetime('now', ?)
        ORDER BY cs.detected_at DESC
    """, (f"-{days} days",)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_signal(competitor_slug, signal_type, summary, relevance, category, source_url):
    conn = get_db()
    conn.execute("""
        INSERT INTO competitor_signals
            (competitor_slug, signal_type, summary, relevance, category, source_url)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (competitor_slug, signal_type, summary, relevance, category, source_url))
    conn.commit()
    conn.close()


def update_last_ingested(slug):
    conn = get_db()
    conn.execute("""
        UPDATE competitors SET last_ingested = datetime('now') WHERE slug=?
    """, (slug,))
    conn.commit()
    conn.close()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/nicholasruzicka/Desktop/meddic-engine && python -m pytest tests/test_competitive.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add competitive/__init__.py competitive/models.py tests/test_competitive.py
git commit -m "feat(competitive): add database schema and model helpers"
```

---

### Task 2: Competitor List + URL Verification — `competitive/competitors.py`

**Files:**
- Create: `competitive/competitors.py`
- Modify: `tests/test_competitive.py`

- [ ] **Step 1: Write failing test for competitor seeding and URL check**

Add to `tests/test_competitive.py`:

```python
from competitive.competitors import COMPETITORS_V1, seed_competitors, verify_urls


class TestCompetitors:
    def test_v1_has_five_entries(self):
        assert len(COMPETITORS_V1) == 5

    def test_seed_populates_db(self):
        init_competitive_db()
        seed_competitors()
        all_c = get_all_competitors()
        assert len(all_c) == 5
        slugs = {c["slug"] for c in all_c}
        assert "f2" in slugs
        assert "alphasense" in slugs

    def test_seed_is_idempotent(self):
        init_competitive_db()
        seed_competitors()
        seed_competitors()
        assert len(get_all_competitors()) == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/nicholasruzicka/Desktop/meddic-engine && python -m pytest tests/test_competitive.py::TestCompetitors -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `competitive/competitors.py`**

```python
# competitive/competitors.py
"""Competitor list and URL verification."""
import logging
import requests

from competitive.models import upsert_competitor, get_db

logger = logging.getLogger(__name__)

# (slug, display_name, url, tier, positioning_note)
COMPETITORS_V1 = [
    ("alphasense", "AlphaSense", "https://www.alphasense.com", 1,
     "Incumbent, acquired Tegus 2024"),
    ("rogo", "Rogo", "https://rogo.ai", 1,
     "Dominant in sell-side IB, $75M Series C"),
    ("f2", "F2.ai", "https://f2.ai", 1,
     "Deterministic spreadsheet computation, explicit  challenger"),
    ("blueflame", "Blueflame AI", "https://blueflame.ai", 1,
     "Acquired by Datasite, embedded in VDR"),
    ("keye", "Keye", "https://keye.co", 1,
     "YC F24, Odin co-pilot, built by investors"),
]

COMPETITORS_V2 = [
    ("brightwave", "Brightwave", "https://brightwave.io", 2,
     "Autonomous research agents"),
    ("metal", "Metal", "https://metal.ai", 2,
     "Proprietary knowledge graph for PE"),
    ("toltiq", "ToltIQ", "https://toltiq.com", 2,
     "Ex-KKR founder, H.I.G. exclusive"),
    ("73strings", "73 Strings", "https://www.73strings.com", 2,
     "Middle-office, valuations, portfolio monitoring"),
    ("dili", "Dili", "https://dili.com", 2,
     "VDR compliance, automated checklists"),
    ("diligencesquared", "DiligenceSquared", "https://diligencesquared.com", 3,
     "Voice-agent commercial DD, YC F25"),
    ("glean", "Glean", "https://glean.com", 3,
     "Horizontal enterprise search, expanding to finance"),
    ("harvey", "Harvey AI", "https://harvey.ai", 3,
     "Legal-first, adjacent to finance"),
    ("benchmark", "Benchmark (Gumloop)", "https://benchmark.ai", 3,
     "$50M Series B, ~$1T AUM customer base"),
    ("linq", "Linq / LinqAlpha", "https://linqalpha.com", 3,
     "Programmatic messaging + financial research"),
]


def seed_competitors(v2=False):
    """Insert/update all competitors in the DB."""
    competitors = COMPETITORS_V1 + (COMPETITORS_V2 if v2 else [])
    for slug, name, url, tier, positioning in competitors:
        upsert_competitor(slug, name, url, tier, positioning)
    logger.info(f"Seeded {len(competitors)} competitors")


def verify_urls(slugs=None):
    """HEAD-request each competitor URL. Flag 404s but don't block pipeline."""
    conn = get_db()
    if slugs:
        rows = [dict(r) for r in conn.execute(
            f"SELECT slug, url FROM competitors WHERE slug IN ({','.join('?' * len(slugs))})",
            slugs
        ).fetchall()]
    else:
        rows = [dict(r) for r in conn.execute(
            "SELECT slug, url FROM competitors"
        ).fetchall()]
    conn.close()

    results = {}
    for row in rows:
        slug, url = row["slug"], row["url"]
        try:
            resp = requests.head(url, timeout=10, allow_redirects=True,
                                 headers={"User-Agent": "CIBot/1.0"})
            ok = resp.status_code < 400
            results[slug] = {"url": url, "status": resp.status_code, "ok": ok}
            if not ok:
                logger.warning(f"URL check failed: {slug} → {url} (HTTP {resp.status_code})")
                conn = get_db()
                conn.execute("UPDATE competitors SET url_ok=0 WHERE slug=?", (slug,))
                conn.commit()
                conn.close()
            else:
                conn = get_db()
                conn.execute("UPDATE competitors SET url_ok=1 WHERE slug=?", (slug,))
                conn.commit()
                conn.close()
        except requests.RequestException as e:
            logger.warning(f"URL check error: {slug} → {url} ({e})")
            results[slug] = {"url": url, "status": 0, "ok": False}
            conn = get_db()
            conn.execute("UPDATE competitors SET url_ok=0 WHERE slug=?", (slug,))
            conn.commit()
            conn.close()

    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/nicholasruzicka/Desktop/meddic-engine && python -m pytest tests/test_competitive.py::TestCompetitors -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add competitive/competitors.py tests/test_competitive.py
git commit -m "feat(competitive): add competitor list and URL verification"
```

---

### Task 3: Ingestion Pipeline — `competitive/ingestion.py`

**Files:**
- Create: `competitive/ingestion.py`
- Modify: `tests/test_competitive.py`

- [ ] **Step 1: Write failing tests for sitemap parsing and content extraction**

Add to `tests/test_competitive.py`:

```python
from competitive.ingestion import parse_sitemap_xml, extract_text_from_html, classify_page_type


class TestIngestion:
    def test_parse_sitemap_xml_extracts_urls(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://f2.ai/blog/post-1</loc><lastmod>2026-01-15</lastmod></url>
          <url><loc>https://f2.ai/about</loc><lastmod>2025-12-01</lastmod></url>
        </urlset>"""
        urls = parse_sitemap_xml(xml)
        assert len(urls) == 2
        assert urls[0]["loc"] == "https://f2.ai/blog/post-1"
        assert urls[0]["lastmod"] == "2026-01-15"

    def test_parse_sitemap_handles_empty(self):
        assert parse_sitemap_xml("") == []
        assert parse_sitemap_xml("<html>not xml</html>") == []

    def test_extract_text_strips_tags(self):
        html = "<html><body><h1>Title</h1><p>Hello world</p><script>bad</script></body></html>"
        text = extract_text_from_html(html)
        assert "Title" in text
        assert "Hello world" in text
        assert "bad" not in text

    def test_classify_page_type(self):
        assert classify_page_type("https://f2.ai/blog/my-post") == "blog"
        assert classify_page_type("https://f2.ai/about") == "about"
        assert classify_page_type("https://f2.ai/pricing") == "pricing"
        assert classify_page_type("https://f2.ai/random/page") == "other"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/nicholasruzicka/Desktop/meddic-engine && python -m pytest tests/test_competitive.py::TestIngestion -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `competitive/ingestion.py`**

```python
# competitive/ingestion.py
"""Sitemap parsing, page fetching, Exa news search, rate limiting."""
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests

from competitive.models import save_page, save_news, get_pages, update_last_ingested

logger = logging.getLogger(__name__)

USER_AGENT = "CIBot/1.0 (competitive intelligence research)"
RATE_LIMIT_SECONDS = 1.0  # per domain
_last_request_per_domain: dict[str, float] = {}


def _rate_limit(url: str):
    """Sleep to enforce 1 req/sec per domain."""
    domain = urlparse(url).netloc
    now = time.time()
    last = _last_request_per_domain.get(domain, 0)
    wait = RATE_LIMIT_SECONDS - (now - last)
    if wait > 0:
        time.sleep(wait)
    _last_request_per_domain[domain] = time.time()


def _fetch(url: str, timeout: int = 15) -> Optional[str]:
    """GET a URL with rate limiting. Returns body text or None on failure."""
    _rate_limit(url)
    try:
        resp = requests.get(url, timeout=timeout, allow_redirects=True,
                            headers={"User-Agent": USER_AGENT})
        if resp.status_code >= 400:
            logger.warning(f"HTTP {resp.status_code} fetching {url}")
            return None
        return resp.text
    except requests.RequestException as e:
        logger.warning(f"Fetch error for {url}: {e}")
        return None


# ── Sitemap parsing ──────────────────────────────────────────────────────────

def parse_sitemap_xml(xml_text: str) -> list[dict]:
    """Parse a sitemap.xml string. Returns list of {"loc": url, "lastmod": date_or_none}."""
    if not xml_text or not xml_text.strip():
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        logger.warning("Failed to parse sitemap XML")
        return []

    # Handle namespace
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    urls = []
    for url_elem in root.findall(f"{ns}url"):
        loc = url_elem.findtext(f"{ns}loc")
        lastmod = url_elem.findtext(f"{ns}lastmod")
        if loc:
            urls.append({"loc": loc.strip(), "lastmod": lastmod})
    return urls


def fetch_sitemap(base_url: str) -> list[dict]:
    """Fetch and parse sitemap.xml for a competitor URL."""
    sitemap_url = urljoin(base_url.rstrip("/") + "/", "sitemap.xml")
    body = _fetch(sitemap_url)
    if not body:
        logger.info(f"No sitemap at {sitemap_url}, will fall back to crawl")
        return []
    return parse_sitemap_xml(body)


# ── HTML text extraction ─────────────────────────────────────────────────────

class _TextExtractor(HTMLParser):
    """Simple HTML→text extractor. Strips script/style tags."""
    SKIP_TAGS = {"script", "style", "noscript", "svg", "head"}

    def __init__(self):
        super().__init__()
        self._pieces: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self._pieces.append(text)

    def get_text(self) -> str:
        return "\n".join(self._pieces)


def extract_text_from_html(html: str) -> str:
    """Extract readable text from HTML, stripping scripts/styles."""
    if not html:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        return ""
    return parser.get_text()


# ── Page type classification ─────────────────────────────────────────────────

_PAGE_PATTERNS = {
    "blog": re.compile(r"/blog|/posts?|/articles?|/news|/insights?", re.I),
    "about": re.compile(r"/about|/company|/team|/our-story", re.I),
    "product": re.compile(r"/product|/platform|/features?|/solutions?", re.I),
    "pricing": re.compile(r"/pricing|/plans", re.I),
    "customers": re.compile(r"/customers?|/case.?stud|/testimonials?", re.I),
    "careers": re.compile(r"/careers?|/jobs?|/hiring", re.I),
}


def classify_page_type(url: str) -> str:
    """Classify a URL into a page type based on path patterns."""
    path = urlparse(url).path
    for page_type, pattern in _PAGE_PATTERNS.items():
        if pattern.search(path):
            return page_type
    if path in ("/", ""):
        return "homepage"
    return "other"


# ── Page fetching ────────────────────────────────────────────────────────────

KNOWN_PATHS = ["/about", "/product", "/platform", "/customers", "/pricing"]
MAX_BLOG_POSTS = 25


def ingest_competitor(slug: str, base_url: str):
    """Full ingestion for one competitor: sitemap → pages → fallback crawl.

    Returns count of pages stored.
    """
    stored = 0

    # 1. Try sitemap
    sitemap_urls = fetch_sitemap(base_url)

    if sitemap_urls:
        # Separate blog posts from other pages
        blog_urls = [u for u in sitemap_urls if classify_page_type(u["loc"]) == "blog"]
        other_urls = [u for u in sitemap_urls if classify_page_type(u["loc"]) != "blog"]

        # Fetch all non-blog pages from sitemap
        for entry in other_urls:
            url = entry["loc"]
            html = _fetch(url)
            if html:
                text = extract_text_from_html(html)
                if len(text.strip()) > 50:
                    save_page(slug, url, text, classify_page_type(url), entry.get("lastmod"))
                    stored += 1

        # Fetch up to MAX_BLOG_POSTS blog posts (most recent by lastmod first)
        blog_urls.sort(key=lambda u: u.get("lastmod") or "", reverse=True)
        for entry in blog_urls[:MAX_BLOG_POSTS]:
            url = entry["loc"]
            html = _fetch(url)
            if html:
                text = extract_text_from_html(html)
                if len(text.strip()) > 50:
                    save_page(slug, url, text, "blog", entry.get("lastmod"))
                    stored += 1
    else:
        # Fallback: fetch homepage + known paths
        homepage_html = _fetch(base_url)
        if homepage_html:
            text = extract_text_from_html(homepage_html)
            if text.strip():
                save_page(slug, base_url, text, "homepage")
                stored += 1

        for path in KNOWN_PATHS:
            url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
            html = _fetch(url)
            if html:
                text = extract_text_from_html(html)
                if len(text.strip()) > 50:
                    save_page(slug, url, text, classify_page_type(url))
                    stored += 1

        # Try to find blog links from homepage for one-level-deep crawl
        if homepage_html:
            blog_links = _extract_blog_links(homepage_html, base_url)
            for url in blog_links[:MAX_BLOG_POSTS]:
                html = _fetch(url)
                if html:
                    text = extract_text_from_html(html)
                    if len(text.strip()) > 50:
                        save_page(slug, url, text, "blog")
                        stored += 1

    update_last_ingested(slug)
    logger.info(f"Ingested {stored} pages for {slug}")
    return stored


def _extract_blog_links(html: str, base_url: str) -> list[str]:
    """Extract links that look like blog posts from homepage HTML."""
    links = []
    for match in re.finditer(r'href=["\']([^"\']+)["\']', html):
        href = match.group(1)
        full = urljoin(base_url, href)
        if urlparse(full).netloc == urlparse(base_url).netloc:
            if classify_page_type(full) == "blog" and full not in links:
                links.append(full)
    return links


# ── Exa news search ──────────────────────────────────────────────────────────

def search_news(competitor_name: str, competitor_slug: str):
    """Run Exa web search for funding/announcement news. Store in competitor_news."""
    key = os.getenv("EXA_API_KEY")
    if not key:
        logger.warning("EXA_API_KEY not set — skipping news search")
        return 0

    try:
        from exa_py import Exa
    except ImportError:
        logger.warning("exa-py not installed — skipping news search")
        return 0

    exa = Exa(api_key=key)
    query = f'"{competitor_name}" funding OR Series OR acquired OR raised 2025 OR 2026'

    try:
        resp = exa.search_and_contents(
            query,
            num_results=10,
            start_published_date="2025-01-01",
            text=True,
            highlights={"num_sentences": 2},
        )
    except Exception as e:
        logger.warning(f"Exa search failed for {competitor_name}: {e}")
        return 0

    stored = 0
    for r in getattr(resp, "results", []) or []:
        url = getattr(r, "url", None)
        if not url:
            continue
        title = getattr(r, "title", "") or ""
        published = getattr(r, "published_date", None)
        highlights = getattr(r, "highlights", None) or []
        snippet = highlights[0] if highlights else (getattr(r, "text", "") or "")[:300]

        save_news(competitor_slug, title, url, "exa", (published or "")[:10], snippet)
        stored += 1

    logger.info(f"Stored {stored} news items for {competitor_name}")
    return stored
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/nicholasruzicka/Desktop/meddic-engine && python -m pytest tests/test_competitive.py::TestIngestion -v`
Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add competitive/ingestion.py tests/test_competitive.py
git commit -m "feat(competitive): add ingestion pipeline — sitemap, page fetch, Exa news"
```

---

### Task 4: Claude Analysis Layer — `competitive/analysis.py`

**Files:**
- Create: `competitive/analysis.py`
- Modify: `tests/test_competitive.py`

- [ ] **Step 1: Write failing tests for prompt building and JSON parsing**

Add to `tests/test_competitive.py`:

```python
import json
from competitive.analysis import build_brief_prompt, build_trajectory_prompt, parse_brief_json, parse_trajectory_json


class TestAnalysis:
    def test_build_brief_prompt_includes_competitor_name(self):
        pages = [{"url": "https://f2.ai/about", "content": "F2 is a platform", "page_type": "about"}]
        news = [{"title": "F2 raises $10M", "url": "https://tc.com/f2", "snippet": "Series A"}]
        prompt = build_brief_prompt("F2.ai", pages, news)
        assert "F2.ai" in prompt
        assert "F2 is a platform" in prompt
        assert "Series A" in prompt

    def test_build_trajectory_prompt_chronological(self):
        pages = [
            {"url": "https://f2.ai/blog/old", "content": "Old post", "page_type": "blog", "lastmod": "2025-01-01"},
            {"url": "https://f2.ai/blog/new", "content": "New post", "page_type": "blog", "lastmod": "2026-01-01"},
        ]
        news = [{"title": "Funding", "snippet": "Series A", "published_at": "2025-06-15"}]
        prompt = build_trajectory_prompt("F2.ai", pages, news)
        assert "F2.ai" in prompt
        # Old should appear before new (chronological)
        old_pos = prompt.index("Old post")
        new_pos = prompt.index("New post")
        assert old_pos < new_pos

    def test_parse_brief_json_valid(self):
        valid = json.dumps({
            "positioning_self": "test",
            "positioning_actual": "test",
            "target_icp": "test",
            "pricing_signals": "test",
            "key_differentiation": "test",
            "weakness_vs_": "test",
            "strength_vs_": "test",
            "recent_moves": ["move1"],
            "threat_level": "high",
            "threat_reasoning": "test"
        })
        result = parse_brief_json(valid)
        assert result["threat_level"] == "high"

    def test_parse_brief_json_extracts_from_markdown(self):
        wrapped = '```json\n{"positioning_self":"x","positioning_actual":"x","target_icp":"x","pricing_signals":"x","key_differentiation":"x","weakness_vs_":"x","strength_vs_":"x","recent_moves":[],"threat_level":"low","threat_reasoning":"x"}\n```'
        result = parse_brief_json(wrapped)
        assert result["threat_level"] == "low"

    def test_parse_trajectory_json_valid(self):
        valid = json.dumps({
            "eras": [{"period": "Q1 2025", "dominant_theme": "Launch",
                      "key_moments": ["Launched"], "positioning": "AI-first"}],
            "inflection_points": ["Series A"],
            "trajectory_summary": "Growing fast"
        })
        result = parse_trajectory_json(valid)
        assert len(result["eras"]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/nicholasruzicka/Desktop/meddic-engine && python -m pytest tests/test_competitive.py::TestAnalysis -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `competitive/analysis.py`**

```python
# competitive/analysis.py
"""Claude analysis — Call A (brief) and Call B (trajectory), signal detection."""
import json
import logging
import os
import re
from datetime import datetime, timezone

import anthropic

from competitive.models import (
    save_brief, save_trajectory, save_signal,
    get_latest_brief, get_latest_trajectory,
    get_pages, get_news,
)

logger = logging.getLogger(__name__)

MODEL = os.getenv("COMPETITIVE_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = 2000
TEMPERATURE = 0

BRIEF_SYSTEM = """You are a senior competitive intelligence analyst at , an enterprise AI platform for institutional finance (PE, IB, asset management, credit funds). 's flagship Matrix processes unstructured documents (CIMs, VDRs, 10-Ks, earnings transcripts, IC memos) with multi-agent reasoning and strong data sovereignty (private cloud, SOC2 Type II, zero data retention). Major customers: KKR, Blackstone, Carlyle, Centerview, BlackRock.

Produce a structured competitive brief on {competitor_name} from the source material below. Respond as JSON with this exact schema:

{{
  "positioning_self": "How they describe themselves, in their own words (2 sentences)",
  "positioning_actual": "How they actually compete — the real story, not the marketing (2-3 sentences)",
  "target_icp": "Who they sell to (specific segments)",
  "pricing_signals": "Any public signal on pricing, packaging, or contract size",
  "key_differentiation": "The one thing they say makes them different",
  "weakness_vs_": "Where  likely still wins (cite specific capability gap)",
  "strength_vs_": "Where they may be ahead of  (cite specific capability)",
  "recent_moves": ["3-5 bullet points of their most recent public activity, newest first"],
  "threat_level": "high | medium | low",
  "threat_reasoning": "One sentence on why threat is rated that way"
}}

Cite specific evidence from source material where possible. Every claim should trace to something concrete. Respond with ONLY the JSON object, no markdown wrapping."""

TRAJECTORY_SYSTEM = """You are a senior competitive intelligence analyst. Given this chronological sequence of {competitor_name}'s public blog posts, news, and announcements, construct a narrative timeline of how they've evolved over the last 18 months. Identify strategic shifts, positioning changes, product launches, and key moments.

Respond as JSON with this exact schema:

{{
  "eras": [
    {{
      "period": "e.g., Q2 2025",
      "dominant_theme": "What they were about in this period",
      "key_moments": ["3-5 specific events with dates"],
      "positioning": "How they were positioning themselves"
    }}
  ],
  "inflection_points": ["Specific moments where their strategy shifted"],
  "trajectory_summary": "2-3 sentences on where they're heading next"
}}

Respond with ONLY the JSON object, no markdown wrapping."""

SIGNAL_SYSTEM = """ is monitoring {competitor_name}. Context: {positioning}.

New change detected: {change_description}. Full content of change:

{content}

Respond as JSON:
{{"summary": "one sentence", "category": "product-launch | customer-win | funding | positioning-shift | hiring | cosmetic | other", "relevance": "high | medium | low", "relevance_reasoning": "one sentence", "source_url": "{source_url}"}}

Respond with ONLY the JSON object, no markdown wrapping."""


def _client():
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        logger.error("ANTHROPIC_API_KEY not set")
        return None
    return anthropic.Anthropic(api_key=key)


def _call_claude(system: str, user_content: str) -> str:
    """Call Claude and return the text response."""
    client = _client()
    if not client:
        return ""
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )
        return resp.content[0].text
    except Exception as e:
        logger.error(f"Claude call failed: {e}")
        return ""


def _extract_json(text: str) -> str:
    """Extract JSON from Claude response, handling markdown code blocks."""
    # Try to find JSON in code block
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Try raw JSON
    text = text.strip()
    if text.startswith("{"):
        return text
    return text


# ── Prompt builders ──────────────────────────────────────────────────────────

def build_brief_prompt(competitor_name: str, pages: list[dict], news: list[dict]) -> str:
    """Build the user message for Call A (static brief)."""
    sections = []
    sections.append(f"# Source material for {competitor_name}\n")

    if pages:
        sections.append("## Website pages\n")
        for p in pages:
            url = p.get("url", "")
            ptype = p.get("page_type", "")
            content = (p.get("content") or "")[:3000]
            sections.append(f"### [{ptype}] {url}\n{content}\n")

    if news:
        sections.append("## News and announcements\n")
        for n in news:
            title = n.get("title", "")
            url = n.get("url", "")
            date = n.get("published_at", "")
            snippet = n.get("snippet", "")
            sections.append(f"### {title} ({date})\nURL: {url}\n{snippet}\n")

    return "\n".join(sections)


def build_trajectory_prompt(competitor_name: str, pages: list[dict], news: list[dict]) -> str:
    """Build the user message for Call B (historical trajectory).

    Content is sorted chronologically (oldest first).
    """
    items = []

    for p in pages:
        if p.get("page_type") == "blog":
            items.append({
                "date": p.get("lastmod") or "unknown",
                "type": "blog",
                "url": p.get("url", ""),
                "content": (p.get("content") or "")[:2000],
            })

    for n in news:
        items.append({
            "date": n.get("published_at") or "unknown",
            "type": "news",
            "url": n.get("url", ""),
            "content": f"{n.get('title', '')}: {n.get('snippet', '')}",
        })

    # Sort chronologically
    items.sort(key=lambda x: x["date"] if x["date"] != "unknown" else "0000")

    sections = [f"# Chronological public activity for {competitor_name}\n"]
    for item in items:
        sections.append(f"## [{item['type'].upper()}] {item['date']} — {item['url']}\n{item['content']}\n")

    return "\n".join(sections)


# ── JSON parsers ─────────────────────────────────────────────────────────────

BRIEF_REQUIRED = {"positioning_self", "positioning_actual", "target_icp",
                   "pricing_signals", "key_differentiation", "weakness_vs_",
                   "strength_vs_", "recent_moves", "threat_level", "threat_reasoning"}

TRAJECTORY_REQUIRED = {"eras", "inflection_points", "trajectory_summary"}


def parse_brief_json(text: str) -> dict:
    """Parse Claude's brief response. Returns dict or raises ValueError."""
    raw = _extract_json(text)
    data = json.loads(raw)
    missing = BRIEF_REQUIRED - set(data.keys())
    if missing:
        raise ValueError(f"Brief missing fields: {missing}")
    return data


def parse_trajectory_json(text: str) -> dict:
    """Parse Claude's trajectory response. Returns dict or raises ValueError."""
    raw = _extract_json(text)
    data = json.loads(raw)
    missing = TRAJECTORY_REQUIRED - set(data.keys())
    if missing:
        raise ValueError(f"Trajectory missing fields: {missing}")
    return data


# ── Public API ───────────────────────────────────────────────────────────────

def generate_brief(competitor_slug: str, competitor_name: str, force: bool = False) -> dict | None:
    """Generate Call A brief for a competitor. Caches for 7 days unless force=True."""
    if not force:
        existing = get_latest_brief(competitor_slug)
        if existing:
            gen_at = existing.get("generated_at", "")
            if gen_at:
                try:
                    age_days = (datetime.now(timezone.utc) -
                                datetime.fromisoformat(gen_at.replace("Z", "+00:00")
                                    if "Z" in gen_at else gen_at + "+00:00")).days
                    if age_days < 7:
                        logger.info(f"Brief for {competitor_slug} is {age_days}d old, using cache")
                        return json.loads(existing["brief_json"])
                except (ValueError, TypeError):
                    pass

    pages = get_pages(competitor_slug)
    news = get_news(competitor_slug)

    if not pages and not news:
        logger.warning(f"No source material for {competitor_slug}, skipping brief")
        return None

    system = BRIEF_SYSTEM.format(competitor_name=competitor_name)
    user_msg = build_brief_prompt(competitor_name, pages, news)

    logger.info(f"Generating brief for {competitor_name} (~{len(user_msg)} chars input)")
    raw = _call_claude(system, user_msg)
    if not raw:
        return None

    try:
        brief = parse_brief_json(raw)
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Failed to parse brief for {competitor_name}: {e}\nRaw: {raw[:500]}")
        return None

    save_brief(competitor_slug, json.dumps(brief), MODEL)
    logger.info(f"Brief saved for {competitor_name}: threat={brief.get('threat_level')}")
    return brief


def generate_trajectory(competitor_slug: str, competitor_name: str, force: bool = False) -> dict | None:
    """Generate Call B trajectory for a competitor. Caches for 30 days unless force=True."""
    if not force:
        existing = get_latest_trajectory(competitor_slug)
        if existing:
            gen_at = existing.get("generated_at", "")
            if gen_at:
                try:
                    age_days = (datetime.now(timezone.utc) -
                                datetime.fromisoformat(gen_at.replace("Z", "+00:00")
                                    if "Z" in gen_at else gen_at + "+00:00")).days
                    if age_days < 30:
                        logger.info(f"Trajectory for {competitor_slug} is {age_days}d old, using cache")
                        return json.loads(existing["trajectory_json"])
                except (ValueError, TypeError):
                    pass

    pages = get_pages(competitor_slug)
    news = get_news(competitor_slug)
    blog_pages = [p for p in pages if p.get("page_type") == "blog"]

    if not blog_pages and not news:
        logger.warning(f"No chronological content for {competitor_slug}, skipping trajectory")
        return None

    system = TRAJECTORY_SYSTEM.format(competitor_name=competitor_name)
    user_msg = build_trajectory_prompt(competitor_name, pages, news)

    logger.info(f"Generating trajectory for {competitor_name} (~{len(user_msg)} chars input)")
    raw = _call_claude(system, user_msg)
    if not raw:
        return None

    try:
        trajectory = parse_trajectory_json(raw)
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Failed to parse trajectory for {competitor_name}: {e}\nRaw: {raw[:500]}")
        return None

    save_trajectory(competitor_slug, json.dumps(trajectory), MODEL)
    logger.info(f"Trajectory saved for {competitor_name}: {len(trajectory.get('eras', []))} eras")
    return trajectory


def detect_signals(competitor_slug: str, competitor_name: str, positioning: str):
    """Compare current pages to previous snapshot. Detect new content and classify."""
    pages = get_pages(competitor_slug)
    news = get_news(competitor_slug)

    # Find pages/news that were fetched today (new or updated)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    new_pages = [p for p in pages if (p.get("fetched_at") or "").startswith(today)]
    new_news = [n for n in news if (n.get("fetched_at") or "").startswith(today)]

    if not new_pages and not new_news:
        logger.info(f"No new content for {competitor_slug} today")
        return 0

    detected = 0
    for item in new_pages + new_news:
        content = item.get("content") or item.get("snippet") or ""
        url = item.get("url", "")
        title = item.get("title", "")
        change_desc = f"New {'page' if 'content' in item else 'news'}: {title or url}"

        system = SIGNAL_SYSTEM.format(
            competitor_name=competitor_name,
            positioning=positioning,
            change_description=change_desc,
            content=content[:2000],
            source_url=url,
        )

        raw = _call_claude(system, "Classify this change.")
        if not raw:
            continue

        try:
            raw_json = _extract_json(raw)
            signal = json.loads(raw_json)
            save_signal(
                competitor_slug,
                signal.get("category", "other"),
                signal.get("summary", ""),
                signal.get("relevance", "low"),
                signal.get("category", "other"),
                signal.get("source_url", url),
            )
            detected += 1
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Failed to parse signal for {competitor_slug}: {e}")

    logger.info(f"Detected {detected} signals for {competitor_name}")
    return detected
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/nicholasruzicka/Desktop/meddic-engine && python -m pytest tests/test_competitive.py::TestAnalysis -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add competitive/analysis.py tests/test_competitive.py
git commit -m "feat(competitive): add Claude analysis layer — briefs, trajectories, signals"
```

---

### Task 5: CLI Entrypoint — `competitive_intel.py`

**Files:**
- Create: `competitive_intel.py` (project root)

- [ ] **Step 1: Write failing test for CLI orchestration**

Add to `tests/test_competitive.py`:

```python
from unittest.mock import patch


class TestCLI:
    def test_import_works(self):
        import competitive_intel
        assert hasattr(competitive_intel, "run")

    @patch("competitive.ingestion.ingest_competitor", return_value=5)
    @patch("competitive.ingestion.search_news", return_value=3)
    @patch("competitive.analysis.generate_brief", return_value={"threat_level": "high"})
    @patch("competitive.analysis.generate_trajectory", return_value={"eras": []})
    @patch("competitive.analysis.detect_signals", return_value=0)
    def test_run_single_competitor(self, mock_detect, mock_traj, mock_brief,
                                    mock_news, mock_ingest):
        init_competitive_db()
        from competitive.competitors import seed_competitors
        seed_competitors()
        import competitive_intel
        competitive_intel.run(slugs=["f2"], skip_analysis=True)
        mock_ingest.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/nicholasruzicka/Desktop/meddic-engine && python -m pytest tests/test_competitive.py::TestCLI -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `competitive_intel.py`**

```python
#!/usr/bin/env python3
"""competitive_intel.py — CLI entrypoint for the competitive intelligence pipeline.

Usage:
    python competitive_intel.py                     # Full run: all tier-1 competitors
    python competitive_intel.py --slug f2           # Single competitor
    python competitive_intel.py --ingest-only       # Ingest without Claude analysis
    python competitive_intel.py --force             # Force regenerate briefs/trajectories
    python competitive_intel.py --seed-only         # Just seed competitors, don't run
"""
import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-25s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("competitive_intel")

from competitive.models import init_competitive_db, get_all_competitors, get_competitor
from competitive.competitors import seed_competitors, verify_urls
from competitive.ingestion import ingest_competitor, search_news
from competitive.analysis import generate_brief, generate_trajectory, detect_signals


def run(slugs=None, force=False, skip_analysis=False, ingest_only=False):
    """Run the competitive intelligence pipeline."""
    init_competitive_db()
    seed_competitors()

    competitors = []
    if slugs:
        for s in slugs:
            c = get_competitor(s)
            if c:
                competitors.append(c)
            else:
                logger.warning(f"Unknown competitor slug: {s}")
    else:
        competitors = get_all_competitors()

    if not competitors:
        logger.error("No competitors to process")
        return 1

    logger.info(f"Processing {len(competitors)} competitors")

    # 1. Verify URLs
    url_results = verify_urls([c["slug"] for c in competitors])
    for slug, result in url_results.items():
        if not result["ok"]:
            logger.warning(f"  {slug}: URL check failed (HTTP {result['status']})")

    # 2. Ingest
    for c in competitors:
        slug, name, url = c["slug"], c["name"], c["url"]
        logger.info(f"\n{'='*60}\nIngesting: {name} ({slug})\n{'='*60}")

        pages_stored = ingest_competitor(slug, url)
        logger.info(f"  Pages stored: {pages_stored}")

        news_stored = search_news(name, slug)
        logger.info(f"  News stored: {news_stored}")

    if ingest_only or skip_analysis:
        logger.info("Ingestion complete (skipping analysis)")
        return 0

    # 3. Analyze
    for c in competitors:
        slug, name = c["slug"], c["name"]
        positioning = c.get("positioning", "")

        logger.info(f"\nAnalyzing: {name}")

        brief = generate_brief(slug, name, force=force)
        if brief:
            logger.info(f"  Brief: threat={brief.get('threat_level')}")
        else:
            logger.warning(f"  Brief generation failed for {name}")

        trajectory = generate_trajectory(slug, name, force=force)
        if trajectory:
            logger.info(f"  Trajectory: {len(trajectory.get('eras', []))} eras")
        else:
            logger.warning(f"  Trajectory generation failed for {name}")

        # Signal detection (only meaningful after first baseline)
        detected = detect_signals(slug, name, positioning)
        if detected:
            logger.info(f"  Signals detected: {detected}")

    logger.info("\nPipeline complete")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Competitive Intelligence Pipeline")
    parser.add_argument("--slug", type=str, help="Run for a single competitor slug")
    parser.add_argument("--force", action="store_true", help="Force regenerate cached briefs/trajectories")
    parser.add_argument("--ingest-only", action="store_true", help="Only run ingestion, skip Claude analysis")
    parser.add_argument("--seed-only", action="store_true", help="Only seed competitors to DB")
    args = parser.parse_args()

    if args.seed_only:
        init_competitive_db()
        seed_competitors()
        print("Competitors seeded.")
        return 0

    slugs = [args.slug] if args.slug else None
    return run(slugs=slugs, force=args.force, ingest_only=args.ingest_only)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/nicholasruzicka/Desktop/meddic-engine && python -m pytest tests/test_competitive.py::TestCLI -v`
Expected: All 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add competitive_intel.py tests/test_competitive.py
git commit -m "feat(competitive): add CLI entrypoint for competitive intelligence pipeline"
```

---

### Task 6: Run F2.ai Ingestion + Brief — Validate Output Quality

**Files:** None new — this is a validation step.

- [ ] **Step 1: Run ingestion for F2.ai only**

```bash
cd /Users/nicholasruzicka/Desktop/meddic-engine
python competitive_intel.py --slug f2 --ingest-only
```

Expected: Logs showing pages fetched + news items stored. No errors.

- [ ] **Step 2: Inspect raw ingested content**

```bash
python -c "
from competitive.models import init_competitive_db, get_pages, get_news
init_competitive_db()
pages = get_pages('f2')
news = get_news('f2')
print(f'Pages: {len(pages)}')
for p in pages[:3]:
    print(f'  [{p[\"page_type\"]}] {p[\"url\"]} ({len(p[\"content\"])} chars)')
print(f'News: {len(news)}')
for n in news[:3]:
    print(f'  {n[\"title\"]} ({n[\"published_at\"]})')
"
```

Expected: At least a few pages + news items. Review for quality — is the extracted text readable? Is it the right content?

- [ ] **Step 3: Run full pipeline for F2.ai (with Claude analysis)**

```bash
python competitive_intel.py --slug f2 --force
```

Expected: Logs showing brief + trajectory generation. Takes ~30-60 seconds.

- [ ] **Step 4: Print the brief and trajectory for review**

```bash
python -c "
import json
from competitive.models import init_competitive_db, get_latest_brief, get_latest_trajectory
init_competitive_db()
brief = get_latest_brief('f2')
if brief:
    data = json.loads(brief['brief_json'])
    print(json.dumps(data, indent=2))
else:
    print('No brief found')
print()
traj = get_latest_trajectory('f2')
if traj:
    data = json.loads(traj['trajectory_json'])
    print(json.dumps(data, indent=2))
else:
    print('No trajectory found')
"
```

**CHECKPOINT: Show this output to the user for quality review before proceeding to Task 7.** If the brief quality is poor (vague claims, no source attribution, wrong positioning), diagnose whether the issue is ingestion (not enough content) or prompting (Claude instructions need tuning) and fix before expanding to all 5.

- [ ] **Step 5: Commit**

```bash
git commit --allow-empty -m "checkpoint: F2.ai brief validated — quality approved"
```

---

### Task 7: Expand to All 5 Tier-1 Competitors

**Files:** None new.

- [ ] **Step 1: Run full pipeline for all 5 competitors**

```bash
python competitive_intel.py --force
```

Expected: Processes AlphaSense, Rogo, F2.ai, Blueflame, Keye. ~3-5 minutes total.

- [ ] **Step 2: Print summary of all briefs**

```bash
python -c "
import json
from competitive.models import init_competitive_db, get_all_competitors, get_latest_brief
init_competitive_db()
for c in get_all_competitors():
    brief = get_latest_brief(c['slug'])
    if brief:
        data = json.loads(brief['brief_json'])
        print(f\"{c['name']:20s} threat={data.get('threat_level'):6s} | {data.get('positioning_actual', '')[:80]}\")
    else:
        print(f\"{c['name']:20s} NO BRIEF\")
"
```

Expected: 5 briefs, each with a threat level and positioning summary. Review for quality.

- [ ] **Step 3: Commit**

```bash
git commit --allow-empty -m "checkpoint: all 5 tier-1 competitor briefs generated"
```

---

### Task 8: JSON Generator — `scripts/update_competitive.py`

**Files:**
- Create: `scripts/update_competitive.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_competitive.py`:

```python
class TestJSONGenerator:
    def test_generate_json_output(self):
        init_competitive_db()
        from competitive.competitors import seed_competitors
        seed_competitors()
        # Insert a fake brief
        from competitive.models import save_brief
        import json
        save_brief("f2", json.dumps({
            "positioning_self": "test", "positioning_actual": "test platform",
            "target_icp": "PE", "pricing_signals": "none",
            "key_differentiation": "spreadsheets", "weakness_vs_": "docs",
            "strength_vs_": "speed", "recent_moves": ["launched v2"],
            "threat_level": "high", "threat_reasoning": "direct competitor"
        }), "test-model")

        # Import and run the generator
        import importlib
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
        import update_competitive
        data = update_competitive.build_competitive_data()
        assert "competitors" in data
        assert "stats" in data
        assert len(data["competitors"]) >= 1
        # Check F2 has brief data
        f2 = next((c for c in data["competitors"] if c["slug"] == "f2"), None)
        assert f2 is not None
        assert f2["brief"]["threat_level"] == "high"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/nicholasruzicka/Desktop/meddic-engine && python -m pytest tests/test_competitive.py::TestJSONGenerator -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `scripts/update_competitive.py`**

```python
#!/usr/bin/env python3
"""scripts/update_competitive.py

Reads competitive intelligence from SQLite, writes export/competitive_data.json.
Runs on the 10-min cron cycle via refresh_dashboards.sh.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from competitive.models import (
    init_competitive_db, get_all_competitors,
    get_latest_brief, get_latest_trajectory, get_recent_signals, get_db,
)

OUTPUT = os.path.join(ROOT, "export", "competitive_data.json")


def build_competitive_data() -> dict:
    """Build the JSON payload for the competitive dashboard."""
    init_competitive_db()
    competitors = get_all_competitors()
    signals = get_recent_signals(days=30)

    competitor_data = []
    high_threat = 0
    total_signals_week = 0

    # Count signals from last 7 days
    now = datetime.now(timezone.utc)
    for s in signals:
        detected = s.get("detected_at", "")
        if detected:
            try:
                dt = datetime.fromisoformat(detected)
                if (now - dt).days <= 7:
                    total_signals_week += 1
            except (ValueError, TypeError):
                pass

    for c in competitors:
        slug = c["slug"]
        brief_row = get_latest_brief(slug)
        traj_row = get_latest_trajectory(slug)

        brief = {}
        if brief_row:
            try:
                brief = json.loads(brief_row["brief_json"])
            except (json.JSONDecodeError, TypeError):
                pass

        trajectory = {}
        if traj_row:
            try:
                trajectory = json.loads(traj_row["trajectory_json"])
            except (json.JSONDecodeError, TypeError):
                pass

        if brief.get("threat_level") == "high":
            high_threat += 1

        comp_signals = [s for s in signals if s["competitor_slug"] == slug]

        competitor_data.append({
            "slug": slug,
            "name": c["name"],
            "url": c["url"],
            "tier": c["tier"],
            "positioning": c.get("positioning", ""),
            "url_ok": c.get("url_ok", 1),
            "last_ingested": c.get("last_ingested"),
            "brief": brief,
            "brief_generated_at": brief_row["generated_at"] if brief_row else None,
            "trajectory": trajectory,
            "trajectory_generated_at": traj_row["generated_at"] if traj_row else None,
            "recent_signals": comp_signals[:10],
        })

    # Sort: tier 1 first, then alphabetical
    competitor_data.sort(key=lambda c: (c["tier"], c["name"]))

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "stats": {
            "total_competitors": len(competitors),
            "signals_this_week": total_signals_week,
            "high_threat": high_threat,
        },
        "competitors": competitor_data,
        "signals": signals[:50],
    }


def main() -> int:
    data = build_competitive_data()

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    stats = data["stats"]
    print(f"✓ competitive_data.json — {stats['total_competitors']} competitors, "
          f"{stats['signals_this_week']} signals this week, "
          f"{stats['high_threat']} high threat")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/nicholasruzicka/Desktop/meddic-engine && python -m pytest tests/test_competitive.py::TestJSONGenerator -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/update_competitive.py tests/test_competitive.py
git commit -m "feat(competitive): add JSON generator for dashboard data"
```

---

### Task 9: Dashboard Route + Nav Updates

**Files:**
- Modify: `dashboard/routes.py`
- Modify: `export/index.html` (nav only)
- Modify: `export/analytics.html` (nav only)
- Modify: `export/ops.html` (nav only)
- Modify: `export/methodology.html` (nav only)
- Modify: `scripts/refresh_dashboards.sh`

- [ ] **Step 1: Add competitive route to `dashboard/routes.py`**

Add `"competitive": "competitive.html"` to `_PAGES` dict.
Add `"competitive_data.json"` to `_DATA_WHITELIST`.
Add a route function:

```python
@dashboard_bp.route("/competitive", methods=["GET"])
def competitive():
    return _serve_page(_PAGES["competitive"])
```

- [ ] **Step 2: Add nav link to all 4 existing HTML pages**

In each page's `<nav class="nav-links">` section, add before the last link:

```html
<a href="competitive.html">Competitive</a>
<span class="nav-sep">·</span>
```

Files to update:
- `export/index.html` — line with `nav-links`
- `export/analytics.html` — find `nav-links`
- `export/ops.html` — find `nav-links`
- `export/methodology.html` — find `nav-links`

- [ ] **Step 3: Add `update_competitive.py` to `scripts/refresh_dashboards.sh`**

Add this line after the existing update scripts:

```bash
python3 scripts/update_competitive.py >> logs/refresh.log 2>&1
```

- [ ] **Step 4: Verify route works**

```bash
cd /Users/nicholasruzicka/Desktop/meddic-engine
python scripts/update_competitive.py
# Should create export/competitive_data.json
ls -la export/competitive_data.json
```

- [ ] **Step 5: Commit**

```bash
git add dashboard/routes.py export/index.html export/analytics.html export/ops.html export/methodology.html scripts/refresh_dashboards.sh
git commit -m "feat(competitive): add route, nav links, and cron integration"
```

---

### Task 10: Dashboard UI — `export/competitive.html`

**Files:**
- Create: `export/competitive.html`

This is the largest task. The page has three sub-tabs: Briefs, Trajectories, Live Signals.

- [ ] **Step 1: Create the HTML page**

Create `export/competitive.html` with:
- Same `<head>` as `index.html` (fonts, shared.css, dark theme tokens)
- Same `<header>` nav pattern with "Competitive" as active
- Hero section with stats
- Three sub-tabs using a simple JS tab switcher
- Briefs grid with accordion expansion
- Trajectories vertical list with competitor dropdown
- Signals feed with filters
- All data loaded from `competitive_data.json` via fetch

The full HTML is ~800 lines. Key patterns to follow from `index.html`:
- `header` with `.brand` + `.nav-links` + `.nav-right`
- Stats use `.stat-card` grid (4 columns)
- Tab switcher uses `data-tab` attributes and JS to toggle visibility
- Cards use `var(--bg-surface)` background, `var(--border)` borders
- Accordion uses CSS `max-height` transition, toggled by JS adding `.open` class
- Pills use `.pill--red`, `.pill--amber`, `.pill--muted` for threat levels
- Timestamps formatted as relative ("3 days ago") via JS

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Competitive Intelligence — MEDDIC Engine</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=Playfair+Display:wght@500;600&display=swap" rel="stylesheet">
<link rel="preload" as="fetch" href="competitive_data.json" crossorigin>
<link rel="stylesheet" href="/shared.css">
<style>
  :root{
    --bg:#101010;--surface:#161616;--elevated:#1c1c1c;--overlay:#222222;
    --text:#ffffff;--text-2:rgba(255,255,255,0.72);--text-3:rgba(255,255,255,0.55);
    --text-4:rgba(255,255,255,0.35);
    --border:rgba(255,255,255,0.10);--border-strong:rgba(255,255,255,0.18);--divider:#1F1F1F;
    --blue:#465BFF;--blue-dim:rgba(70,91,255,0.15);--blue-border:rgba(70,91,255,0.40);
    --green:#10b981;--green-dim:rgba(16,185,129,0.12);
    --amber:#f59e0b;--amber-dim:rgba(245,158,11,0.12);
    --red:#ef4444;--red-dim:rgba(239,68,68,0.12);
    --font-ui:'Inter',system-ui,sans-serif;--font-display:'Playfair Display',Georgia,serif;
  }
  *{box-sizing:border-box}
  html,body{margin:0;padding:0;background:var(--bg);color:var(--text);
    font-family:var(--font-ui);font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased}
  a{color:inherit}

  /* NAV — same as index.html */
  header{position:sticky;top:0;z-index:20;background:rgba(16,16,16,0.92);backdrop-filter:blur(12px);
    border-bottom:1px solid rgba(255,255,255,0.08);height:64px;padding:0 28px;
    display:flex;align-items:center;justify-content:space-between;gap:20px}
  .brand{display:flex;align-items:center;gap:12px}
  .brand .name{font-size:15px;font-weight:500;letter-spacing:0.02em}
  .brand .sub{color:var(--text-3);font-size:13px;letter-spacing:0.02em;margin-left:2px}
  .brand .eyebrow{margin-left:14px;padding-left:14px;border-left:1px solid var(--border);
    font-size:10px;font-weight:500;letter-spacing:0.10em;text-transform:uppercase;color:var(--text-3)}
  .nav-links{display:flex;align-items:center;gap:10px;margin-left:auto;margin-right:16px}
  .nav-links a{font-size:11px;font-weight:500;letter-spacing:0.08em;text-transform:uppercase;
    color:var(--text-3);text-decoration:none;transition:color 150ms}
  .nav-links a:hover{color:var(--text)}
  .nav-links a.active{color:var(--blue)}
  .nav-links .nav-sep{color:rgba(255,255,255,0.20);font-size:11px}

  /* HERO */
  .hero{padding:48px 28px 32px;max-width:1200px;margin:0 auto}
  .hero .eyebrow{font-size:10px;font-weight:600;letter-spacing:0.14em;text-transform:uppercase;
    color:var(--blue);margin-bottom:8px}
  .hero h1{font-family:var(--font-display);font-size:42px;font-weight:500;margin:0 0 8px;
    letter-spacing:-0.02em}
  .hero .subline{color:var(--text-3);font-size:14px;margin-bottom:24px}
  .stat-row{display:flex;gap:24px;flex-wrap:wrap}
  .stat-card{background:var(--surface);border:1px solid var(--border);border-radius:2px;
    padding:14px 20px;min-width:140px}
  .stat-card .val{font-size:28px;font-weight:600;letter-spacing:-0.02em}
  .stat-card .lbl{font-size:10px;font-weight:500;letter-spacing:0.10em;text-transform:uppercase;
    color:var(--text-3);margin-top:2px}

  /* SUB-TABS */
  .tab-bar{display:flex;gap:0;border-bottom:1px solid var(--border);padding:0 28px;max-width:1200px;margin:0 auto}
  .tab-btn{padding:12px 20px;font-size:12px;font-weight:500;letter-spacing:0.06em;
    text-transform:uppercase;color:var(--text-3);cursor:pointer;border:none;background:none;
    border-bottom:2px solid transparent;transition:all 150ms}
  .tab-btn:hover{color:var(--text)}
  .tab-btn.active{color:var(--blue);border-bottom-color:var(--blue)}

  .tab-content{display:none;padding:24px 28px;max-width:1200px;margin:0 auto}
  .tab-content.active{display:block}

  /* BRIEFS GRID */
  .briefs-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px;margin-bottom:32px}
  .brief-card{background:var(--surface);border:1px solid var(--border);border-radius:2px;
    padding:20px;cursor:pointer;transition:border-color 150ms}
  .brief-card:hover{border-color:var(--border-strong)}
  .brief-card .card-header{display:flex;align-items:center;gap:10px;margin-bottom:12px}
  .brief-card .card-name{font-size:16px;font-weight:600}
  .brief-card .card-line{color:var(--text-3);font-size:13px;line-height:1.5;margin-bottom:6px}
  .brief-card .card-meta{font-size:11px;color:var(--text-4)}

  /* Threat dot */
  .threat-dot{width:8px;height:8px;border-radius:50%;display:inline-block;flex-shrink:0}
  .threat-dot.high{background:var(--red)}
  .threat-dot.medium{background:var(--amber)}
  .threat-dot.low{background:rgba(255,255,255,0.25)}

  /* ACCORDION */
  .accordion-body{max-height:0;overflow:hidden;transition:max-height 300ms ease}
  .brief-card.open .accordion-body{max-height:2000px}
  .accordion-body .detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;
    padding-top:16px;border-top:1px solid var(--border);margin-top:12px}
  .detail-field .field-label{font-size:10px;font-weight:600;letter-spacing:0.10em;
    text-transform:uppercase;color:var(--text-3);margin-bottom:4px}
  .detail-field .field-value{font-size:13px;color:var(--text-2);line-height:1.6}
  .detail-field.full-width{grid-column:1/-1}
  .moves-list{list-style:none;padding:0;margin:4px 0 0}
  .moves-list li{font-size:13px;color:var(--text-2);padding:4px 0;
    border-bottom:1px solid rgba(255,255,255,0.05)}
  .moves-list li:last-child{border:none}

  /* METHODOLOGY BLOCK */
  .methodology{background:var(--surface);border:1px solid var(--border);border-radius:2px;
    padding:20px;margin-top:8px}
  .methodology h3{font-size:13px;font-weight:600;letter-spacing:0.04em;margin:0 0 8px;color:var(--text-2)}
  .methodology p{font-size:13px;color:var(--text-3);line-height:1.6;margin:0}

  /* TRAJECTORIES */
  .traj-controls{margin-bottom:24px}
  .traj-select{background:var(--surface);border:1px solid var(--border);border-radius:2px;
    padding:8px 12px;font-size:13px;color:var(--text);cursor:pointer;min-width:200px}
  .traj-select option{background:var(--bg);color:var(--text)}
  .era-list{display:flex;flex-direction:column;gap:16px}
  .era-card{background:var(--surface);border:1px solid var(--border);border-radius:2px;padding:20px}
  .era-card .era-period{font-size:11px;font-weight:600;letter-spacing:0.10em;
    text-transform:uppercase;color:var(--blue);margin-bottom:8px}
  .era-card .era-theme{font-size:16px;font-weight:500;margin-bottom:8px}
  .era-card .era-positioning{font-size:13px;color:var(--text-3);margin-bottom:12px;font-style:italic}
  .era-card .era-moments{list-style:none;padding:0;margin:0}
  .era-card .era-moments li{font-size:13px;color:var(--text-2);padding:4px 0;
    padding-left:12px;position:relative}
  .era-card .era-moments li::before{content:"";position:absolute;left:0;top:11px;
    width:4px;height:4px;border-radius:50%;background:var(--blue)}
  .inflection{background:var(--blue-dim);border:1px solid rgba(70,91,255,0.25);
    border-radius:2px;padding:16px 20px;margin-top:16px}
  .inflection h4{font-size:11px;font-weight:600;letter-spacing:0.10em;text-transform:uppercase;
    color:var(--blue);margin:0 0 8px}
  .inflection li{font-size:13px;color:var(--text-2);padding:3px 0}
  .traj-summary{background:var(--surface);border:1px solid var(--border);border-radius:2px;
    padding:20px;margin-top:16px}
  .traj-summary h4{font-size:11px;font-weight:600;letter-spacing:0.10em;text-transform:uppercase;
    color:var(--text-3);margin:0 0 8px}
  .traj-summary p{font-size:14px;color:var(--text-2);line-height:1.6;margin:0}

  /* SIGNALS FEED */
  .signals-layout{display:flex;gap:24px}
  .signals-filters{width:200px;flex-shrink:0}
  .signals-feed{flex:1}
  .filter-group{margin-bottom:20px}
  .filter-group h4{font-size:10px;font-weight:600;letter-spacing:0.10em;text-transform:uppercase;
    color:var(--text-3);margin:0 0 8px}
  .filter-chip{display:inline-block;padding:4px 10px;font-size:11px;border:1px solid var(--border);
    border-radius:2px;margin:0 4px 4px 0;cursor:pointer;color:var(--text-3);transition:all 150ms}
  .filter-chip:hover,.filter-chip.active{border-color:var(--blue);color:var(--blue);background:var(--blue-dim)}

  .signal-card{background:var(--surface);border:1px solid var(--border);border-radius:2px;
    padding:16px 20px;margin-bottom:8px}
  .signal-header{display:flex;align-items:center;gap:8px;margin-bottom:6px;flex-wrap:wrap}
  .signal-summary{font-size:14px;color:var(--text-2);line-height:1.5}
  .signal-source{font-size:12px;color:var(--blue);text-decoration:none;margin-top:6px;display:inline-block}
  .signal-source:hover{text-decoration:underline}

  .empty-state{text-align:center;padding:60px 20px;color:var(--text-3)}
  .empty-state h3{font-size:18px;font-weight:500;margin:0 0 8px;color:var(--text-2)}
  .empty-state p{font-size:14px;margin:0}

  @media(max-width:768px){
    .briefs-grid{grid-template-columns:1fr}
    .accordion-body .detail-grid{grid-template-columns:1fr}
    .signals-layout{flex-direction:column}
    .signals-filters{width:100%}
  }
</style>
</head>
<body>
<header>
  <div class="brand">
    <span class="name"></span>
    <span class="sub">Signal Engine</span>
    <span class="eyebrow">Competitive Intelligence</span>
  </div>
  <nav class="nav-links">
    <a href="analytics.html">Analytics</a>
    <span class="nav-sep">&middot;</span>
    <a href="ops.html">Pipeline Ops</a>
    <span class="nav-sep">&middot;</span>
    <a href="methodology.html">Methodology</a>
    <span class="nav-sep">&middot;</span>
    <a href="competitive.html" class="active">Competitive</a>
    <span class="nav-sep">&middot;</span>
    <a href="index.html">&larr; Contacts</a>
  </nav>
</header>

<section class="hero">
  <div class="eyebrow">Competitive Intelligence</div>
  <h1>The competitive perimeter.</h1>
  <div class="subline" id="subline">Loading&hellip;</div>
  <div class="stat-row">
    <div class="stat-card"><div class="val" id="stat-competitors">—</div><div class="lbl">Competitors</div></div>
    <div class="stat-card"><div class="val" id="stat-signals">—</div><div class="lbl">Signals This Week</div></div>
    <div class="stat-card"><div class="val" id="stat-threat">—</div><div class="lbl">High Threat</div></div>
    <div class="stat-card"><div class="val" id="stat-updated">—</div><div class="lbl">Last Updated</div></div>
  </div>
</section>

<div class="tab-bar">
  <button class="tab-btn active" data-tab="briefs">Briefs</button>
  <button class="tab-btn" data-tab="trajectories">Trajectories</button>
  <button class="tab-btn" data-tab="signals">Live Signals</button>
</div>

<div id="tab-briefs" class="tab-content active"></div>
<div id="tab-trajectories" class="tab-content"></div>
<div id="tab-signals" class="tab-content"></div>

<script>
(function(){
  // ── Tab switching ──
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
    });
  });

  // ── Helpers ──
  const esc = s => {
    const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML;
  };
  const ago = dt => {
    if (!dt) return '—';
    const ms = Date.now() - new Date(dt).getTime();
    const d = Math.floor(ms / 86400000);
    if (d > 30) return Math.floor(d/30) + 'mo ago';
    if (d > 0) return d + 'd ago';
    const h = Math.floor(ms / 3600000);
    if (h > 0) return h + 'h ago';
    return 'just now';
  };
  const threatPill = level => {
    const cls = level === 'high' ? 'pill--red' : level === 'medium' ? 'pill--amber' : 'pill--muted';
    return `<span class="pill ${cls}">${esc(level)}</span>`;
  };
  const threatDot = level =>
    `<span class="threat-dot ${level || 'low'}"></span>`;

  // ── Load data ──
  let DATA = null;
  fetch('competitive_data.json').then(r => r.json()).then(data => {
    DATA = data;
    renderStats(data);
    renderBriefs(data.competitors);
    renderTrajectories(data.competitors);
    renderSignals(data.signals, data.competitors);
  }).catch(err => {
    document.getElementById('subline').textContent = 'Failed to load data: ' + err.message;
  });

  // ── Stats ──
  function renderStats(data) {
    const s = data.stats;
    document.getElementById('stat-competitors').textContent = s.total_competitors;
    document.getElementById('stat-signals').textContent = s.signals_this_week;
    document.getElementById('stat-threat').textContent = s.high_threat;
    document.getElementById('stat-updated').textContent = ago(data.generated_at);
    document.getElementById('subline').textContent =
      `${s.total_competitors} companies actively competing for 's buyer. Briefs refreshed weekly. Signals detected daily.`;
  }

  // ── Briefs ──
  function renderBriefs(competitors) {
    const container = document.getElementById('tab-briefs');
    let html = '<div class="briefs-grid">';

    for (const c of competitors) {
      const b = c.brief || {};
      const threat = b.threat_level || 'low';
      html += `
        <div class="brief-card" onclick="this.classList.toggle('open')">
          <div class="card-header">
            ${threatDot(threat)}
            <span class="card-name">${esc(c.name)}</span>
            ${threatPill(threat)}
            <span class="pill pill--muted">Tier ${c.tier}</span>
          </div>
          <div class="card-line">${esc(b.positioning_actual || c.positioning || 'Brief pending...')}</div>
          <div class="card-line" style="font-size:12px;color:var(--text-3)">${esc(b.key_differentiation || '')}</div>
          <div class="card-meta">Updated ${ago(c.brief_generated_at)}</div>
          <div class="accordion-body">
            <div class="detail-grid">
              <div class="detail-field">
                <div class="field-label">Self-positioning</div>
                <div class="field-value">${esc(b.positioning_self || '—')}</div>
              </div>
              <div class="detail-field">
                <div class="field-label">Target ICP</div>
                <div class="field-value">${esc(b.target_icp || '—')}</div>
              </div>
              <div class="detail-field">
                <div class="field-label">Pricing Signals</div>
                <div class="field-value">${esc(b.pricing_signals || '—')}</div>
              </div>
              <div class="detail-field">
                <div class="field-label">Threat Reasoning</div>
                <div class="field-value">${esc(b.threat_reasoning || '—')}</div>
              </div>
              <div class="detail-field">
                <div class="field-label">Weakness vs </div>
                <div class="field-value">${esc(b.weakness_vs_ || '—')}</div>
              </div>
              <div class="detail-field">
                <div class="field-label">Strength vs </div>
                <div class="field-value">${esc(b.strength_vs_ || '—')}</div>
              </div>
              <div class="detail-field full-width">
                <div class="field-label">Recent Moves</div>
                <ul class="moves-list">
                  ${(b.recent_moves || []).map(m => `<li>${esc(m)}</li>`).join('')}
                </ul>
              </div>
            </div>
          </div>
        </div>`;
    }

    html += '</div>';
    html += `<div class="methodology">
      <h3>How these briefs are built</h3>
      <p>Each brief is synthesized by Claude from the competitor's public website (sitemap + key pages),
      up to 25 blog posts, and recent news via Exa web search. Briefs refresh weekly.
      Every claim traces to specific source material — no hallucinated intelligence.</p>
    </div>`;
    container.innerHTML = html;
  }

  // ── Trajectories ──
  function renderTrajectories(competitors) {
    const container = document.getElementById('tab-trajectories');
    const withTraj = competitors.filter(c => c.trajectory && c.trajectory.eras);
    if (!withTraj.length) {
      container.innerHTML = '<div class="empty-state"><h3>No trajectories yet</h3><p>Trajectories will appear after the first full pipeline run.</p></div>';
      return;
    }

    let selectHtml = '<div class="traj-controls"><select class="traj-select" id="traj-select">';
    for (const c of withTraj) {
      const sel = c.slug === 'f2' ? ' selected' : '';
      selectHtml += `<option value="${esc(c.slug)}"${sel}>${esc(c.name)}</option>`;
    }
    selectHtml += '</select></div>';

    container.innerHTML = selectHtml + '<div id="traj-display"></div>';

    function showTrajectory(slug) {
      const c = withTraj.find(x => x.slug === slug);
      if (!c || !c.trajectory) return;
      const t = c.trajectory;
      let html = '<div class="era-list">';

      for (const era of (t.eras || [])) {
        html += `<div class="era-card">
          <div class="era-period">${esc(era.period)}</div>
          <div class="era-theme">${esc(era.dominant_theme)}</div>
          <div class="era-positioning">${esc(era.positioning)}</div>
          <ul class="era-moments">
            ${(era.key_moments || []).map(m => `<li>${esc(m)}</li>`).join('')}
          </ul>
        </div>`;
      }

      if (t.inflection_points && t.inflection_points.length) {
        html += `<div class="inflection"><h4>Inflection Points</h4><ul>
          ${t.inflection_points.map(p => `<li>${esc(p)}</li>`).join('')}
        </ul></div>`;
      }

      if (t.trajectory_summary) {
        html += `<div class="traj-summary"><h4>Trajectory</h4><p>${esc(t.trajectory_summary)}</p></div>`;
      }

      html += '</div>';
      document.getElementById('traj-display').innerHTML = html;
    }

    document.getElementById('traj-select').addEventListener('change', e => showTrajectory(e.target.value));
    // Default: F2 if available, else first
    showTrajectory(withTraj.find(c => c.slug === 'f2') ? 'f2' : withTraj[0].slug);
  }

  // ── Signals ──
  function renderSignals(signals, competitors) {
    const container = document.getElementById('tab-signals');

    if (!signals || !signals.length) {
      container.innerHTML = `<div class="empty-state">
        <h3>Live signals start appearing after 24h of monitoring</h3>
        <p>The system detects new blog posts, sitemap changes, and news items daily.
        Check back tomorrow for the first signals.</p>
      </div>`;
      return;
    }

    // Build filter chips
    const categories = [...new Set(signals.map(s => s.category).filter(Boolean))];
    const relevances = ['high', 'medium', 'low'];
    const compNames = [...new Set(signals.map(s => s.competitor_name).filter(Boolean))];

    let filtersHtml = '<div class="filter-group"><h4>Category</h4>';
    filtersHtml += '<span class="filter-chip active" data-filter="category" data-val="all">All</span>';
    categories.forEach(c => { filtersHtml += `<span class="filter-chip" data-filter="category" data-val="${esc(c)}">${esc(c)}</span>`; });
    filtersHtml += '</div>';

    filtersHtml += '<div class="filter-group"><h4>Relevance</h4>';
    filtersHtml += '<span class="filter-chip active" data-filter="relevance" data-val="all">All</span>';
    relevances.forEach(r => { filtersHtml += `<span class="filter-chip" data-filter="relevance" data-val="${r}">${r}</span>`; });
    filtersHtml += '</div>';

    filtersHtml += '<div class="filter-group"><h4>Competitor</h4>';
    filtersHtml += '<span class="filter-chip active" data-filter="competitor" data-val="all">All</span>';
    compNames.forEach(n => { filtersHtml += `<span class="filter-chip" data-filter="competitor" data-val="${esc(n)}">${esc(n)}</span>`; });
    filtersHtml += '</div>';

    let feedHtml = signals.map(s => `
      <div class="signal-card" data-category="${esc(s.category || '')}" data-relevance="${esc(s.relevance || '')}" data-competitor="${esc(s.competitor_name || '')}">
        <div class="signal-header">
          <span class="pill pill--muted">${esc(s.category || 'other')}</span>
          <span style="font-weight:600;font-size:13px">${esc(s.competitor_name || s.competitor_slug)}</span>
          ${threatPill(s.relevance || 'low')}
          <span style="font-size:11px;color:var(--text-4);margin-left:auto">${ago(s.detected_at)}</span>
        </div>
        <div class="signal-summary">${esc(s.summary)}</div>
        ${s.source_url ? `<a class="signal-source" href="${esc(s.source_url)}" target="_blank" rel="noopener">&rarr; View source</a>` : ''}
      </div>
    `).join('');

    container.innerHTML = `<div class="signals-layout">
      <div class="signals-filters">${filtersHtml}</div>
      <div class="signals-feed" id="signals-feed">${feedHtml}</div>
    </div>`;

    // Filter logic
    let activeFilters = { category: 'all', relevance: 'all', competitor: 'all' };
    container.querySelectorAll('.filter-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        const group = chip.dataset.filter;
        container.querySelectorAll(`.filter-chip[data-filter="${group}"]`).forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        activeFilters[group] = chip.dataset.val;
        applyFilters();
      });
    });

    function applyFilters() {
      container.querySelectorAll('.signal-card').forEach(card => {
        const show =
          (activeFilters.category === 'all' || card.dataset.category === activeFilters.category) &&
          (activeFilters.relevance === 'all' || card.dataset.relevance === activeFilters.relevance) &&
          (activeFilters.competitor === 'all' || card.dataset.competitor === activeFilters.competitor);
        card.style.display = show ? '' : 'none';
      });
    }
  }
})();
</script>
</body>
</html>
```

- [ ] **Step 2: Generate the JSON data and verify page loads**

```bash
cd /Users/nicholasruzicka/Desktop/meddic-engine
python scripts/update_competitive.py
```

- [ ] **Step 3: Start the static server and verify in browser**

```bash
cd /Users/nicholasruzicka/Desktop/meddic-engine
python scripts/static_server.py &
# Open http://localhost:8080/competitive.html in browser
```

Verify:
- Page loads with dark theme
- Stats show correct numbers
- Briefs tab shows 5 competitor cards
- Clicking a card expands the accordion with full brief
- Trajectories tab shows dropdown, selecting a competitor renders eras
- Signals tab shows empty state ("Live signals start appearing after 24h")
- Nav links work — can navigate to/from other pages

- [ ] **Step 4: Commit**

```bash
git add export/competitive.html
git commit -m "feat(competitive): add dashboard UI with briefs, trajectories, signals tabs"
```

---

### Task 11: Shell Wrapper — `scripts/run_competitive.sh`

**Files:**
- Create: `scripts/run_competitive.sh`

- [ ] **Step 1: Create the script**

```bash
#!/bin/bash
# Daily competitive intelligence pipeline.
# Install as its own cron job, separate from refresh_dashboards.sh:
#   chmod +x scripts/run_competitive.sh
#   ( crontab -l 2>/dev/null | grep -v run_competitive
#     echo "0 6 * * * /root/meddic-engine/scripts/run_competitive.sh"
#   ) | crontab -

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -d venv ] && source venv/bin/activate
mkdir -p logs
echo "$(date) — starting competitive intelligence run" >> logs/competitive.log
python3 competitive_intel.py >> logs/competitive.log 2>&1
python3 scripts/update_competitive.py >> logs/competitive.log 2>&1
echo "$(date) — competitive intelligence run complete" >> logs/competitive.log
```

- [ ] **Step 2: Make executable**

```bash
chmod +x scripts/run_competitive.sh
```

- [ ] **Step 3: Commit**

```bash
git add scripts/run_competitive.sh
git commit -m "feat(competitive): add daily cron wrapper script"
```

---

### Task 12: Final Integration Test + Cleanup

**Files:** None new.

- [ ] **Step 1: Run the full test suite**

```bash
cd /Users/nicholasruzicka/Desktop/meddic-engine
python -m pytest tests/test_competitive.py -v
```

Expected: All tests PASS.

- [ ] **Step 2: Run the full pipeline end-to-end**

```bash
python competitive_intel.py --force
python scripts/update_competitive.py
```

- [ ] **Step 3: Verify the dashboard in browser**

Start the server and open `http://localhost:8080/competitive.html`. Verify all three tabs render correctly with real data.

- [ ] **Step 4: Check nav links on all pages**

Visit each page and confirm the "Competitive" link appears and works:
- `http://localhost:8080/index.html`
- `http://localhost:8080/analytics.html`
- `http://localhost:8080/ops.html`
- `http://localhost:8080/methodology.html`

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "feat(competitive): complete competitive intelligence tab — briefs, trajectories, signals"
```
