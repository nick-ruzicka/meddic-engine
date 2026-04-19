"""Tests for competitive/collectors/dns_collector.py.

All tests use a temporary SQLite database and mock crt.sh HTTP calls,
so no real network requests are made.
"""

import importlib
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def db_path(tmp_path):
    """Point DB_PATH at a fresh temp file for each test."""
    db_file = str(tmp_path / "test_dns.db")
    os.environ["DB_PATH"] = db_file
    yield db_file
    os.environ.pop("DB_PATH", None)


@pytest.fixture()
def collector(db_path):
    """Return a freshly-imported DNSCollector with the temp DB wired in."""
    import database as db_module
    importlib.reload(db_module)

    import competitive.collectors.dns_collector as dns_mod
    importlib.reload(dns_mod)

    return dns_mod.DNSCollector()


@pytest.fixture()
def dns_mod(db_path):
    """Return the reloaded dns_collector module (for helper access in tests)."""
    import database as db_module
    importlib.reload(db_module)

    import competitive.collectors.dns_collector as mod
    importlib.reload(mod)
    return mod


COMPETITOR = {
    "slug": "rogo",
    "name": "Rogo",
    "domain": "rogo.ai",
    "dns_root": "rogo.ai",
}

# A realistic fake crt.sh response for rogo.ai
FAKE_CRTSH_RESPONSE = [
    {"name_value": "www.rogo.ai", "not_before": "2025-01-01T00:00:00"},
    {"name_value": "api.rogo.ai", "not_before": "2025-02-01T00:00:00"},
    {"name_value": "docs.rogo.ai", "not_before": "2025-03-01T00:00:00"},
    # Duplicate — same subdomain from a second cert
    {"name_value": "api.rogo.ai", "not_before": "2025-04-01T00:00:00"},
    # Wildcard — should be filtered out
    {"name_value": "*.rogo.ai", "not_before": "2025-01-15T00:00:00"},
    # Root domain itself — should be filtered out
    {"name_value": "rogo.ai", "not_before": "2025-01-01T00:00:00"},
    # Cross-domain SAN — should be filtered out
    {"name_value": "other.example.com", "not_before": "2025-01-01T00:00:00"},
]


# ── _parse_subdomains ─────────────────────────────────────────────────────────


class TestParseSubdomains:
    def test_returns_unique_subdomains(self, dns_mod):
        result = dns_mod._parse_subdomains(FAKE_CRTSH_RESPONSE, "rogo.ai")
        assert result == {"www.rogo.ai", "api.rogo.ai", "docs.rogo.ai"}

    def test_filters_wildcards(self, dns_mod):
        entries = [{"name_value": "*.rogo.ai"}]
        result = dns_mod._parse_subdomains(entries, "rogo.ai")
        assert result == set()

    def test_filters_root_domain(self, dns_mod):
        entries = [{"name_value": "rogo.ai"}]
        result = dns_mod._parse_subdomains(entries, "rogo.ai")
        assert result == set()

    def test_deduplicates(self, dns_mod):
        entries = [
            {"name_value": "api.rogo.ai"},
            {"name_value": "api.rogo.ai"},
            {"name_value": "API.rogo.ai"},  # case variant
        ]
        result = dns_mod._parse_subdomains(entries, "rogo.ai")
        assert result == {"api.rogo.ai"}

    def test_filters_cross_domain_sans(self, dns_mod):
        entries = [{"name_value": "other.example.com"}]
        result = dns_mod._parse_subdomains(entries, "rogo.ai")
        assert result == set()

    def test_handles_newline_separated_sans(self, dns_mod):
        """crt.sh sometimes packs multiple SANs into one name_value field."""
        entries = [{"name_value": "api.rogo.ai\nbeta.rogo.ai\n*.rogo.ai"}]
        result = dns_mod._parse_subdomains(entries, "rogo.ai")
        assert result == {"api.rogo.ai", "beta.rogo.ai"}

    def test_empty_response(self, dns_mod):
        result = dns_mod._parse_subdomains([], "rogo.ai")
        assert result == set()


# ── _score_subdomain ──────────────────────────────────────────────────────────


