"""Jobs collector — monitors competitor career pages for new postings.

Supports two job board backends:
  - Greenhouse: https://boards-api.greenhouse.io/v1/boards/{board_id}/jobs
  - Ashby:      https://api.ashbyhq.com/posting-api/job-board/{board_id}

Detects:
  - New job postings not previously seen (compared against ci_jobs_baseline)
  - Engineering surges (3+ engineering roles in one collect() batch)
  - Classifies each new posting with job_classifier.py

Emits RawSignal(signal_type="job_posting") for each new posting.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import requests

from competitive.collectors.base import Collector, RawSignal
from competitive.collectors.job_classifier import classify_job, CONFIDENCE_MAP
from database import get_db

logger = logging.getLogger(__name__)

GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{board_id}/jobs"
ASHBY_API = "https://api.ashbyhq.com/posting-api/job-board/{board_id}"

REQUEST_TIMEOUT = 15  # seconds


def _extract_board_id(jobs_url: str) -> str:
    """Extract the board slug/id from a jobs URL.

    Examples:
      https://boards.greenhouse.io/alphasense -> alphasense
      https://jobs.ashbyhq.com/rogo          -> rogo
    """
    path = urlparse(jobs_url).path.rstrip("/")
    return path.split("/")[-1]


def _fetch_greenhouse(board_id: str) -> list[dict]:
    """Fetch job listings from Greenhouse API.

    Returns list of normalised job dicts:
      {id, title, department, location, url, published_at}
    """
    url = GREENHOUSE_API.format(board_id=board_id)
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.warning("Greenhouse fetch failed for board %s: %s", board_id, exc)
        return []

    jobs = []
    for job in data.get("jobs", []):
        departments = job.get("departments") or []
        department = departments[0].get("name", "") if departments else ""
        location_obj = job.get("location") or {}
        jobs.append(
            {
                "id": str(job.get("id", "")),
                "title": job.get("title", ""),
                "department": department,
                "location": location_obj.get("name", "") if isinstance(location_obj, dict) else "",
                "url": job.get("absolute_url", ""),
                "published_at": job.get("updated_at", ""),
            }
        )
    return jobs


def _fetch_ashby(board_id: str) -> list[dict]:
    """Fetch job listings from Ashby API.

    Returns list of normalised job dicts:
      {id, title, department, location, url, published_at}
    """
    url = ASHBY_API.format(board_id=board_id)
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.warning("Ashby fetch failed for board %s: %s", board_id, exc)
        return []

    jobs = []
    for job in data.get("jobs", []):
        jobs.append(
            {
                "id": str(job.get("id", "")),
                "title": job.get("title", ""),
                "department": job.get("departmentName", ""),
                "location": job.get("location", ""),
                "url": job.get("jobUrl", job.get("applyUrl", "")),
                "published_at": job.get("publishedAt", ""),
            }
        )
    return jobs


def _fetch_jobs(jobs_url: str, jobs_source: str) -> list[dict]:
    """Dispatch to the correct backend and return normalised job list."""
    board_id = _extract_board_id(jobs_url)
    if jobs_source == "greenhouse":
        return _fetch_greenhouse(board_id)
    elif jobs_source == "ashby":
        return _fetch_ashby(board_id)
    else:
        logger.warning("Unknown jobs_source '%s' for url %s", jobs_source, jobs_url)
        return []


def _is_engineering_role(title: str) -> bool:
    """Return True if the job title looks like an engineering role."""
    title_lower = title.lower()
    engineering_terms = [
        "engineer",
        "engineering",
        "developer",
        "sre",
        "devops",
        "ml",
        "machine learning",
        "data scientist",
        "backend",
        "frontend",
        "full stack",
        "fullstack",
        "infrastructure",
        "platform",
        "security",
    ]
    return any(term in title_lower for term in engineering_terms)


class JobsCollector(Collector):
    """Collects job postings from competitor career pages and diffs against baseline."""

    name = "jobs"

    def _ensure_table(self, conn) -> None:
        """Create ci_jobs_baseline table if it doesn't exist."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ci_jobs_baseline (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                competitor TEXT NOT NULL,
                job_id     TEXT NOT NULL,
                title      TEXT,
                department TEXT,
                location   TEXT,
                url        TEXT,
                first_seen TEXT DEFAULT (datetime('now')),
                still_open INTEGER DEFAULT 1,
                UNIQUE(competitor, job_id)
            )
            """
        )
        conn.commit()

    def baseline(self, competitor: dict) -> None:
        """Fetch current job listings and store as baseline (first-time setup).

        Existing rows for this competitor are left untouched so that re-running
        baseline() is safe (INSERT OR IGNORE).
        """
        slug = competitor.get("slug", "")
        jobs_url = competitor.get("jobs_url")
        jobs_source = competitor.get("jobs_source")

        if not jobs_url:
            logger.info("No jobs_url for %s — skipping baseline", slug)
            return

        jobs = _fetch_jobs(jobs_url, jobs_source)
        if not jobs:
            logger.info("No jobs returned for %s baseline", slug)
            return

        conn = get_db()
        try:
            self._ensure_table(conn)
            for job in jobs:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO ci_jobs_baseline
                        (competitor, job_id, title, department, location, url)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        slug,
                        job["id"],
                        job["title"],
                        job["department"],
                        job["location"],
                        job["url"],
                    ),
                )
            conn.commit()
            logger.info("Baseline stored %d jobs for %s", len(jobs), slug)
        finally:
            conn.close()

    def collect(self, competitor: dict) -> list[RawSignal]:
        """Diff current job listings against baseline and emit signals for new postings.

        Steps:
          1. Fetch current listings from API.
          2. Load known job_ids from ci_jobs_baseline.
          3. New listings not in baseline → classify → RawSignal.
          4. Listings in baseline but no longer live → mark still_open=0.
          5. Upsert current listings into baseline.
          6. Detect engineering surge (3+ new engineering roles in this batch).

        Returns list of RawSignal, one per new job posting.
        """
        slug = competitor.get("slug", "")
        jobs_url = competitor.get("jobs_url")
        jobs_source = competitor.get("jobs_source")

        if not jobs_url:
            logger.info("No jobs_url for %s — skipping collect", slug)
            return []

        current_jobs = _fetch_jobs(jobs_url, jobs_source)
        current_ids = {job["id"] for job in current_jobs}

        conn = get_db()
        signals: list[RawSignal] = []

        try:
            self._ensure_table(conn)

            # Load existing baseline ids for this competitor
            rows = conn.execute(
                "SELECT job_id FROM ci_jobs_baseline WHERE competitor = ? AND still_open = 1",
                (slug,),
            ).fetchall()
            baseline_ids = {row["job_id"] for row in rows}

            # Mark closed jobs
            closed_ids = baseline_ids - current_ids
            if closed_ids:
                conn.execute(
                    "UPDATE ci_jobs_baseline SET still_open = 0 WHERE competitor = ? AND job_id IN ({})".format(
                        ",".join("?" * len(closed_ids))
                    ),
                    (slug, *closed_ids),
                )

            # Identify new jobs
            new_jobs = [job for job in current_jobs if job["id"] not in baseline_ids]

            # Count new engineering roles for surge detection
            new_eng_roles = [j for j in new_jobs if _is_engineering_role(j["title"])]

            observed_at = datetime.now(timezone.utc)

            for job in new_jobs:
                classification = classify_job(job["title"])

                # Inject engineering_surge if 3+ engineering roles in this batch
                if len(new_eng_roles) >= 3 and _is_engineering_role(job["title"]):
                    category = "engineering_surge"
                    keywords_matched = classification["keywords_matched"]
                    confidence = CONFIDENCE_MAP["engineering_surge"]
                else:
                    category = classification["category"]
                    keywords_matched = classification["keywords_matched"]
                    confidence = classification["confidence"]

                # Derive posting_date from published_at (ISO string or empty)
                posting_date = ""
                if job.get("published_at"):
                    try:
                        dt_str = job["published_at"]
                        # Strip trailing Z or timezone suffix for strptime compat
                        posting_date = dt_str[:10]  # YYYY-MM-DD
                    except Exception:
                        posting_date = ""

                payload = {
                    "title": job["title"],
                    "location": job["location"],
                    "department": job["department"],
                    "url": job["url"],
                    "posting_date": posting_date,
                    "keywords": keywords_matched,
                    "category": category,
                }

                signal = RawSignal(
                    competitor=slug,
                    source=self.name,
                    signal_type="job_posting",
                    payload=payload,
                    observed_at=observed_at,
                    raw_url=job["url"] or jobs_url,
                    confidence=confidence,
                )
                signals.append(signal)

                # Upsert into baseline
                conn.execute(
                    """
                    INSERT INTO ci_jobs_baseline
                        (competitor, job_id, title, department, location, url, still_open)
                    VALUES (?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(competitor, job_id) DO UPDATE SET still_open = 1
                    """,
                    (
                        slug,
                        job["id"],
                        job["title"],
                        job["department"],
                        job["location"],
                        job["url"],
                    ),
                )

            conn.commit()
            logger.info(
                "collect(%s): %d new jobs, %d closed, %d signals emitted",
                slug,
                len(new_jobs),
                len(closed_ids),
                len(signals),
            )
        finally:
            conn.close()

        return signals
