"""Tests for competitive/models.py — schema creation, idempotency, upsert, and get_all.

Tests use a temporary SQLite DB file via os.environ["DB_PATH"] set BEFORE
importing from competitive.models, so they never touch the real DB.
"""

import os
import sys
import json
import tempfile
import importlib

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture()
def db_path(tmp_path):
    """Create a fresh temp DB file and set DB_PATH env var before module import."""
    db_file = str(tmp_path / "test_competitive.db")
    os.environ["DB_PATH"] = db_file
    yield db_file
    # Cleanup env so other tests are unaffected
    os.environ.pop("DB_PATH", None)


@pytest.fixture()
def models(db_path):
    """Import (or re-import) competitive.models after DB_PATH is set.

    database.py reads DB_PATH at module-load time, so we must reload it
    (and then competitive.models) each fixture call so get_db() points at
    the per-test temp file.
    """
    import importlib
    import database as db_module

    # Reload database so DB_PATH picks up the new env var.
    importlib.reload(db_module)

    import competitive.models as m
    # Reload models so its `get_db` binding comes from the freshly-reloaded database.
    importlib.reload(m)
    return m


# ── Schema creation ────────────────────────────────────────────────────────────

class TestInitCompetitiveDb:
    def test_creates_tables(self, models):
        """init_competitive_db() creates all 6 expected tables."""
        models.init_competitive_db()
        import sqlite3
        conn = sqlite3.connect(os.environ["DB_PATH"])
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        expected = {
            "competitors",
            "competitor_pages",
            "competitor_news",
            "competitor_briefs",
            "competitor_trajectories",
            "competitor_signals",
        }
        assert expected <= tables, f"Missing tables: {expected - tables}"

    def test_idempotent(self, models):
        """Calling init_competitive_db() twice does not raise."""
        models.init_competitive_db()
        models.init_competitive_db()  # should not raise

    def test_competitors_columns(self, models):
        """competitors table has all required columns."""
        models.init_competitive_db()
        import sqlite3
        conn = sqlite3.connect(os.environ["DB_PATH"])
        cols = {row[1] for row in conn.execute("PRAGMA table_info(competitors)").fetchall()}
        conn.close()
        required = {"slug", "name", "url", "tier", "positioning", "url_ok", "last_ingested"}
        assert required <= cols

    def test_competitor_pages_columns(self, models):
        models.init_competitive_db()
        import sqlite3
        conn = sqlite3.connect(os.environ["DB_PATH"])
        cols = {row[1] for row in conn.execute("PRAGMA table_info(competitor_pages)").fetchall()}
        conn.close()
        required = {"id", "competitor_slug", "url", "page_type", "lastmod", "content", "fetched_at"}
        assert required <= cols

    def test_competitor_news_columns(self, models):
        models.init_competitive_db()
        import sqlite3
        conn = sqlite3.connect(os.environ["DB_PATH"])
        cols = {row[1] for row in conn.execute("PRAGMA table_info(competitor_news)").fetchall()}
        conn.close()
        required = {"id", "competitor_slug", "title", "url", "source", "published_at", "snippet", "fetched_at"}
        assert required <= cols

    def test_competitor_briefs_columns(self, models):
        models.init_competitive_db()
        import sqlite3
        conn = sqlite3.connect(os.environ["DB_PATH"])
        cols = {row[1] for row in conn.execute("PRAGMA table_info(competitor_briefs)").fetchall()}
        conn.close()
        required = {"id", "competitor_slug", "brief_json", "generated_at", "model"}
        assert required <= cols

    def test_competitor_trajectories_columns(self, models):
        models.init_competitive_db()
        import sqlite3
        conn = sqlite3.connect(os.environ["DB_PATH"])
        cols = {row[1] for row in conn.execute("PRAGMA table_info(competitor_trajectories)").fetchall()}
        conn.close()
        required = {"id", "competitor_slug", "trajectory_json", "generated_at", "model"}
        assert required <= cols

    def test_competitor_signals_columns(self, models):
        models.init_competitive_db()
        import sqlite3
        conn = sqlite3.connect(os.environ["DB_PATH"])
        cols = {row[1] for row in conn.execute("PRAGMA table_info(competitor_signals)").fetchall()}
        conn.close()
        required = {"id", "competitor_slug", "signal_type", "summary", "relevance", "category", "source_url", "detected_at"}
        assert required <= cols


