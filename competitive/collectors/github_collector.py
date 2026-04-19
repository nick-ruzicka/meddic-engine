"""GitHub activity monitor for the  Competitive Signal Engine.

Detects commit bursts, new repositories, and star spikes in competitor GitHub
organisations.  These are leading indicators of development velocity and
upcoming product launches.

Rate limits (unauthenticated): 60 requests / hour.
- Most competitors have empty github_orgs → fast no-op.
- AlphaSense is the primary monitored org ("AlphaSense-Engineering").
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import requests

from competitive.collectors.base import Collector, RawSignal
from competitive.collectors.commit_patterns import (
    detect_commit_burst,
    detect_star_spike,
    filter_bot_authors,
)
from database import get_db

logger = logging.getLogger(__name__)

_GITHUB_API_BASE = "https://api.github.com"
_HEADERS = {"Accept": "application/vnd.github.v3+json"}

# SQL for the baseline table — idempotent CREATE IF NOT EXISTS.
_CREATE_BASELINE_SQL = """
CREATE TABLE IF NOT EXISTS ci_github_baseline (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    competitor       TEXT NOT NULL,
    org              TEXT NOT NULL,
    repo             TEXT NOT NULL,
    stars            INTEGER DEFAULT 0,
    last_commit_sha  TEXT,
    baselined_at     TEXT DEFAULT (datetime('now')),
    UNIQUE(competitor, org, repo)
)
"""


def _ensure_table(conn) -> None:
    conn.execute(_CREATE_BASELINE_SQL)
    conn.commit()


def _fetch_repos(org: str) -> Optional[list]:
    """Fetch up to 30 recently-updated repos for an org.

    Returns None on HTTP error so callers can handle gracefully.
    """
    url = f"{_GITHUB_API_BASE}/orgs/{org}/repos"
    params = {"sort": "updated", "per_page": 30}
    try:
        resp = requests.get(url, headers=_HEADERS, params=params, timeout=15)
        if resp.status_code == 403:
            logger.warning("GitHub API rate limit hit fetching repos for org=%s", org)
            return None
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        logger.warning("GitHub API error fetching repos for org=%s: %s", org, exc)
        return None


def _fetch_commits(org: str, repo: str, since_iso: str) -> Optional[list]:
    """Fetch up to 30 commits for a repo since a given ISO timestamp.

    Returns None on HTTP error.
    """
    url = f"{_GITHUB_API_BASE}/repos/{org}/{repo}/commits"
    params = {"per_page": 30, "since": since_iso}
    try:
        resp = requests.get(url, headers=_HEADERS, params=params, timeout=15)
        if resp.status_code == 403:
            logger.warning(
                "GitHub API rate limit hit fetching commits for %s/%s", org, repo
            )
            return None
        if resp.status_code == 409:
            # Empty repo — no commits yet
            return []
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        logger.warning(
            "GitHub API error fetching commits for %s/%s: %s", org, repo, exc
        )
        return None


def _load_baseline(conn, competitor: str, org: str) -> dict[str, dict]:
    """Return a dict keyed by repo name with baseline metadata."""
    rows = conn.execute(
        "SELECT repo, stars, last_commit_sha, baselined_at "
        "FROM ci_github_baseline WHERE competitor=? AND org=?",
        (competitor, org),
    ).fetchall()
    return {
        row["repo"]: {
            "stars": row["stars"],
            "last_commit_sha": row["last_commit_sha"],
            "baselined_at": row["baselined_at"],
        }
        for row in rows
    }


def _upsert_baseline_repo(
    conn,
    competitor: str,
    org: str,
    repo: str,
    stars: int,
    last_commit_sha: Optional[str],
) -> None:
    conn.execute(
        """
        INSERT INTO ci_github_baseline (competitor, org, repo, stars, last_commit_sha, baselined_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(competitor, org, repo) DO UPDATE SET
            stars           = excluded.stars,
            last_commit_sha = excluded.last_commit_sha,
            baselined_at    = excluded.baselined_at
        """,
        (competitor, org, repo, stars, last_commit_sha),
    )


class GitHubCollector(Collector):
    """Collects GitHub activity signals for competitor organisations."""

    name = "github"

    # ── Baseline ──────────────────────────────────────────────────────────────

    def baseline(self, competitor: dict) -> None:
        """Record the current state of all repos in competitor's github_orgs."""
        orgs: list[str] = competitor.get("github_orgs") or []
        if not orgs:
            return

        slug = competitor["slug"]
        conn = get_db()
        try:
            _ensure_table(conn)
            for org in orgs:
                repos = _fetch_repos(org)
                if repos is None:
                    logger.warning(
                        "Skipping baseline for org=%s (fetch failed)", org
                    )
                    continue

                for repo_data in repos:
                    repo_name = repo_data.get("name", "")
                    stars = repo_data.get("stargazers_count", 0)

                    # Grab the latest commit SHA from the default branch if available.
                    # We store it so collect() can use it as a marker.
                    last_sha: Optional[str] = None
                    default_branch_sha = (
                        (repo_data.get("default_branch") or "")
                    )
                    # The /repos API doesn't directly give the latest SHA; we'll
                    # fetch the first commit to get the HEAD SHA.
                    commits = _fetch_commits(
                        org,
                        repo_name,
                        since_iso="1970-01-01T00:00:00Z",
                    )
                    if commits:
                        # API returns newest-first
                        last_sha = commits[0].get("sha")

                    _upsert_baseline_repo(conn, slug, org, repo_name, stars, last_sha)

                conn.commit()
                logger.info(
                    "Baselined %d repos for competitor=%s org=%s",
                    len(repos),
                    slug,
                    org,
                )
        finally:
            conn.close()

    # ── Collect ───────────────────────────────────────────────────────────────

    def collect(self, competitor: dict) -> list[RawSignal]:
        """Diff current GitHub state against baseline; return new signals."""
        orgs: list[str] = competitor.get("github_orgs") or []
        if not orgs:
            return []

        slug = competitor["slug"]
        signals: list[RawSignal] = []
        now = datetime.now(timezone.utc)

        conn = get_db()
        try:
            _ensure_table(conn)

            for org in orgs:
                current_repos = _fetch_repos(org)
                if current_repos is None:
                    logger.warning(
                        "Skipping collect for org=%s (fetch failed)", org
                    )
                    continue

                baseline = _load_baseline(conn, slug, org)
                current_repo_names = {r["name"] for r in current_repos}

                for repo_data in current_repos:
                    repo_name = repo_data.get("name", "")
                    current_stars = repo_data.get("stargazers_count", 0)

                    # ── New repo ──────────────────────────────────────────────
                    if repo_name not in baseline:
                        logger.info(
                            "New repo detected: %s/%s for competitor=%s",
                            org,
                            repo_name,
                            slug,
                        )
                        signals.append(
                            RawSignal(
                                competitor=slug,
                                source=self.name,
                                signal_type="new_repo",
                                payload={
                                    "org": org,
                                    "repo": repo_name,
                                    "event": "new_repo",
                                    "count": 0,
                                    "period": "7d",
                                    "top_authors": [],
                                },
                                observed_at=now,
                                raw_url=f"https://github.com/{org}/{repo_name}",
                                confidence=0.7,
                            )
                        )
                        # Record in baseline so we don't fire again next run.
                        _upsert_baseline_repo(conn, slug, org, repo_name, current_stars, None)
                        continue

                    repo_baseline = baseline[repo_name]

                    # ── Commit burst ──────────────────────────────────────────
                    since_iso = repo_baseline.get("baselined_at") or "1970-01-01T00:00:00Z"
                    commits = _fetch_commits(org, repo_name, since_iso)

                    if commits is None:
                        # Rate-limited or error — log and move on.
                        logger.warning(
                            "Could not fetch commits for %s/%s; skipping burst check",
                            org,
                            repo_name,
                        )
                    else:
                        human_commits = filter_bot_authors(commits)
                        if detect_commit_burst(human_commits):
                            top_authors = list(
                                {
                                    (c.get("author") or {}).get("login")
                                    or (c.get("commit") or {}).get("author", {}).get("name", "")
                                    for c in human_commits
                                }
                                - {""}
                            )[:5]
                            logger.info(
                                "Commit burst detected: %s/%s (%d commits) for competitor=%s",
                                org,
                                repo_name,
                                len(human_commits),
                                slug,
                            )
                            signals.append(
                                RawSignal(
                                    competitor=slug,
                                    source=self.name,
                                    signal_type="commit_burst",
                                    payload={
                                        "org": org,
                                        "repo": repo_name,
                                        "event": "commit_burst",
                                        "count": len(human_commits),
                                        "period": "7d",
                                        "top_authors": top_authors,
                                    },
                                    observed_at=now,
                                    raw_url=f"https://github.com/{org}/{repo_name}/commits",
                                    confidence=0.6,
                                )
                            )

                    # ── Star spike ────────────────────────────────────────────
                    baseline_stars = repo_baseline.get("stars", 0)
                    if detect_star_spike(current_stars, baseline_stars):
                        logger.info(
                            "Star spike detected: %s/%s (%d → %d) for competitor=%s",
                            org,
                            repo_name,
                            baseline_stars,
                            current_stars,
                            slug,
                        )
                        signals.append(
                            RawSignal(
                                competitor=slug,
                                source=self.name,
                                signal_type="star_spike",
                                payload={
                                    "org": org,
                                    "repo": repo_name,
                                    "event": "star_spike",
                                    "count": current_stars,
                                    "period": "7d",
                                    "top_authors": [],
                                    "baseline_stars": baseline_stars,
                                },
                                observed_at=now,
                                raw_url=f"https://github.com/{org}/{repo_name}",
                                confidence=0.5,
                            )
                        )

                    # Update baseline with current state.
                    new_sha = repo_baseline.get("last_commit_sha")
                    if commits:
                        new_sha = commits[0].get("sha") or new_sha
                    _upsert_baseline_repo(conn, slug, org, repo_name, current_stars, new_sha)

                conn.commit()

        finally:
            conn.close()

        return signals
