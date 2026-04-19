"""Pattern detection helpers for GitHub activity analysis.

Used by github_collector.py to identify meaningful signals from raw GitHub data:
- commit bursts (5+ non-bot commits in a single day)
- bot author filtering (dependabot, renovate, etc.)
- star spikes (>10% increase from baseline)
"""

from collections import defaultdict

# Authors (login prefixes or exact names) considered bots — commits by these are noise.
_BOT_AUTHOR_PATTERNS = (
    "dependabot",
    "renovate",
    "github-actions",
    "greenkeeper",
    "semantic-release",
)


def filter_bot_authors(commits: list[dict]) -> list[dict]:
    """Remove commits authored by known bots.

    Args:
        commits: List of commit dicts from the GitHub API.  Each dict should
            have a nested structure like:
                {"commit": {"author": {"name": "..."}}, "author": {"login": "..."}}
            Either the top-level ``author.login`` or ``commit.author.name`` is
            checked (lowercased) against the bot pattern list.

    Returns:
        Filtered list with bot commits removed.
    """
    human_commits = []
    for c in commits:
        # Prefer the GitHub login; fall back to the git commit author name.
        login = ""
        name = ""
        if isinstance(c.get("author"), dict):
            login = (c["author"].get("login") or "").lower()
        commit_obj = c.get("commit") or {}
        author_obj = commit_obj.get("author") or {}
        name = (author_obj.get("name") or "").lower()

        is_bot = any(
            pat in login or pat in name
            for pat in _BOT_AUTHOR_PATTERNS
        )
        if not is_bot:
            human_commits.append(c)
    return human_commits


def detect_commit_burst(commits: list[dict], threshold: int = 5) -> bool:
    """Return True if any single calendar day has ``threshold`` or more commits.

    Args:
        commits: List of commit dicts (already bot-filtered is recommended).
            Each dict should have ``commit.author.date`` as an ISO-8601 string,
            e.g. ``"2026-04-19T14:23:00Z"``.
        threshold: Minimum number of commits in a day to consider a burst.
            Defaults to 5.

    Returns:
        True if a burst is detected, False otherwise.
    """
    daily_counts: dict[str, int] = defaultdict(int)
    for c in commits:
        commit_obj = c.get("commit") or {}
        author_obj = commit_obj.get("author") or {}
        date_str = author_obj.get("date") or ""
        # Take only the date portion: "2026-04-19T14:23:00Z" → "2026-04-19"
        day = date_str[:10]
        if day:
            daily_counts[day] += 1

    return any(count >= threshold for count in daily_counts.values())


def detect_star_spike(
    current_stars: int,
    baseline_stars: int,
    threshold_pct: float = 10.0,
) -> bool:
    """Return True if stars have increased by more than ``threshold_pct`` percent.

    Args:
        current_stars: Current star count from the GitHub API.
        baseline_stars: Star count recorded during the last baseline run.
        threshold_pct: Percentage increase required to declare a spike.
            Defaults to 10.0 (i.e. >10%).

    Returns:
        True if the relative increase exceeds the threshold, False otherwise.
        Returns False when baseline_stars is 0 to avoid division by zero.
    """
    if baseline_stars <= 0:
        return False
    pct_increase = (current_stars - baseline_stars) / baseline_stars * 100.0
    return pct_increase > threshold_pct
