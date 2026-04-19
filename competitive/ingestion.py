"""Competitive intelligence ingestion pipeline.

Provides sitemap parsing, HTML text extraction, page-type classification,
rate-limited fetching, and full competitor ingestion orchestration.
"""

import hashlib
import logging
import os
import re
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests

from competitive.models import save_page, save_news, update_last_ingested

logger = logging.getLogger(__name__)

# ── Sitemap parsing ────────────────────────────────────────────────────────────

# Standard sitemap XML namespace
_SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


def parse_sitemap_xml(xml_text: str) -> list[dict]:
    """Parse sitemap.xml text and return list of {"loc": url, "lastmod": date_or_none}.

    Handles XML namespaces. Returns [] on empty or malformed input.
    """
    if not xml_text or not xml_text.strip():
        return []

    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError:
        return []

    results: list[dict] = []

    # Strip namespace from tag for matching
    def _tag(element) -> str:
        tag = element.tag
        if tag.startswith("{"):
            tag = tag.split("}", 1)[1]
        return tag

    # Handle both <urlset> and <sitemapindex> (index of sitemaps)
    def _find_urls(node) -> None:
        for child in node:
            if _tag(child) in ("url", "sitemap"):
                loc = None
                lastmod = None
                for sub in child:
                    name = _tag(sub)
                    if name == "loc":
                        loc = (sub.text or "").strip()
                    elif name == "lastmod":
                        lastmod = (sub.text or "").strip() or None
                if loc:
                    results.append({"loc": loc, "lastmod": lastmod})
            else:
                _find_urls(child)

    _find_urls(root)
    return results


# ── HTML text extraction ───────────────────────────────────────────────────────

_SKIP_TAGS = {"script", "style", "noscript", "svg", "head"}


class _TextExtractor(HTMLParser):
    """Minimal HTMLParser subclass that strips unwanted tags and collects text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        if tag.lower() in _SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str):
        if tag.lower() in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str):
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self._parts.append(text)

    def get_text(self) -> str:
        return " ".join(self._parts)


def extract_text_from_html(html: str) -> str:
    """Strip script/style/noscript/svg/head tags; return readable text.

    Uses stdlib html.parser — no BeautifulSoup dependency.
    """
    if not html:
        return ""
    parser = _TextExtractor()
    parser.feed(html)
    return parser.get_text()


# ── Page type classification ───────────────────────────────────────────────────

_PAGE_TYPE_PATTERNS: list[tuple[str, str]] = [
    (r"/blog", "blog"),
    (r"/news", "blog"),
    (r"/insights", "blog"),
    (r"/resources", "blog"),
    (r"/about", "about"),
    (r"/company", "about"),
    (r"/team", "about"),
    (r"/product", "product"),
    (r"/platform", "product"),
    (r"/features", "product"),
    (r"/solutions", "product"),
    (r"/pricing", "pricing"),
    (r"/plans", "pricing"),
    (r"/customers", "customers"),
    (r"/case-studies", "customers"),
    (r"/testimonials", "customers"),
    (r"/careers", "careers"),
    (r"/jobs", "careers"),
    (r"/work-with-us", "careers"),
]


def classify_page_type(url: str) -> str:
    """Classify a URL path into a page type category.

    Returns one of: blog, about, product, pricing, customers, careers,
    homepage, other.
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip("/").lower()

    if path == "" or path == "/":
        return "homepage"

    for pattern, page_type in _PAGE_TYPE_PATTERNS:
        if re.search(pattern, path):
            return page_type

    return "other"


# ── Rate-limited fetcher ───────────────────────────────────────────────────────

_domain_last_fetch: dict[str, float] = {}
_domain_lock = threading.Lock()
_RATE_LIMIT_SECONDS = 1.0  # 1 request per second per domain

_USER_AGENT = "CIBot/1.0"


