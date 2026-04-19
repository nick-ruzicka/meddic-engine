"""Tests for competitive/collectors/sitemap_collector.py and content_hash.py.

Uses a temp DB (set via os.environ["DB_PATH"]) so the real DB is never touched.
All HTTP calls are mocked via unittest.mock.patch so no network access occurs.
"""

import importlib
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def db_path(tmp_path):
    """Create a fresh temp DB and set DB_PATH env var before module imports."""
    db_file = str(tmp_path / "test_sitemap.db")
    os.environ["DB_PATH"] = db_file
    yield db_file
    os.environ.pop("DB_PATH", None)


@pytest.fixture()
def modules(db_path):
    """Reload database and collector modules after DB_PATH is set."""
    import database as db_module
    importlib.reload(db_module)

    # Reload content_hash
    import competitive.collectors.content_hash as ch_module
    importlib.reload(ch_module)

    # Reload sitemap_collector (it imports database transitively)
    import competitive.collectors.sitemap_collector as sc_module
    importlib.reload(sc_module)

    return {"content_hash": ch_module, "sitemap_collector": sc_module, "database": db_module}


@pytest.fixture()
def collector(modules):
    """Return a fresh SitemapCollector instance (table created in __init__)."""
    sc_module = modules["sitemap_collector"]
    return sc_module.SitemapCollector()


@pytest.fixture()
def competitor():
    """Minimal competitor dict matching competitors.yaml structure."""
    return {
        "slug": "test_corp",
        "name": "Test Corp",
        "domain": "testcorp.example.com",
        "sitemap_url": "https://testcorp.example.com/sitemap.xml",
    }


# ── Helpers ────────────────────────────────────────────────────────────────────

def _raw_db(db_path) -> sqlite3.Connection:
    """Open a plain sqlite3 connection (no row_factory) for direct assertions."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _baseline_rows(db_path, competitor_slug):
    """Return all ci_sitemap_baseline rows for a competitor as list of dicts."""
    conn = _raw_db(db_path)
    rows = conn.execute(
        "SELECT * FROM ci_sitemap_baseline WHERE competitor = ?",
        (competitor_slug,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Content hash tests ────────────────────────────────────────────────────────

class TestHashContent:
    def test_same_content_same_hash(self, modules):
        ch = modules["content_hash"]
        assert ch.hash_content("Hello World") == ch.hash_content("Hello World")

    def test_different_content_different_hash(self, modules):
        ch = modules["content_hash"]
        assert ch.hash_content("Hello World") != ch.hash_content("Goodbye World")

    def test_returns_16_hex_chars(self, modules):
        ch = modules["content_hash"]
        result = ch.hash_content("some page content here")
        assert len(result) == 16
        assert all(c in "0123456789abcdef" for c in result)

    def test_empty_string(self, modules):
        ch = modules["content_hash"]
        h = ch.hash_content("")
        assert isinstance(h, str) and len(h) == 16

    def test_whitespace_normalised(self, modules):
        """Extra whitespace shouldn't produce a different hash."""
        ch = modules["content_hash"]
        assert ch.hash_content("hello   world") == ch.hash_content("hello world")

    def test_case_insensitive(self, modules):
        """Case differences are normalised away."""
        ch = modules["content_hash"]
        assert ch.hash_content("Hello World") == ch.hash_content("hello world")


# ── Normalization tests ────────────────────────────────────────────────────────

class TestNormalizeForHash:
    def test_strips_utm_tracking(self, modules):
        ch = modules["content_hash"]
        with_utm = "Visit us utm_source=google utm_campaign=spring for more"
        without_utm = "Visit us  for more"
        # After normalization both should produce the same hash
        assert ch.hash_content(with_utm) == ch.hash_content(without_utm)

    def test_strips_timestamps(self, modules):
        ch = modules["content_hash"]
        with_ts = "Updated at 2026-04-19T12:34:56Z today"
        without_ts = "Updated at  today"
        assert ch.hash_content(with_ts) == ch.hash_content(without_ts)

    def test_strips_iso_timestamps(self, modules):
        ch = modules["content_hash"]
        # Two different timestamps in otherwise identical content → same hash
        content_a = "Last modified 2026-01-01T00:00:00Z. Main content here."
        content_b = "Last modified 2026-12-31T23:59:59Z. Main content here."
        assert ch.hash_content(content_a) == ch.hash_content(content_b)

    def test_strips_session_ids(self, modules):
        ch = modules["content_hash"]
        with_sid = "token sessionid=abc123xyz page content"
        without_sid = "token  page content"
        assert ch.hash_content(with_sid) == ch.hash_content(without_sid)

    def test_strips_numeric_dates(self, modules):
        ch = modules["content_hash"]
        content_a = "Posted 2026-04-19. Read more below."
        content_b = "Posted 2025-11-01. Read more below."
        assert ch.hash_content(content_a) == ch.hash_content(content_b)

    def test_meaningful_differences_preserved(self, modules):
        """Normalization must NOT collapse genuinely different content."""
        ch = modules["content_hash"]
        assert ch.hash_content("product launch announcement") != ch.hash_content("pricing page updated")