# ── Competitor CRUD ────────────────────────────────────────────────────────────

class TestUpsertCompetitor:
    def test_insert_new_competitor(self, models):
        models.init_competitive_db()
        models.upsert_competitor("cursor", "Cursor", "https://cursor.sh", tier=1, positioning="AI code editor")
        row = models.get_competitor("cursor")
        assert row is not None
        assert row["slug"] == "cursor"
        assert row["name"] == "Cursor"
        assert row["url"] == "https://cursor.sh"
        assert row["tier"] == 1
        assert row["positioning"] == "AI code editor"

    def test_upsert_updates_existing(self, models):
        models.init_competitive_db()
        models.upsert_competitor("cursor", "Cursor", "https://cursor.sh", tier=1, positioning="AI code editor")
        models.upsert_competitor("cursor", "Cursor AI", "https://cursor.sh", tier=2, positioning="Updated positioning")
        row = models.get_competitor("cursor")
        assert row["name"] == "Cursor AI"
        assert row["tier"] == 2
        assert row["positioning"] == "Updated positioning"

    def test_upsert_defaults(self, models):
        """url_ok defaults to 1 when not specified."""
        models.init_competitive_db()
        models.upsert_competitor("notion", "Notion", "https://notion.so")
        row = models.get_competitor("notion")
        assert row["url_ok"] == 1

    def test_get_competitor_missing(self, models):
        models.init_competitive_db()
        row = models.get_competitor("nonexistent-slug")
        assert row is None

    def test_get_all_competitors_empty(self, models):
        models.init_competitive_db()
        result = models.get_all_competitors()
        assert result == []

    def test_get_all_competitors_returns_all(self, models):
        models.init_competitive_db()
        models.upsert_competitor("cursor", "Cursor", "https://cursor.sh", tier=1)
        models.upsert_competitor("notion", "Notion", "https://notion.so", tier=2)
        result = models.get_all_competitors()
        slugs = [r["slug"] for r in result]
        assert "cursor" in slugs
        assert "notion" in slugs
        assert len(result) == 2


# ── Pages ──────────────────────────────────────────────────────────────────────

class TestPages:
    def _setup(self, models):
        models.init_competitive_db()
        models.upsert_competitor("cursor", "Cursor", "https://cursor.sh")

    def test_save_page_inserts(self, models):
        self._setup(models)
        models.save_page("cursor", "https://cursor.sh/pricing", "pricing", content="<html>", lastmod="2024-01-01")
        pages = models.get_pages("cursor")
        assert len(pages) == 1
        assert pages[0]["url"] == "https://cursor.sh/pricing"
        assert pages[0]["page_type"] == "pricing"
        assert pages[0]["content"] == "<html>"

    def test_save_page_upserts_on_duplicate(self, models):
        self._setup(models)
        models.save_page("cursor", "https://cursor.sh/pricing", "pricing", content="v1")
        models.save_page("cursor", "https://cursor.sh/pricing", "pricing", content="v2")
        pages = models.get_pages("cursor")
        assert len(pages) == 1
        assert pages[0]["content"] == "v2"

    def test_get_pages_filters_by_slug(self, models):
        self._setup(models)
        models.upsert_competitor("notion", "Notion", "https://notion.so")
        models.save_page("cursor", "https://cursor.sh/pricing", "pricing", content="cursor")
        models.save_page("notion", "https://notion.so/pricing", "pricing", content="notion")
        cursor_pages = models.get_pages("cursor")
        assert len(cursor_pages) == 1
        assert cursor_pages[0]["content"] == "cursor"