def _fetch_via_curl(url: str, timeout: int = 15) -> Optional[str]:
    """Fallback fetcher using system curl for TLS 1.3 sites."""
    try:
        result = subprocess.run(
            ["curl", "-sL", "-A", _USER_AGENT, "--max-time", str(timeout), url],
            capture_output=True, text=True, timeout=timeout + 5,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout
        logger.debug("curl fallback failed for %s: exit %d", url, result.returncode)
        return None
    except Exception as exc:
        logger.debug("curl fallback error for %s: %s", url, exc)
        return None


def _fetch(url: str, timeout: int = 15) -> Optional[str]:
    """GET url with rate limiting (1 req/sec per domain).

    Returns response text or None on any failure.
    User-Agent: CIBot/1.0.
    """
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        with _domain_lock:
            now = time.monotonic()
            last = _domain_last_fetch.get(domain, 0.0)
            wait = _RATE_LIMIT_SECONDS - (now - last)
            if wait > 0:
                time.sleep(wait)
            _domain_last_fetch[domain] = time.monotonic()

        resp = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": _USER_AGENT},
            allow_redirects=True,
        )
        resp.raise_for_status()
        return resp.text
    except requests.exceptions.SSLError:
        # Fall back to curl for hosts whose TLS version exceeds the
        # system's OpenSSL/LibreSSL capabilities (Python 3.9 + LibreSSL
        # 2.8 can't negotiate TLS 1.3 which some modern sites require).
        return _fetch_via_curl(url, timeout)
    except Exception as exc:
        logger.debug("_fetch failed for %s: %s", url, exc)
        return None


# ── Sitemap fetcher ────────────────────────────────────────────────────────────

def fetch_sitemap(base_url: str) -> list[dict]:
    """Fetch and parse sitemap.xml for a given base URL.

    Returns list of {"loc": url, "lastmod": date_or_none}.
    """
    base = base_url.rstrip("/")
    sitemap_url = f"{base}/sitemap.xml"
    xml_text = _fetch(sitemap_url)
    if not xml_text:
        return []
    return parse_sitemap_xml(xml_text)


# ── Blog link crawler (fallback) ───────────────────────────────────────────────

_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)


def _extract_links(html: str, base_url: str) -> list[str]:
    """Extract all href links from HTML, resolved against base_url."""
    links: list[str] = []
    for match in _HREF_RE.finditer(html):
        href = match.group(1).strip()
        if href.startswith("#") or href.startswith("mailto:") or href.startswith("javascript:"):
            continue
        full = urljoin(base_url, href)
        links.append(full)
    return links


def _same_domain(url: str, base_url: str) -> bool:
    """Return True if url is on the same domain as base_url."""
    return urlparse(url).netloc.lower() == urlparse(base_url).netloc.lower()


# ── Full competitor ingestion ──────────────────────────────────────────────────

_KNOWN_PATHS = ["/about", "/product", "/platform", "/customers", "/pricing"]
_BLOG_PATHS = ["/blog", "/news", "/insights"]


