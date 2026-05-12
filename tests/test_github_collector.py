"""Tests for GitHub activity collector and commit pattern helpers.

Uses temporary SQLite databases so the real data/meddic.db is never touched.
All GitHub API calls are mocked via unittest.mock.patch / pytest monkeypatch.
"""

import importlib
import os
import sys
import sqlite3
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    """Point DB_PATH at a fresh temp file for every test, then reload modules."""
    db_file = str(tmp_path / "test_github.db")
    os.environ["DB_PATH"] = db_file

    # Reload database so get_db() uses the new path.
    import database as db_module
    importlib.reload(db_module)

    # Reload the collector modules so their `get_db` binding is fresh.
    import competitive.collectors.github_collector as gc_module
    importlib.reload(gc_module)

    yield db_file

    os.environ.pop("DB_PATH", None)


@pytest.fixture()
def gc():
    """Return a freshly imported GitHubCollector instance."""
    from competitive.collectors.github_collector import GitHubCollector
    return GitHubCollector()


@pytest.fixture()
def alphasense_competitor():
    return {
        "slug": "alphasense",
        "name": "AlphaSense",
        "github_orgs": ["AlphaSense-Engineering"],
    }


@pytest.fixture()
def no_orgs_competitor():
    return {
        "slug": "rogo",
        "name": "Rogo",
        "github_orgs": [],
    }


# ── commit_patterns tests ──────────────────────────────────────────────────────


class TestFilterBotAuthors:
    """filter_bot_authors removes known bot commits and keeps human ones."""

    def _make_commit(self, login: str = "", name: str = "") -> dict:
        return {
            "sha": "abc123",
            "author": {"login": login} if login else None,
            "commit": {"author": {"name": name, "date": "2026-04-19T10:00:00Z"}},
        }

    def test_removes_dependabot(self):
        from competitive.collectors.commit_patterns import filter_bot_authors
        commits = [
            self._make_commit(login="dependabot[bot]"),
            self._make_commit(login="alice", name="Alice"),
        ]
        result = filter_bot_authors(commits)
        assert len(result) == 1
        assert result[0]["author"]["login"] == "alice"

    def test_removes_renovate(self):
        from competitive.collectors.commit_patterns import filter_bot_authors
        commits = [self._make_commit(login="renovate-bot")]
        assert filter_bot_authors(commits) == []

    def test_removes_github_actions(self):
        from competitive.collectors.commit_patterns import filter_bot_authors
        commits = [self._make_commit(name="github-actions")]
        assert filter_bot_authors(commits) == []

    def test_removes_greenkeeper(self):
        from competitive.collectors.commit_patterns import filter_bot_authors
        commits = [self._make_commit(login="greenkeeper[bot]")]
        assert filter_bot_authors(commits) == []

    def test_removes_semantic_release(self):
        from competitive.collectors.commit_patterns import filter_bot_authors
        commits = [self._make_commit(name="semantic-release")]
        assert filter_bot_authors(commits) == []

    def test_keeps_human_commits(self):
        from competitive.collectors.commit_patterns import filter_bot_authors
        commits = [
            self._make_commit(login="bob", name="Bob"),
            self._make_commit(login="carol", name="Carol"),
        ]
        assert len(filter_bot_authors(commits)) == 2

    def test_empty_list(self):
        from competitive.collectors.commit_patterns import filter_bot_authors
        assert filter_bot_authors([]) == []


class TestDetectCommitBurst:
    """detect_commit_burst uses a threshold of 5 commits in a single day."""

    def _make_commits(self, count: int, date: str = "2026-04-19") -> list[dict]:
        return [
            {
                "sha": f"sha{i}",
                "author": {"login": f"user{i}"},
                "commit": {"author": {"name": f"User {i}", "date": f"{date}T{i:02d}:00:00Z"}},
            }
            for i in range(count)
        ]

    def test_four_commits_no_burst(self):
        from competitive.collectors.commit_patterns import detect_commit_burst
        assert detect_commit_burst(self._make_commits(4)) is False

    def test_five_commits_is_burst(self):
        from competitive.collectors.commit_patterns import detect_commit_burst
        assert detect_commit_burst(self._make_commits(5)) is True

    def test_ten_commits_is_burst(self):
        from competitive.collectors.commit_patterns import detect_commit_burst
        assert detect_commit_burst(self._make_commits(10)) is True

    def test_burst_requires_same_day(self):
        from competitive.collectors.commit_patterns import detect_commit_burst
        # 3 on day 1 + 3 on day 2 = no single-day burst (threshold=5)
        commits = self._make_commits(3, "2026-04-18") + self._make_commits(3, "2026-04-19")
        assert detect_commit_burst(commits) is False

    def test_custom_threshold(self):
        from competitive.collectors.commit_patterns import detect_commit_burst
        assert detect_commit_burst(self._make_commits(3), threshold=3) is True
        assert detect_commit_burst(self._make_commits(2), threshold=3) is False

    def test_empty_list(self):
        from competitive.collectors.commit_patterns import detect_commit_burst
        assert detect_commit_burst([]) is False