# ── News ───────────────────────────────────────────────────────────────────────

class TestNews:
    def _setup(self, models):
        models.init_competitive_db()
        models.upsert_competitor("cursor", "Cursor", "https://cursor.sh")

    def test_save_news_inserts(self, models):
        self._setup(models)
        models.save_news(
            "cursor",
            title="Cursor raises $100M",
            url="https://techcrunch.com/cursor-100m",
            source="TechCrunch",
            published_at="2024-03-01",
            snippet="Cursor AI raises...",
        )
        news = models.get_news("cursor")
        assert len(news) == 1
        assert news[0]["title"] == "Cursor raises $100M"

    def test_save_news_upserts_on_duplicate_url(self, models):
        self._setup(models)
        url = "https://techcrunch.com/cursor-100m"
        models.save_news("cursor", title="Old title", url=url, source="TC", published_at="2024-03-01", snippet="old")
        models.save_news("cursor", title="New title", url=url, source="TC", published_at="2024-03-01", snippet="new")
        news = models.get_news("cursor")
        assert len(news) == 1
        assert news[0]["title"] == "New title"

    def test_get_news_filters_by_slug(self, models):
        self._setup(models)
        models.upsert_competitor("notion", "Notion", "https://notion.so")
        models.save_news("cursor", title="Cursor news", url="https://tc.com/c", source="TC", published_at="2024-01-01", snippet="")
        models.save_news("notion", title="Notion news", url="https://tc.com/n", source="TC", published_at="2024-01-01", snippet="")
        assert len(models.get_news("cursor")) == 1
        assert models.get_news("cursor")[0]["title"] == "Cursor news"


# ── Briefs ─────────────────────────────────────────────────────────────────────

class TestBriefs:
    def _setup(self, models):
        models.init_competitive_db()
        models.upsert_competitor("cursor", "Cursor", "https://cursor.sh")

    def test_save_and_get_latest_brief(self, models):
        self._setup(models)
        brief = {"summary": "Cursor is a strong competitor", "threats": ["pricing"]}
        models.save_brief("cursor", brief, model="claude-3-5-sonnet")
        result = models.get_latest_brief("cursor")
        assert result is not None
        loaded = json.loads(result["brief_json"])
        assert loaded["summary"] == "Cursor is a strong competitor"
        assert result["model"] == "claude-3-5-sonnet"

    def test_get_latest_brief_returns_most_recent(self, models):
        self._setup(models)
        models.save_brief("cursor", {"v": 1}, model="gpt-4")
        models.save_brief("cursor", {"v": 2}, model="claude-3-5-sonnet")
        result = models.get_latest_brief("cursor")
        assert json.loads(result["brief_json"])["v"] == 2

    def test_get_latest_brief_missing(self, models):
        self._setup(models)
        assert models.get_latest_brief("cursor") is None


# ── Trajectories ───────────────────────────────────────────────────────────────

class TestTrajectories:
    def _setup(self, models):
        models.init_competitive_db()
        models.upsert_competitor("cursor", "Cursor", "https://cursor.sh")

    def test_save_and_get_latest_trajectory(self, models):
        self._setup(models)
        trajectory = {"direction": "upmarket", "signals": ["enterprise pricing page"]}
        models.save_trajectory("cursor", trajectory, model="claude-3-5-sonnet")
        result = models.get_latest_trajectory("cursor")
        assert result is not None
        loaded = json.loads(result["trajectory_json"])
        assert loaded["direction"] == "upmarket"

    def test_get_latest_trajectory_returns_most_recent(self, models):
        self._setup(models)
        models.save_trajectory("cursor", {"v": 1}, model="gpt-4")
        models.save_trajectory("cursor", {"v": 2}, model="claude-3-5-sonnet")
        result = models.get_latest_trajectory("cursor")
        assert json.loads(result["trajectory_json"])["v"] == 2

    def test_get_latest_trajectory_missing(self, models):
        self._setup(models)
        assert models.get_latest_trajectory("cursor") is None