def ingest_competitor(slug: str, base_url: str) -> int:
    """Orchestrate full competitor ingestion.

    Strategy:
    1. Try sitemap first.
       - Separate blog posts from other pages.
       - Fetch up to 25 blog posts (most recent by lastmod).
       - Fetch all non-blog sitemap pages.
    2. If no sitemap, fall back to:
       - Homepage + known paths (/about, /product, /platform, /customers, /pricing).
       - One-level-deep crawl of the homepage for blog links.

    Stores pages via save_page(). Returns count of pages stored.
    """
    base = base_url.rstrip("/")
    count = 0

    sitemap_entries = fetch_sitemap(base)

    if sitemap_entries:
        blog_entries = []
        other_entries = []

        for entry in sitemap_entries:
            url = entry["loc"]
            if not _same_domain(url, base):
                continue
            ptype = classify_page_type(url)
            if ptype == "blog":
                blog_entries.append(entry)
            else:
                other_entries.append(entry)

        # Sort blog entries by lastmod descending (None sorts last)
        blog_entries.sort(
            key=lambda e: e["lastmod"] or "",
            reverse=True,
        )
        blog_entries = blog_entries[:25]

        # Fetch blog posts
        for entry in blog_entries:
            url = entry["loc"]
            html = _fetch(url)
            if html is None:
                continue
            text = extract_text_from_html(html)
            if len(text.strip()) < 200:
                logger.debug("Skipping %s — only %d chars (SPA shell)", url, len(text.strip()))
                continue
            text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
            save_page(slug, url, "blog", content=text, content_hash=text_hash, lastmod=entry["lastmod"])
            count += 1

        # Fetch non-blog pages
        for entry in other_entries:
            url = entry["loc"]
            ptype = classify_page_type(url)
            html = _fetch(url)
            if html is None:
                continue
            text = extract_text_from_html(html)
            if len(text.strip()) < 200:
                logger.debug("Skipping %s — only %d chars (SPA shell)", url, len(text.strip()))
                continue
            text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
            save_page(slug, url, ptype, content=text, content_hash=text_hash, lastmod=entry["lastmod"])
            count += 1

    else:
        # Fallback: homepage + known paths
        pages_to_fetch = [base] + [f"{base}{p}" for p in _KNOWN_PATHS]

        # Crawl homepage for blog links (one level deep)
        homepage_html = _fetch(base)
        if homepage_html:
            links = _extract_links(homepage_html, base)
            for link in links:
                if not _same_domain(link, base):
                    continue
                if classify_page_type(link) == "blog":
                    pages_to_fetch.append(link)

        seen: set[str] = set()
        for url in pages_to_fetch:
            if url in seen:
                continue
            seen.add(url)

            ptype = classify_page_type(url)
            if url == base or url == base + "/":
                ptype = "homepage"

            # For homepage we already have the HTML; avoid re-fetching
            if url == base and homepage_html is not None:
                html = homepage_html
            else:
                html = _fetch(url)

            if html is None:
                continue
            text = extract_text_from_html(html)
            if len(text.strip()) < 200:
                logger.debug("Skipping %s — only %d chars (SPA shell)", url, len(text.strip()))
                continue
            text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
            save_page(slug, url, ptype, content=text, content_hash=text_hash)
            count += 1

    update_last_ingested(slug)
    return count


# ── Exa news search ────────────────────────────────────────────────────────────

def search_news(competitor_name: str, competitor_slug: str) -> int:
    """Search for recent news/funding announcements via Exa.

    Requires EXA_API_KEY environment variable. Skips gracefully if not set.
    Returns count of news items stored.
    """
    api_key = os.environ.get("EXA_API_KEY", "").strip()
    if not api_key:
        logger.info("EXA_API_KEY not set — skipping Exa news search for %s", competitor_slug)
        return 0

    try:
        from exa_py import Exa  # type: ignore

        exa = Exa(api_key=api_key)
        query = f"{competitor_name} funding announcement product launch"

        results = exa.search_and_contents(
            query,
            num_results=10,
            use_autoprompt=True,
            text=True,
        )

        count = 0
        for result in results.results:
            url = getattr(result, "url", None) or ""
            title = getattr(result, "title", None) or ""
            published_at = getattr(result, "published_date", None) or ""
            # Trim published_at to date portion if it's an ISO timestamp
            if published_at and "T" in published_at:
                published_at = published_at.split("T")[0]
            snippet = ""
            text_content = getattr(result, "text", None) or ""
            if text_content:
                snippet = text_content[:500]

            source = urlparse(url).netloc if url else ""

            if not url:
                continue

            save_news(
                competitor_slug,
                title=title,
                url=url,
                source=source,
                published_at=published_at or None,
                snippet=snippet or None,
            )
            count += 1

        return count

    except Exception as exc:
        logger.warning("Exa news search failed for %s: %s", competitor_slug, exc)
        return 0