class TestDetectStarSpike:
    """detect_star_spike uses >10% relative increase by default."""

    def test_nine_percent_no_spike(self):
        from competitive.collectors.commit_patterns import detect_star_spike
        # 100 → 109 = 9% increase
        assert detect_star_spike(109, 100) is False

    def test_ten_percent_no_spike(self):
        from competitive.collectors.commit_patterns import detect_star_spike
        # Exactly 10% — threshold is STRICTLY greater than
        assert detect_star_spike(110, 100) is False

    def test_eleven_percent_is_spike(self):
        from competitive.collectors.commit_patterns import detect_star_spike
        # 100 → 111 = 11% increase
        assert detect_star_spike(111, 100) is True

    def test_zero_baseline_no_spike(self):
        from competitive.collectors.commit_patterns import detect_star_spike
        assert detect_star_spike(1000, 0) is False

    def test_negative_change_no_spike(self):
        from competitive.collectors.commit_patterns import detect_star_spike
        assert detect_star_spike(90, 100) is False

    def test_custom_threshold(self):
        from competitive.collectors.commit_patterns import detect_star_spike
        # 114 / 100 = 14% increase — below the 15% custom threshold
        assert detect_star_spike(114, 100, threshold_pct=15.0) is False
        # 116 / 100 = 16% increase — above the 15% custom threshold
        assert detect_star_spike(116, 100, threshold_pct=15.0) is True


# ── GitHubCollector.baseline tests ────────────────────────────────────────────


class TestBaseline:
    """baseline() stores repos in ci_github_baseline."""

    def _fake_repo(self, name: str, stars: int = 10) -> dict:
        return {"name": name, "stargazers_count": stars, "default_branch": "main"}

    def _fake_commit(self, sha: str = "deadbeef") -> dict:
        return {
            "sha": sha,
            "author": {"login": "alice"},
            "commit": {"author": {"name": "Alice", "date": "2026-04-19T10:00:00Z"}},
        }

    def test_baseline_stores_repos(self, gc, alphasense_competitor, isolated_db):
        repos = [self._fake_repo("market-data"), self._fake_repo("sdk")]
        commits = [self._fake_commit("abc111")]

        with patch(
            "competitive.collectors.github_collector._fetch_repos",
            return_value=repos,
        ), patch(
            "competitive.collectors.github_collector._fetch_commits",
            return_value=commits,
        ):
            gc.baseline(alphasense_competitor)

        conn = sqlite3.connect(isolated_db)
        rows = conn.execute(
            "SELECT repo FROM ci_github_baseline WHERE competitor='alphasense'"
        ).fetchall()
        conn.close()

        repo_names = {r[0] for r in rows}
        assert "market-data" in repo_names
        assert "sdk" in repo_names

    def test_baseline_skips_empty_orgs(self, gc, no_orgs_competitor, isolated_db):
        """No DB writes when github_orgs is empty."""
        gc.baseline(no_orgs_competitor)
        conn = sqlite3.connect(isolated_db)
        # Table may not even exist yet — that's fine
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        # Either no table or no rows for this competitor
        if "ci_github_baseline" in tables:
            conn2 = sqlite3.connect(isolated_db)
            count = conn2.execute(
                "SELECT COUNT(*) FROM ci_github_baseline WHERE competitor='rogo'"
            ).fetchone()[0]
            conn2.close()
            assert count == 0


# ── GitHubCollector.collect tests ─────────────────────────────────────────────