class TestScoreSubdomain:
    @pytest.mark.parametrize("subdomain,expected", [
        ("docs.rogo.ai", 0.9),
        ("api.rogo.ai", 0.9),
        ("developer.rogo.ai", 0.9),
        ("sdk.rogo.ai", 0.9),
        ("app.rogo.ai", 0.85),
        ("beta.rogo.ai", 0.85),
        ("staging.rogo.ai", 0.85),
        ("v2.rogo.ai", 0.85),
        ("status.rogo.ai", 0.7),
        ("security.rogo.ai", 0.7),
        ("random.rogo.ai", 0.4),
        ("www.rogo.ai", 0.4),
        ("careers.rogo.ai", 0.4),
    ])
    def test_confidence_scores(self, dns_mod, subdomain, expected):
        assert dns_mod._score_subdomain(subdomain) == expected


# ── baseline() ───────────────────────────────────────────────────────────────


class TestBaseline:
    def test_stores_subdomains_in_db(self, collector, db_path, monkeypatch):
        import competitive.collectors.dns_collector as dns_mod
        monkeypatch.setattr(dns_mod, "_fetch_crt_sh", lambda domain: FAKE_CRTSH_RESPONSE)

        collector.baseline(COMPETITOR)

        import sqlite3
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT subdomain FROM ci_dns_baseline WHERE competitor = 'rogo'"
        ).fetchall()
        conn.close()

        stored = {row[0] for row in rows}
        assert stored == {"www.rogo.ai", "api.rogo.ai", "docs.rogo.ai"}

    def test_baseline_is_idempotent(self, collector, db_path, monkeypatch):
        """Calling baseline() twice should not duplicate rows."""
        import competitive.collectors.dns_collector as dns_mod
        monkeypatch.setattr(dns_mod, "_fetch_crt_sh", lambda domain: FAKE_CRTSH_RESPONSE)

        collector.baseline(COMPETITOR)
        collector.baseline(COMPETITOR)

        import sqlite3
        conn = sqlite3.connect(db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM ci_dns_baseline WHERE competitor = 'rogo'"
        ).fetchone()[0]
        conn.close()

        assert count == 3  # www, api, docs — no duplicates

    def test_baseline_graceful_on_api_failure(self, collector, monkeypatch):
        """baseline() should not raise if crt.sh is unavailable."""
        import competitive.collectors.dns_collector as dns_mod
        import requests

        def raise_error(domain):
            raise requests.ConnectionError("crt.sh unreachable")

        monkeypatch.setattr(dns_mod, "_fetch_crt_sh", raise_error)

        # Must not raise
        collector.baseline(COMPETITOR)


# ── collect() ────────────────────────────────────────────────────────────────