# ── Signals ────────────────────────────────────────────────────────────────────

class TestSignals:
    def _setup(self, models):
        models.init_competitive_db()
        models.upsert_competitor("cursor", "Cursor", "https://cursor.sh")

    def test_save_and_get_recent_signals(self, models):
        self._setup(models)
        models.save_signal(
            competitor_slug="cursor",
            signal_type="pricing_change",
            summary="Cursor dropped free tier",
            relevance=0.9,
            category="pricing",
            source_url="https://cursor.sh/pricing",
        )
        signals = models.get_recent_signals("cursor")
        assert len(signals) == 1
        assert signals[0]["signal_type"] == "pricing_change"
        assert signals[0]["summary"] == "Cursor dropped free tier"
        assert signals[0]["relevance"] == 0.9

    def test_get_recent_signals_limit(self, models):
        self._setup(models)
        for i in range(5):
            models.save_signal(
                competitor_slug="cursor",
                signal_type="update",
                summary=f"Signal {i}",
                relevance=0.5,
                category="product",
                source_url=f"https://cursor.sh/update/{i}",
            )
        signals = models.get_recent_signals("cursor", limit=3)
        assert len(signals) == 3

    def test_get_recent_signals_filters_by_slug(self, models):
        self._setup(models)
        models.upsert_competitor("notion", "Notion", "https://notion.so")
        models.save_signal("cursor", "update", "cursor signal", 0.8, "product", "https://cursor.sh")
        models.save_signal("notion", "update", "notion signal", 0.7, "product", "https://notion.so")
        cursor_signals = models.get_recent_signals("cursor")
        assert len(cursor_signals) == 1
        assert cursor_signals[0]["summary"] == "cursor signal"


# ── update_last_ingested ───────────────────────────────────────────────────────

class TestUpdateLastIngested:
    def test_update_last_ingested(self, models):
        models.init_competitive_db()
        models.upsert_competitor("cursor", "Cursor", "https://cursor.sh")
        models.update_last_ingested("cursor", "2024-03-15T12:00:00")
        row = models.get_competitor("cursor")
        assert row["last_ingested"] == "2024-03-15T12:00:00"


# ── Competitors list + seed ────────────────────────────────────────────────────

@pytest.fixture()
def competitors_mod(db_path):
    """Import (or re-import) competitive.competitors after DB_PATH is set."""
    import importlib
    import database as db_module

    importlib.reload(db_module)

    import competitive.models as m
    importlib.reload(m)

    import competitive.competitors as c
    importlib.reload(c)

    # init the schema so seed_competitors has tables to write to
    m.init_competitive_db()
    return c