class TestCollect:
    """collect() diffs current state against baseline and emits RawSignals."""

    def _seed_baseline(self, db_path: str, competitor: str, org: str, repo: str,
                       stars: int = 100, sha: str = "base_sha") -> None:
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ci_github_baseline (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                competitor TEXT NOT NULL,
                org TEXT NOT NULL,
                repo TEXT NOT NULL,
                stars INTEGER DEFAULT 0,
                last_commit_sha TEXT,
                baselined_at TEXT DEFAULT (datetime('now')),
                UNIQUE(competitor, org, repo)
            )
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO ci_github_baseline "
            "(competitor, org, repo, stars, last_commit_sha, baselined_at) "
            "VALUES (?, ?, ?, ?, ?, '2026-04-01T00:00:00')",
            (competitor, org, repo, stars, sha),
        )
        conn.commit()
        conn.close()

    def _fake_commit(self, sha: str = "newsha", login: str = "alice",
                     date: str = "2026-04-19T10:00:00Z") -> dict:
        return {
            "sha": sha,
            "author": {"login": login},
            "commit": {"author": {"name": login.capitalize(), "date": date}},
        }

    # ── empty orgs ────────────────────────────────────────────────────────────

    def test_empty_orgs_returns_empty_list(self, gc, no_orgs_competitor):
        result = gc.collect(no_orgs_competitor)
        assert result == []

    # ── new repo ──────────────────────────────────────────────────────────────

    def test_detects_new_repo(self, gc, alphasense_competitor, isolated_db):
        """A repo not in baseline → new_repo signal with confidence 0.7."""
        # Seed baseline with "old-repo" only.
        self._seed_baseline(isolated_db, "alphasense", "AlphaSense-Engineering", "old-repo")

        current_repos = [
            {"name": "old-repo", "stargazers_count": 100},
            {"name": "brand-new-repo", "stargazers_count": 5},
        ]
        no_commits: list = []

        with patch(
            "competitive.collectors.github_collector._fetch_repos",
            return_value=current_repos,
        ), patch(
            "competitive.collectors.github_collector._fetch_commits",
            return_value=no_commits,
        ):
            signals = gc.collect(alphasense_competitor)

        new_repo_signals = [s for s in signals if s.signal_type == "new_repo"]
        assert len(new_repo_signals) == 1
        sig = new_repo_signals[0]
        assert sig.competitor == "alphasense"
        assert sig.source == "github"
        assert sig.payload["repo"] == "brand-new-repo"
        assert sig.confidence == 0.7

    # ── commit burst ──────────────────────────────────────────────────────────

    def test_detects_commit_burst(self, gc, alphasense_competitor, isolated_db):
        """5+ non-bot commits in a day → commit_burst signal with confidence 0.6."""
        self._seed_baseline(isolated_db, "alphasense", "AlphaSense-Engineering", "core")

        current_repos = [{"name": "core", "stargazers_count": 100}]
        # 6 commits on the same day (all human)
        burst_commits = [
            self._fake_commit(sha=f"sha{i}", login=f"dev{i}", date="2026-04-19T0{i}:00:00Z")
            for i in range(6)
        ]

        with patch(
            "competitive.collectors.github_collector._fetch_repos",
            return_value=current_repos,
        ), patch(
            "competitive.collectors.github_collector._fetch_commits",
            return_value=burst_commits,
        ):
            signals = gc.collect(alphasense_competitor)

        burst_signals = [s for s in signals if s.signal_type == "commit_burst"]
        assert len(burst_signals) == 1
        assert burst_signals[0].confidence == 0.6
        assert burst_signals[0].payload["count"] == 6

    # ── star spike ────────────────────────────────────────────────────────────

    def test_detects_star_spike(self, gc, alphasense_competitor, isolated_db):
        """Stars up >10% from baseline → star_spike signal with confidence 0.5."""
        self._seed_baseline(
            isolated_db, "alphasense", "AlphaSense-Engineering", "market-data", stars=100
        )

        current_repos = [{"name": "market-data", "stargazers_count": 115}]

        with patch(
            "competitive.collectors.github_collector._fetch_repos",
            return_value=current_repos,
        ), patch(
            "competitive.collectors.github_collector._fetch_commits",
            return_value=[],
        ):
            signals = gc.collect(alphasense_competitor)

        spike_signals = [s for s in signals if s.signal_type == "star_spike"]
        assert len(spike_signals) == 1
        assert spike_signals[0].confidence == 0.5

    # ── no spike below threshold ──────────────────────────────────────────────

    def test_no_spike_below_threshold(self, gc, alphasense_competitor, isolated_db):
        """Stars up 9% should NOT produce a star_spike signal."""
        self._seed_baseline(
            isolated_db, "alphasense", "AlphaSense-Engineering", "market-data", stars=100
        )

        current_repos = [{"name": "market-data", "stargazers_count": 109}]

        with patch(
            "competitive.collectors.github_collector._fetch_repos",
            return_value=current_repos,
        ), patch(
            "competitive.collectors.github_collector._fetch_commits",
            return_value=[],
        ):
            signals = gc.collect(alphasense_competitor)

        assert not any(s.signal_type == "star_spike" for s in signals)

    # ── API failure ───────────────────────────────────────────────────────────

    def test_handles_api_failure_gracefully(self, gc, alphasense_competitor, isolated_db):
        """When _fetch_repos returns None (API failure), return empty list without raising."""
        with patch(
            "competitive.collectors.github_collector._fetch_repos",
            return_value=None,
        ):
            signals = gc.collect(alphasense_competitor)

        assert signals == []

    def test_handles_commits_api_failure_gracefully(self, gc, alphasense_competitor, isolated_db):
        """When _fetch_commits returns None (rate-limited), skip burst check but continue."""
        self._seed_baseline(
            isolated_db, "alphasense", "AlphaSense-Engineering", "core", stars=100
        )
        current_repos = [{"name": "core", "stargazers_count": 100}]

        with patch(
            "competitive.collectors.github_collector._fetch_repos",
            return_value=current_repos,
        ), patch(
            "competitive.collectors.github_collector._fetch_commits",
            return_value=None,
        ):
            # Should not raise; returns whatever other signals were detected.
            signals = gc.collect(alphasense_competitor)

        # No burst signal because commits fetch failed; no star spike (stars unchanged).
        assert not any(s.signal_type == "commit_burst" for s in signals)