class TestCollect:
    def _set_baseline(self, db_path, subdomains):
        """Helper: manually seed the baseline table."""
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ci_dns_baseline (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                competitor TEXT NOT NULL,
                subdomain TEXT NOT NULL,
                first_seen TEXT DEFAULT (datetime('now')),
                last_seen TEXT DEFAULT (datetime('now')),
                UNIQUE(competitor, subdomain)
            )
        """)
        for sd in subdomains:
            conn.execute(
                "INSERT OR IGNORE INTO ci_dns_baseline (competitor, subdomain) VALUES (?, ?)",
                ("rogo", sd),
            )
        conn.commit()
        conn.close()

    def test_detects_new_subdomain(self, collector, db_path, monkeypatch):
        """New subdomain not in baseline → RawSignal emitted."""
        import competitive.collectors.dns_collector as dns_mod

        # Baseline has www and api; crt.sh now returns www, api, AND docs (new)
        self._set_baseline(db_path, ["www.rogo.ai", "api.rogo.ai"])
        monkeypatch.setattr(dns_mod, "_fetch_crt_sh", lambda domain: FAKE_CRTSH_RESPONSE)

        signals = collector.collect(COMPETITOR)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.signal_type == "new_subdomain"
        assert sig.payload["subdomain"] == "docs.rogo.ai"
        assert sig.competitor == "rogo"
        assert sig.source == "dns"

    def test_no_signal_when_nothing_new(self, collector, db_path, monkeypatch):
        """If all current subdomains are already in baseline, return empty list."""
        import competitive.collectors.dns_collector as dns_mod

        self._set_baseline(db_path, ["www.rogo.ai", "api.rogo.ai", "docs.rogo.ai"])
        monkeypatch.setattr(dns_mod, "_fetch_crt_sh", lambda domain: FAKE_CRTSH_RESPONSE)

        signals = collector.collect(COMPETITOR)
        assert signals == []

    def test_collect_updates_baseline_with_new_subdomain(self, collector, db_path, monkeypatch):
        """After collect(), new subdomains are inserted into ci_dns_baseline."""
        import competitive.collectors.dns_collector as dns_mod
        import sqlite3

        self._set_baseline(db_path, ["www.rogo.ai"])
        monkeypatch.setattr(dns_mod, "_fetch_crt_sh", lambda domain: FAKE_CRTSH_RESPONSE)

        collector.collect(COMPETITOR)

        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT subdomain FROM ci_dns_baseline WHERE competitor = 'rogo'"
        ).fetchall()
        conn.close()

        stored = {row[0] for row in rows}
        assert "api.rogo.ai" in stored
        assert "docs.rogo.ai" in stored

    def test_collect_confidence_docs_subdomain(self, collector, db_path, monkeypatch):
        """docs.* subdomain gets confidence 0.9."""
        import competitive.collectors.dns_collector as dns_mod

        # Baseline missing only docs.rogo.ai
        self._set_baseline(db_path, ["www.rogo.ai", "api.rogo.ai"])
        monkeypatch.setattr(dns_mod, "_fetch_crt_sh", lambda domain: FAKE_CRTSH_RESPONSE)

        signals = collector.collect(COMPETITOR)

        docs_signals = [s for s in signals if "docs" in s.payload["subdomain"]]
        assert len(docs_signals) == 1
        assert docs_signals[0].confidence == 0.9

    def test_collect_confidence_random_subdomain(self, collector, db_path, monkeypatch):
        """An unrecognised subdomain prefix gets confidence 0.4."""
        import competitive.collectors.dns_collector as dns_mod

        random_entry = [{"name_value": "careers.rogo.ai", "not_before": "2025-01-01"}]
        self._set_baseline(db_path, [])
        monkeypatch.setattr(dns_mod, "_fetch_crt_sh", lambda domain: random_entry)

        signals = collector.collect(COMPETITOR)

        assert len(signals) == 1
        assert signals[0].confidence == 0.4

    def test_collect_graceful_on_api_failure(self, collector, db_path, monkeypatch):
        """collect() returns empty list and does not raise on crt.sh failure."""
        import competitive.collectors.dns_collector as dns_mod
        import requests

        def raise_error(domain):
            raise requests.Timeout("crt.sh timed out")

        monkeypatch.setattr(dns_mod, "_fetch_crt_sh", raise_error)

        signals = collector.collect(COMPETITOR)
        assert signals == []

    def test_multiple_new_subdomains(self, collector, db_path, monkeypatch):
        """All new subdomains (not in baseline) produce individual signals."""
        import competitive.collectors.dns_collector as dns_mod

        # Empty baseline — everything is new
        self._set_baseline(db_path, [])
        monkeypatch.setattr(dns_mod, "_fetch_crt_sh", lambda domain: FAKE_CRTSH_RESPONSE)

        signals = collector.collect(COMPETITOR)

        assert len(signals) == 3  # www, api, docs
        subdomains_in_signals = {s.payload["subdomain"] for s in signals}
        assert subdomains_in_signals == {"www.rogo.ai", "api.rogo.ai", "docs.rogo.ai"}

    def test_raw_url_points_to_crtsh(self, collector, db_path, monkeypatch):
        """Each signal's raw_url links to the crt.sh entry for that subdomain."""
        import competitive.collectors.dns_collector as dns_mod

        self._set_baseline(db_path, ["www.rogo.ai", "api.rogo.ai"])
        monkeypatch.setattr(dns_mod, "_fetch_crt_sh", lambda domain: FAKE_CRTSH_RESPONSE)

        signals = collector.collect(COMPETITOR)

        assert len(signals) == 1
        assert signals[0].raw_url == "https://crt.sh/?q=docs.rogo.ai"


# ── Collector interface ───────────────────────────────────────────────────────


class TestCollectorInterface:
    def test_name_is_dns(self, collector):
        assert collector.name == "dns"

    def test_implements_collector_abc(self, collector):
        from competitive.collectors.base import Collector
        assert isinstance(collector, Collector)
