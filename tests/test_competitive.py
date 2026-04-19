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