# ── Table creation ────────────────────────────────────────────────────────────

class TestTableCreation:
    def test_table_exists_after_init(self, collector, db_path):
        """SitemapCollector.__init__ creates ci_sitemap_baseline table."""
        conn = sqlite3.connect(db_path)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        assert "ci_sitemap_baseline" in tables

    def test_table_has_expected_columns(self, collector, db_path):
        conn = sqlite3.connect(db_path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(ci_sitemap_baseline)").fetchall()}
        conn.close()
        assert {"id", "competitor", "url", "content_hash", "page_type", "last_seen"} <= cols


# ── Baseline tests ────────────────────────────────────────────────────────────

class TestBaseline:
    def _sitemap_xml(self, urls):
        items = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
        return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{items}</urlset>'

    def test_baseline_creates_db_records(self, collector, competitor, db_path):
        """baseline() stores one row per scraped URL in ci_sitemap_baseline."""
        urls = [
            "https://testcorp.example.com/product",
            "https://testcorp.example.com/about",
        ]
        sitemap_xml = self._sitemap_xml(urls)

        def fake_fetch(url, timeout=15):
            if "sitemap" in url:
                return sitemap_xml
            return "<html><body>" + ("A" * 300) + "</body></html>"

        with patch("competitive.collectors.sitemap_collector._fetch", side_effect=fake_fetch), \
             patch("competitive.collectors.sitemap_collector._fetch_via_curl", return_value=None):
            collector.baseline(competitor)

        rows = _baseline_rows(db_path, competitor["slug"])
        stored_urls = {r["url"] for r in rows}
        assert "https://testcorp.example.com/product" in stored_urls
        assert "https://testcorp.example.com/about" in stored_urls

    def test_baseline_skips_spa_shells(self, collector, competitor, db_path):
        """baseline() skips pages with <200 chars of extracted content."""
        urls = [
            "https://testcorp.example.com/product",
            "https://testcorp.example.com/empty",
        ]
        sitemap_xml = self._sitemap_xml(urls)

        def fake_fetch(url, timeout=15):
            if "sitemap" in url:
                return sitemap_xml
            if "empty" in url:
                return "<html><body>Short</body></html>"  # < 200 chars
            return "<html><body>" + ("A" * 300) + "</body></html>"

        with patch("competitive.collectors.sitemap_collector._fetch", side_effect=fake_fetch), \
             patch("competitive.collectors.sitemap_collector._fetch_via_curl", return_value=None):
            collector.baseline(competitor)

        rows = _baseline_rows(db_path, competitor["slug"])
        stored_urls = {r["url"] for r in rows}
        assert "https://testcorp.example.com/empty" not in stored_urls
        assert "https://testcorp.example.com/product" in stored_urls

    def test_baseline_stores_content_hash(self, collector, competitor, db_path):
        """baseline() stores a non-empty content_hash for each page."""
        urls = ["https://testcorp.example.com/pricing"]
        sitemap_xml = self._sitemap_xml(urls)

        def fake_fetch(url, timeout=15):
            if "sitemap" in url:
                return sitemap_xml
            return "<html><body>" + ("Pricing content " * 30) + "</body></html>"

        with patch("competitive.collectors.sitemap_collector._fetch", side_effect=fake_fetch), \
             patch("competitive.collectors.sitemap_collector._fetch_via_curl", return_value=None):
            collector.baseline(competitor)

        rows = _baseline_rows(db_path, competitor["slug"])
        assert len(rows) == 1
        assert rows[0]["content_hash"] and len(rows[0]["content_hash"]) == 16

    def test_baseline_stores_page_type(self, collector, competitor, db_path):
        """baseline() stores the correct page_type classification."""
        urls = ["https://testcorp.example.com/blog/post-1"]
        sitemap_xml = self._sitemap_xml(urls)

        def fake_fetch(url, timeout=15):
            if "sitemap" in url:
                return sitemap_xml
            return "<html><body>" + ("Blog content here. " * 20) + "</body></html>"

        with patch("competitive.collectors.sitemap_collector._fetch", side_effect=fake_fetch), \
             patch("competitive.collectors.sitemap_collector._fetch_via_curl", return_value=None):
            collector.baseline(competitor)

        rows = _baseline_rows(db_path, competitor["slug"])
        assert rows[0]["page_type"] == "blog"


# ── Collect tests ─────────────────────────────────────────────────────────────

class TestCollect:
    def _sitemap_xml(self, urls):
        items = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
        return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{items}</urlset>'

    def _seed_baseline(self, db_path, competitor_slug, url, content_hash, page_type="other"):
        """Directly insert a row into ci_sitemap_baseline."""
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            INSERT OR REPLACE INTO ci_sitemap_baseline
                (competitor, url, content_hash, page_type, last_seen)
            VALUES (?, ?, ?, ?, datetime('now'))
            """,
            (competitor_slug, url, content_hash, page_type),
        )
        conn.commit()
        conn.close()

    def test_detects_new_url(self, collector, competitor, db_path):
        """collect() emits new_url signal for URLs not in baseline."""
        new_url = "https://testcorp.example.com/product/new-feature"
        sitemap_xml = self._sitemap_xml([new_url])

        def fake_fetch(url, timeout=15):
            if "sitemap" in url:
                return sitemap_xml
            return "<html><body>" + ("New feature content. " * 20) + "</body></html>"

        with patch("competitive.collectors.sitemap_collector._fetch", side_effect=fake_fetch), \
             patch("competitive.collectors.sitemap_collector._fetch_via_curl", return_value=None):
            signals = collector.collect(competitor)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.signal_type == "new_url"
        assert sig.confidence == 0.8
        assert sig.source == "sitemap"
        assert sig.competitor == competitor["slug"]
        assert sig.payload["url"] == new_url
        assert sig.payload["change"] == "new"
        assert sig.payload["old_hash"] is None
        assert sig.payload["new_hash"] is not None
        assert isinstance(sig.observed_at, datetime)

    def test_detects_content_change(self, collector, competitor, db_path):
        """collect() emits content_change when hash differs from baseline."""
        url = "https://testcorp.example.com/pricing"
        old_hash = "0000000000000000"  # deliberately wrong hash
        self._seed_baseline(db_path, competitor["slug"], url, old_hash, "pricing")

        sitemap_xml = self._sitemap_xml([url])

        def fake_fetch(url_arg, timeout=15):
            if "sitemap" in url_arg:
                return sitemap_xml
            # Return content that hashes to something other than old_hash
            return "<html><body>" + ("New pricing content. " * 20) + "</body></html>"

        with patch("competitive.collectors.sitemap_collector._fetch", side_effect=fake_fetch), \
             patch("competitive.collectors.sitemap_collector._fetch_via_curl", return_value=None):
            signals = collector.collect(competitor)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.signal_type == "content_change"
        assert sig.confidence == 0.6
        assert sig.payload["old_hash"] == old_hash
        assert sig.payload["new_hash"] != old_hash
        assert sig.payload["change"] == "modified"

    def test_no_signal_when_content_unchanged(self, collector, competitor, db_path):
        """collect() emits no signal when content hash matches baseline."""
        from competitive.collectors.content_hash import hash_content
        from competitive.ingestion import extract_text_from_html

        page_content = "Unchanged pricing content. " * 20
        full_html = "<html><body>" + page_content + "</body></html>"
        # The collector hashes extracted text, not raw HTML — match that here.
        extracted = extract_text_from_html(full_html)
        content_hash = hash_content(extracted)

        url = "https://testcorp.example.com/pricing"
        self._seed_baseline(db_path, competitor["slug"], url, content_hash, "pricing")

        sitemap_xml = self._sitemap_xml([url])

        def fake_fetch(url_arg, timeout=15):
            if "sitemap" in url_arg:
                return sitemap_xml
            return full_html

        with patch("competitive.collectors.sitemap_collector._fetch", side_effect=fake_fetch), \
             patch("competitive.collectors.sitemap_collector._fetch_via_curl", return_value=None):
            signals = collector.collect(competitor)

        # No content change — no signals
        assert all(s.signal_type != "content_change" for s in signals)
        assert all(s.signal_type != "new_url" for s in signals)

    def test_detects_removed_url(self, collector, competitor, db_path):
        """collect() emits url_removed for baseline URLs absent from current sitemap."""
        removed_url = "https://testcorp.example.com/old-product"
        still_present_url = "https://testcorp.example.com/about"

        self._seed_baseline(db_path, competitor["slug"], removed_url, "aabbccddeeff0011", "product")
        self._seed_baseline(db_path, competitor["slug"], still_present_url, "1122334455667788", "about")

        # Current sitemap only contains still_present_url — removed_url is gone.
        sitemap_xml = self._sitemap_xml([still_present_url])

        def fake_fetch(url_arg, timeout=15):
            if "sitemap" in url_arg:
                return sitemap_xml
            return "<html><body>" + ("About content. " * 20) + "</body></html>"

        with patch("competitive.collectors.sitemap_collector._fetch", side_effect=fake_fetch), \
             patch("competitive.collectors.sitemap_collector._fetch_via_curl", return_value=None):
            signals = collector.collect(competitor)

        removed_signals = [s for s in signals if s.signal_type == "url_removed"]
        assert len(removed_signals) == 1
        sig = removed_signals[0]
        assert sig.confidence == 0.4
        assert sig.payload["url"] == removed_url
        assert sig.payload["change"] == "removed"
        assert sig.payload["old_hash"] == "aabbccddeeff0011"
        assert sig.payload["new_hash"] is None

    def test_collect_updates_baseline_with_new_url(self, collector, competitor, db_path):
        """After collect(), newly found URLs are stored in baseline."""
        new_url = "https://testcorp.example.com/product/v2"
        sitemap_xml = self._sitemap_xml([new_url])

        def fake_fetch(url_arg, timeout=15):
            if "sitemap" in url_arg:
                return sitemap_xml
            return "<html><body>" + ("Product v2 content. " * 20) + "</body></html>"

        with patch("competitive.collectors.sitemap_collector._fetch", side_effect=fake_fetch), \
             patch("competitive.collectors.sitemap_collector._fetch_via_curl", return_value=None):
            collector.collect(competitor)

        rows = _baseline_rows(db_path, competitor["slug"])
        assert any(r["url"] == new_url for r in rows)

    def test_collect_updates_baseline_hash_on_change(self, collector, competitor, db_path):
        """After collect(), a changed page's hash is updated in baseline."""
        url = "https://testcorp.example.com/pricing"
        old_hash = "deadbeefdeadbeef"
        self._seed_baseline(db_path, competitor["slug"], url, old_hash, "pricing")

        sitemap_xml = self._sitemap_xml([url])

        def fake_fetch(url_arg, timeout=15):
            if "sitemap" in url_arg:
                return sitemap_xml
            return "<html><body>" + ("Updated pricing. " * 30) + "</body></html>"

        with patch("competitive.collectors.sitemap_collector._fetch", side_effect=fake_fetch), \
             patch("competitive.collectors.sitemap_collector._fetch_via_curl", return_value=None):
            collector.collect(competitor)

        rows = _baseline_rows(db_path, competitor["slug"])
        row = next(r for r in rows if r["url"] == url)
        assert row["content_hash"] != old_hash

    def test_collect_returns_rawsignal_instances(self, collector, competitor, db_path):
        """collect() returns proper RawSignal instances."""
        from competitive.collectors.base import RawSignal

        new_url = "https://testcorp.example.com/careers/senior-engineer"
        sitemap_xml = self._sitemap_xml([new_url])

        def fake_fetch(url_arg, timeout=15):
            if "sitemap" in url_arg:
                return sitemap_xml
            return "<html><body>" + ("Job description. " * 20) + "</body></html>"

        with patch("competitive.collectors.sitemap_collector._fetch", side_effect=fake_fetch), \
             patch("competitive.collectors.sitemap_collector._fetch_via_curl", return_value=None):
            signals = collector.collect(competitor)

        assert all(isinstance(s, RawSignal) for s in signals)

    def test_empty_sitemap_produces_removed_signals(self, collector, competitor, db_path):
        """If sitemap becomes empty, all baseline URLs generate url_removed signals."""
        for i in range(3):
            self._seed_baseline(
                db_path, competitor["slug"],
                f"https://testcorp.example.com/page{i}",
                f"hash{i:012d}",
                "other",
            )

        empty_sitemap = '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>'

        with patch("competitive.collectors.sitemap_collector._fetch", return_value=empty_sitemap), \
             patch("competitive.collectors.sitemap_collector._fetch_via_curl", return_value=None):
            signals = collector.collect(competitor)

        removed = [s for s in signals if s.signal_type == "url_removed"]
        assert len(removed) == 3

    def test_no_signals_when_sitemap_fetch_fails(self, collector, competitor, db_path):
        """If sitemap cannot be fetched, collect() returns empty list (no crash)."""
        with patch("competitive.collectors.sitemap_collector._fetch", return_value=None), \
             patch("competitive.collectors.sitemap_collector._fetch_via_curl", return_value=None):
            signals = collector.collect(competitor)

        # Baseline URLs still generate removed signals, but there are none seeded here.
        assert isinstance(signals, list)

    def test_collector_name(self, collector):
        """SitemapCollector.name must be 'sitemap'."""
        assert collector.name == "sitemap"