class TestCompetitors:
    def test_v1_has_five_entries(self, competitors_mod):
        """COMPETITORS_V1 contains exactly 5 tier-1 tuples."""
        assert len(competitors_mod.COMPETITORS_V1) == 5

    def test_v1_slugs_are_correct(self, competitors_mod):
        """Each entry in COMPETITORS_V1 has the expected slug as first element."""
        expected_slugs = {"alphasense", "rogo", "f2", "blueflame", "keye"}
        actual_slugs = {entry[0] for entry in competitors_mod.COMPETITORS_V1}
        assert actual_slugs == expected_slugs

    def test_v1_all_tier_one(self, competitors_mod):
        """All COMPETITORS_V1 entries have tier == 1 (index 3)."""
        for entry in competitors_mod.COMPETITORS_V1:
            assert entry[3] == 1, f"{entry[0]} should be tier 1, got {entry[3]}"

    def test_v2_has_ten_entries(self, competitors_mod):
        """COMPETITORS_V2 contains exactly 10 entries."""
        assert len(competitors_mod.COMPETITORS_V2) == 10

    def test_seed_populates_db(self, competitors_mod):
        """seed_competitors() inserts all 5 v1 entries into the DB."""
        competitors_mod.seed_competitors()
        import competitive.models as m
        importlib.reload(m)
        rows = m.get_all_competitors()
        slugs = {row["slug"] for row in rows}
        expected = {"alphasense", "rogo", "f2", "blueflame", "keye"}
        assert expected <= slugs
        assert len(rows) == 5

    def test_seed_is_idempotent(self, competitors_mod):
        """Calling seed_competitors() twice still results in exactly 5 entries."""
        competitors_mod.seed_competitors()
        competitors_mod.seed_competitors()
        import competitive.models as m
        importlib.reload(m)
        rows = m.get_all_competitors()
        assert len(rows) == 5

    def test_seed_v2_adds_all_entries(self, competitors_mod):
        """seed_competitors(v2=True) seeds v1 + v2, totalling 15 entries."""
        competitors_mod.seed_competitors(v2=True)
        import competitive.models as m
        importlib.reload(m)
        rows = m.get_all_competitors()
        assert len(rows) == 15


# ── Ingestion ──────────────────────────────────────────────────────────────────

class TestIngestion:
    """Tests for competitive/ingestion.py — pure-logic functions only (no network)."""

    def test_parse_sitemap_xml_extracts_urls(self):
        """Valid sitemap XML with 2 URLs is parsed into 2 dicts with loc + lastmod."""
        from competitive.ingestion import parse_sitemap_xml

        xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://example.com/</loc>
    <lastmod>2024-01-15</lastmod>
  </url>
  <url>
    <loc>https://example.com/blog/post-1</loc>
    <lastmod>2024-03-20</lastmod>
  </url>
</urlset>"""

        result = parse_sitemap_xml(xml)
        assert len(result) == 2

        locs = [r["loc"] for r in result]
        assert "https://example.com/" in locs
        assert "https://example.com/blog/post-1" in locs

        # Verify lastmod values are present
        by_loc = {r["loc"]: r for r in result}
        assert by_loc["https://example.com/"]["lastmod"] == "2024-01-15"
        assert by_loc["https://example.com/blog/post-1"]["lastmod"] == "2024-03-20"

    def test_parse_sitemap_handles_empty(self):
        """Empty string, whitespace-only, and malformed XML all return []."""
        from competitive.ingestion import parse_sitemap_xml

        assert parse_sitemap_xml("") == []
        assert parse_sitemap_xml("   ") == []
        assert parse_sitemap_xml("not xml at all <<<") == []
        assert parse_sitemap_xml("<broken><xml>") == []

    def test_extract_text_strips_tags(self):
        """script tags (and content) are stripped; visible text is kept."""
        from competitive.ingestion import extract_text_from_html

        html = """<html>
<head><title>Page Title</title></head>
<body>
  <script>var x = 1; alert('hidden');</script>
  <style>body { color: red; }</style>
  <h1>Hello World</h1>
  <p>Some <strong>bold</strong> text here.</p>
  <noscript>No JS fallback hidden</noscript>
</body>
</html>"""

        result = extract_text_from_html(html)

        # Visible text should be present
        assert "Hello World" in result
        assert "Some" in result
        assert "bold" in result
        assert "text here" in result

        # Script/style content must NOT appear
        assert "var x = 1" not in result
        assert "alert" not in result
        assert "color: red" not in result
        # noscript content should be stripped
        assert "No JS fallback hidden" not in result

    def test_classify_page_type(self):
        """URL paths are classified into the correct page type."""
        from competitive.ingestion import classify_page_type

        # Blog variants
        assert classify_page_type("https://example.com/blog/my-post") == "blog"
        assert classify_page_type("https://example.com/blog") == "blog"
        assert classify_page_type("https://example.com/news/article") == "blog"

        # About variants
        assert classify_page_type("https://example.com/about") == "about"
        assert classify_page_type("https://example.com/about-us") == "about"
        assert classify_page_type("https://example.com/company") == "about"

        # Pricing
        assert classify_page_type("https://example.com/pricing") == "pricing"
        assert classify_page_type("https://example.com/plans") == "pricing"

        # Customers
        assert classify_page_type("https://example.com/customers") == "customers"
        assert classify_page_type("https://example.com/case-studies") == "customers"

        # Careers
        assert classify_page_type("https://example.com/careers") == "careers"
        assert classify_page_type("https://example.com/jobs") == "careers"

        # Homepage
        assert classify_page_type("https://example.com/") == "homepage"
        assert classify_page_type("https://example.com") == "homepage"

        # Other
        assert classify_page_type("https://example.com/some-random-page") == "other"
        assert classify_page_type("https://example.com/contact") == "other"


# ── Analysis ───────────────────────────────────────────────────────────────────

class TestAnalysis:
    """Tests for competitive/analysis.py — prompt builders and JSON parsers (no API calls)."""

    # ── build_brief_prompt ─────────────────────────────────────────────────────

    def test_build_brief_prompt_includes_competitor_name(self):
        """build_brief_prompt() includes the competitor name in the output."""
        from competitive.analysis import build_brief_prompt

        pages = [
            {"url": "https://example.com/pricing", "content": "Pricing content here", "page_type": "pricing"},
        ]
        news = [
            {"title": "Example raises $50M", "url": "https://tc.com/ex", "published_at": "2024-01-01", "snippet": "Big raise"},
        ]
        result = build_brief_prompt("ExampleCorp", pages, news)

        assert "ExampleCorp" in result
        assert "## Website pages" in result
        assert "## News and announcements" in result
        assert "https://example.com/pricing" in result
        assert "Example raises $50M" in result

    def test_build_brief_prompt_truncates_content(self):
        """build_brief_prompt() truncates page content to 3000 chars."""
        from competitive.analysis import build_brief_prompt

        long_content = "x" * 5000
        pages = [{"url": "https://example.com/", "content": long_content, "page_type": "homepage"}]
        result = build_brief_prompt("Acme", pages, [])

        # The content in the prompt should be at most 3000 chars of 'x'
        assert "x" * 3000 in result
        assert "x" * 3001 not in result

    def test_build_brief_prompt_empty_pages_and_news(self):
        """build_brief_prompt() handles empty pages and news gracefully."""
        from competitive.analysis import build_brief_prompt

        result = build_brief_prompt("Acme", [], [])
        assert "## Website pages" in result
        assert "## News and announcements" in result
        assert "no pages available" in result
        assert "no news available" in result

    # ── build_trajectory_prompt ────────────────────────────────────────────────

    def test_build_trajectory_prompt_chronological(self):
        """build_trajectory_prompt() sorts items oldest first (by date ascending)."""
        from competitive.analysis import build_trajectory_prompt

        pages = [
            {"url": "https://example.com/blog/new", "content": "new post", "page_type": "blog", "lastmod": "2024-12-01"},
            {"url": "https://example.com/blog/old", "content": "old post", "page_type": "blog", "lastmod": "2022-01-01"},
            {"url": "https://example.com/blog/mid", "content": "mid post", "page_type": "blog", "lastmod": "2023-06-15"},
        ]
        news = []
        result = build_trajectory_prompt("Acme", pages, news)

        pos_old = result.index("2022-01-01")
        pos_mid = result.index("2023-06-15")
        pos_new = result.index("2024-12-01")

        assert pos_old < pos_mid < pos_new, "Pages must appear oldest-first"

    def test_build_trajectory_prompt_unknown_dates_sort_first(self):
        """Items with unknown/missing dates sort before dated items."""
        from competitive.analysis import build_trajectory_prompt

        pages = [
            {"url": "https://example.com/blog/dated", "content": "dated post", "page_type": "blog", "lastmod": "2024-01-01"},
            {"url": "https://example.com/blog/no-date", "content": "no date post", "page_type": "blog", "lastmod": None},
        ]
        result = build_trajectory_prompt("Acme", pages, [])

        pos_no_date = result.index("example.com/blog/no-date")
        pos_dated = result.index("example.com/blog/dated")

        assert pos_no_date < pos_dated, "Unknown-date items must appear before dated items"

    def test_build_trajectory_prompt_filters_non_blog_pages(self):
        """build_trajectory_prompt() only includes blog-type pages."""
        from competitive.analysis import build_trajectory_prompt

        pages = [
            {"url": "https://example.com/blog/post", "content": "blog content", "page_type": "blog", "lastmod": "2024-01-01"},
            {"url": "https://example.com/pricing", "content": "pricing content", "page_type": "pricing", "lastmod": "2024-01-01"},
            {"url": "https://example.com/about", "content": "about content", "page_type": "about", "lastmod": "2024-01-01"},
        ]
        result = build_trajectory_prompt("Acme", pages, [])

        assert "blog content" in result
        assert "pricing content" not in result
        assert "about content" not in result

    # ── parse_brief_json ───────────────────────────────────────────────────────

    def test_parse_brief_json_valid(self):
        """parse_brief_json() parses valid JSON with all required fields."""
        from competitive.analysis import parse_brief_json

        data = {
            "positioning_self": "The AI platform for finance",
            "positioning_actual": "Document search for PE firms",
            "target_icp": "Private equity associates",
            "pricing_signals": "Enterprise pricing, no public pricing",
            "key_differentiation": "Fast document processing",
            "weakness_vs_": "Narrower document types",
            "strength_vs_": "Lower price point",
            "recent_moves": "Launched new dashboard",
            "threat_level": "medium",
            "threat_reasoning": "Strong in SMB, weak in enterprise",
        }
        result = parse_brief_json(json.dumps(data))
        assert result["threat_level"] == "medium"
        assert result["positioning_self"] == "The AI platform for finance"

    def test_parse_brief_json_extracts_from_markdown(self):
        """parse_brief_json() handles ```json...``` markdown wrapping."""
        from competitive.analysis import parse_brief_json

        data = {
            "positioning_self": "AI search",
            "positioning_actual": "Document AI",
            "target_icp": "Finance teams",
            "pricing_signals": "Unknown",
            "key_differentiation": "Speed",
            "weakness_vs_": "Less accurate",
            "strength_vs_": "Cheaper",
            "recent_moves": "New integrations",
            "threat_level": "low",
            "threat_reasoning": "Not enterprise focused",
        }
        wrapped = f"```json\n{json.dumps(data)}\n```"
        result = parse_brief_json(wrapped)
        assert result["threat_level"] == "low"

    def test_parse_brief_json_missing_fields_raises(self):
        """parse_brief_json() raises ValueError when required fields are missing."""
        from competitive.analysis import parse_brief_json

        incomplete = json.dumps({"positioning_self": "something"})
        with pytest.raises(ValueError, match="missing required fields"):
            parse_brief_json(incomplete)

    def test_parse_brief_json_invalid_json_raises(self):
        """parse_brief_json() raises json.JSONDecodeError on invalid JSON."""
        from competitive.analysis import parse_brief_json

        with pytest.raises(json.JSONDecodeError):
            parse_brief_json("not valid json at all {{{")

    # ── parse_trajectory_json ──────────────────────────────────────────────────

    def test_parse_trajectory_json_valid(self):
        """parse_trajectory_json() parses valid JSON with all required fields."""
        from competitive.analysis import parse_trajectory_json

        data = {
            "eras": [
                {"period": "2020-2022", "theme": "Early growth", "description": "Started as a search tool"},
                {"period": "2023-present", "theme": "Enterprise pivot", "description": "Shifted to enterprise"},
            ],
            "inflection_points": [
                "Raised Series B in 2022",
                "Launched enterprise tier in 2023",
            ],
            "trajectory_summary": "Moving upmarket toward enterprise finance customers.",
        }
        result = parse_trajectory_json(json.dumps(data))
        assert len(result["eras"]) == 2
        assert len(result["inflection_points"]) == 2
        assert "enterprise" in result["trajectory_summary"]

    def test_parse_trajectory_json_extracts_from_markdown(self):
        """parse_trajectory_json() handles ```json...``` markdown wrapping."""
        from competitive.analysis import parse_trajectory_json

        data = {
            "eras": [{"period": "2021-present", "theme": "Growth", "description": "Fast growth"}],
            "inflection_points": ["Series A"],
            "trajectory_summary": "Steady growth",
        }
        wrapped = f"```json\n{json.dumps(data)}\n```"
        result = parse_trajectory_json(wrapped)
        assert result["trajectory_summary"] == "Steady growth"

    def test_parse_trajectory_json_missing_fields_raises(self):
        """parse_trajectory_json() raises ValueError when required fields are missing."""
        from competitive.analysis import parse_trajectory_json

        incomplete = json.dumps({"eras": []})
        with pytest.raises(ValueError, match="missing required fields"):
            parse_trajectory_json(incomplete)


# ── CLI Entrypoint ─────────────────────────────────────────────────────────────

class TestCLI:
    """Tests for competitive_intel.py — CLI entrypoint and run() orchestrator."""

    def test_import_works(self):
        """competitive_intel module is importable and exposes a `run` attribute."""
        import importlib
        ci = importlib.import_module("competitive_intel")
        assert hasattr(ci, "run"), "competitive_intel must expose a `run` function"

    def test_run_single_competitor(self, db_path, monkeypatch):
        """run(slugs=['f2']) calls ingest_competitor exactly once, for f2."""
        import importlib
        import database as db_module

        # Reload database so it uses the temp DB path
        importlib.reload(db_module)

        import competitive.models as m
        importlib.reload(m)

        import competitive.competitors as c
        importlib.reload(c)

        # Track calls to the ingestion / analysis functions
        ingest_calls = []
        search_calls = []
        brief_calls = []
        trajectory_calls = []
        signal_calls = []

        def fake_ingest(slug, url):
            ingest_calls.append(slug)

        def fake_search(name, slug):
            search_calls.append(slug)

        def fake_brief(slug, force=False):
            brief_calls.append(slug)

        def fake_trajectory(slug, force=False):
            trajectory_calls.append(slug)

        def fake_signals(slug):
            signal_calls.append(slug)

        def fake_verify(slugs=None):
            pass  # skip real HTTP calls

        # Patch at the module level that competitive_intel imports from
        monkeypatch.setattr("competitive.ingestion.ingest_competitor", fake_ingest)
        monkeypatch.setattr("competitive.ingestion.search_news", fake_search)
        monkeypatch.setattr("competitive.analysis.generate_brief", fake_brief)
        monkeypatch.setattr("competitive.analysis.generate_trajectory", fake_trajectory)
        monkeypatch.setattr("competitive.analysis.detect_signals", fake_signals)
        monkeypatch.setattr("competitive.competitors.verify_urls", fake_verify)

        import competitive_intel
        importlib.reload(competitive_intel)

        result = competitive_intel.run(slugs=["f2"], force=False)

        assert result == 0, f"run() should return 0 on success, got {result}"
        assert ingest_calls == ["f2"], (
            f"ingest_competitor should be called once for 'f2', got: {ingest_calls}"
        )
        assert search_calls == ["f2"], (
            f"search_news should be called once for 'f2', got: {search_calls}"
        )
