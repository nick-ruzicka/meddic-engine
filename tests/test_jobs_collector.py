"""Tests for jobs_collector.py and job_classifier.py.

Covers:
  - Greenhouse response parsing
  - Ashby response parsing
  - job_classifier keyword detection (vertical_expansion, launch_related, generic)
  - baseline() stores jobs in ci_jobs_baseline
  - collect() detects new postings and emits RawSignals
  - collect() handles null jobs_url gracefully (returns empty list)
  - engineering_surge classification when 3+ engineering roles in a batch
"""

import sqlite3
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from competitive.collectors.job_classifier import classify_job
from competitive.collectors.jobs_collector import (
    JobsCollector,
    _fetch_ashby,
    _fetch_greenhouse,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def in_memory_db(tmp_path, monkeypatch):
    """Redirect get_db() to a fresh in-memory SQLite database for each test."""
    db_file = str(tmp_path / "test.db")

    def _get_db():
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr("competitive.collectors.jobs_collector.get_db", _get_db)
    return db_file


@pytest.fixture()
def collector():
    return JobsCollector()


@pytest.fixture()
def greenhouse_competitor():
    return {
        "slug": "alphasense",
        "name": "AlphaSense",
        "jobs_url": "https://boards.greenhouse.io/alphasense",
        "jobs_source": "greenhouse",
    }


@pytest.fixture()
def ashby_competitor():
    return {
        "slug": "rogo",
        "name": "Rogo",
        "jobs_url": "https://jobs.ashbyhq.com/rogo",
        "jobs_source": "ashby",
    }


@pytest.fixture()
def null_jobs_competitor():
    return {
        "slug": "f2",
        "name": "F2.ai",
        "jobs_url": None,
        "jobs_source": None,
    }


# ─── Greenhouse parsing ───────────────────────────────────────────────────────


GREENHOUSE_RESPONSE = {
    "jobs": [
        {
            "id": 101,
            "title": "Solutions Engineer — Private Credit",
            "location": {"name": "New York, NY"},
            "departments": [{"name": "Sales"}],
            "absolute_url": "https://boards.greenhouse.io/alphasense/jobs/101",
            "updated_at": "2026-04-15T12:00:00Z",
        },
        {
            "id": 102,
            "title": "Backend Engineer",
            "location": {"name": "Remote"},
            "departments": [],
            "absolute_url": "https://boards.greenhouse.io/alphasense/jobs/102",
            "updated_at": "2026-04-14T08:00:00Z",
        },
    ]
}


def test_greenhouse_parsing():
    """_fetch_greenhouse() correctly normalises Greenhouse API response."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = GREENHOUSE_RESPONSE
    mock_resp.raise_for_status.return_value = None

    with patch("competitive.collectors.jobs_collector.requests.get", return_value=mock_resp):
        jobs = _fetch_greenhouse("alphasense")

    assert len(jobs) == 2

    job0 = jobs[0]
    assert job0["id"] == "101"
    assert job0["title"] == "Solutions Engineer — Private Credit"
    assert job0["department"] == "Sales"
    assert job0["location"] == "New York, NY"
    assert "101" in job0["url"]

    job1 = jobs[1]
    assert job1["id"] == "102"
    assert job1["department"] == ""  # empty departments list handled gracefully


def test_greenhouse_api_failure_returns_empty():
    """_fetch_greenhouse() returns [] on network error without raising."""
    import requests as req_lib

    with patch(
        "competitive.collectors.jobs_collector.requests.get",
        side_effect=req_lib.RequestException("timeout"),
    ):
        jobs = _fetch_greenhouse("alphasense")

    assert jobs == []


# ─── Ashby parsing ───────────────────────────────────────────────────────────


ASHBY_RESPONSE = {
    "jobs": [
        {
            "id": "abc-123",
            "title": "Product Marketing Manager",
            "departmentName": "Marketing",
            "location": "San Francisco, CA",
            "jobUrl": "https://jobs.ashbyhq.com/rogo/abc-123",
            "publishedAt": "2026-04-10T09:00:00.000Z",
        },
        {
            "id": "def-456",
            "title": "Security Engineer",
            "departmentName": "Engineering",
            "location": "Remote",
            "jobUrl": "https://jobs.ashbyhq.com/rogo/def-456",
            "publishedAt": "2026-04-12T11:00:00.000Z",
        },
    ]
}


def test_ashby_parsing():
    """_fetch_ashby() correctly normalises Ashby API response."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = ASHBY_RESPONSE
    mock_resp.raise_for_status.return_value = None

    with patch("competitive.collectors.jobs_collector.requests.get", return_value=mock_resp):
        jobs = _fetch_ashby("rogo")

    assert len(jobs) == 2

    job0 = jobs[0]
    assert job0["id"] == "abc-123"
    assert job0["title"] == "Product Marketing Manager"
    assert job0["department"] == "Marketing"
    assert job0["location"] == "San Francisco, CA"
    assert job0["url"] == "https://jobs.ashbyhq.com/rogo/abc-123"
    assert job0["published_at"].startswith("2026-04-10")

    job1 = jobs[1]
    assert job1["id"] == "def-456"
    assert job1["title"] == "Security Engineer"


def test_ashby_api_failure_returns_empty():
    """_fetch_ashby() returns [] on network error without raising."""
    import requests as req_lib

    with patch(
        "competitive.collectors.jobs_collector.requests.get",
        side_effect=req_lib.RequestException("connection refused"),
    ):
        jobs = _fetch_ashby("rogo")

    assert jobs == []


# ─── job_classifier ───────────────────────────────────────────────────────────


def test_classify_vertical_expansion():
    result = classify_job("Solutions Engineer — Private Credit", "")
    assert result["category"] == "vertical_expansion"
    assert "private credit" in result["keywords_matched"]
    assert result["confidence"] == 0.8


def test_classify_vertical_expansion_hedge_fund():
    result = classify_job("Head of Sales — Hedge Fund", "covering asset management clients")
    assert result["category"] == "vertical_expansion"
    assert result["confidence"] == 0.8


def test_classify_launch_related():
    result = classify_job("Product Marketing Manager — Go-to-Market", "")
    assert result["category"] == "launch_related"
    assert result["confidence"] == 0.7


def test_classify_launch_related_gtm():
    result = classify_job("GTM Strategy Lead", "developer relations and launch planning")
    assert result["category"] == "launch_related"
    assert result["confidence"] == 0.7


def test_classify_enterprise_readiness():
    result = classify_job("Security Engineer — SOC 2", "")
    assert result["category"] == "enterprise_readiness"
    assert result["confidence"] == 0.6


def test_classify_generic():
    result = classify_job("Office Manager", "general administrative support")
    assert result["category"] == "generic"
    assert result["keywords_matched"] == []
    assert result["confidence"] == 0.2


def test_classify_generic_recruiter():
    result = classify_job("Senior Recruiter", "talent acquisition team")
    assert result["category"] == "generic"
    assert result["confidence"] == 0.2


def test_classify_case_insensitive():
    """Classification is case-insensitive."""
    result = classify_job("PRIVATE EQUITY ASSOCIATE", "")
    assert result["category"] == "vertical_expansion"


# ─── baseline() ──────────────────────────────────────────────────────────────


def test_baseline_stores_jobs(collector, greenhouse_competitor, in_memory_db):
    """baseline() inserts all fetched jobs into ci_jobs_baseline."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = GREENHOUSE_RESPONSE
    mock_resp.raise_for_status.return_value = None

    with patch("competitive.collectors.jobs_collector.requests.get", return_value=mock_resp):
        collector.baseline(greenhouse_competitor)

    conn = sqlite3.connect(in_memory_db)
    rows = conn.execute(
        "SELECT * FROM ci_jobs_baseline WHERE competitor = 'alphasense'"
    ).fetchall()
    conn.close()

    assert len(rows) == 2
    titles = {row[3] for row in rows}  # column index 3 = title
    assert "Solutions Engineer — Private Credit" in titles
    assert "Backend Engineer" in titles


def test_baseline_idempotent(collector, greenhouse_competitor, in_memory_db):
    """Calling baseline() twice does not duplicate rows."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = GREENHOUSE_RESPONSE
    mock_resp.raise_for_status.return_value = None

    with patch("competitive.collectors.jobs_collector.requests.get", return_value=mock_resp):
        collector.baseline(greenhouse_competitor)
        collector.baseline(greenhouse_competitor)

    conn = sqlite3.connect(in_memory_db)
    count = conn.execute(
        "SELECT COUNT(*) FROM ci_jobs_baseline WHERE competitor = 'alphasense'"
    ).fetchone()[0]
    conn.close()

    assert count == 2


def test_baseline_null_jobs_url(collector, null_jobs_competitor, in_memory_db):
    """baseline() with null jobs_url returns without error and inserts nothing.

    The table may not be created at all when jobs_url is null (early return),
    so we verify the call completes without raising an exception.
    """
    # Should not raise
    collector.baseline(null_jobs_competitor)

    # If the table was created, no rows should exist for competitor 'f2'
    conn = sqlite3.connect(in_memory_db)
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ci_jobs_baseline'"
    ).fetchone()
    if table_exists:
        count = conn.execute(
            "SELECT COUNT(*) FROM ci_jobs_baseline WHERE competitor = 'f2'"
        ).fetchone()[0]
        assert count == 0
    conn.close()


# ─── collect() ───────────────────────────────────────────────────────────────


def test_collect_detects_new_posting(collector, greenhouse_competitor, in_memory_db):
    """collect() emits a RawSignal for each new job not in baseline."""
    # Baseline has only job 101; job 102 is new
    baseline_response = {
        "jobs": [GREENHOUSE_RESPONSE["jobs"][0]]  # only job 101
    }
    full_response = GREENHOUSE_RESPONSE  # jobs 101 + 102

    mock_baseline = MagicMock()
    mock_baseline.json.return_value = baseline_response
    mock_baseline.raise_for_status.return_value = None

    mock_collect = MagicMock()
    mock_collect.json.return_value = full_response
    mock_collect.raise_for_status.return_value = None

    with patch(
        "competitive.collectors.jobs_collector.requests.get",
        side_effect=[mock_baseline, mock_collect],
    ):
        collector.baseline(greenhouse_competitor)
        signals = collector.collect(greenhouse_competitor)

    assert len(signals) == 1
    sig = signals[0]
    assert sig.competitor == "alphasense"
    assert sig.source == "jobs"
    assert sig.signal_type == "job_posting"
    assert sig.payload["title"] == "Backend Engineer"
    assert isinstance(sig.observed_at, datetime)


def test_collect_no_new_jobs(collector, greenhouse_competitor, in_memory_db):
    """collect() emits no signals when all current jobs were already baselined."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = GREENHOUSE_RESPONSE
    mock_resp.raise_for_status.return_value = None

    with patch("competitive.collectors.jobs_collector.requests.get", return_value=mock_resp):
        collector.baseline(greenhouse_competitor)
        signals = collector.collect(greenhouse_competitor)

    assert signals == []


def test_collect_marks_closed_jobs(collector, greenhouse_competitor, in_memory_db):
    """collect() marks removed jobs as still_open=0 in baseline."""
    # Baseline has both; current response has only job 102
    trimmed_response = {"jobs": [GREENHOUSE_RESPONSE["jobs"][1]]}

    mock_baseline = MagicMock()
    mock_baseline.json.return_value = GREENHOUSE_RESPONSE
    mock_baseline.raise_for_status.return_value = None

    mock_collect = MagicMock()
    mock_collect.json.return_value = trimmed_response
    mock_collect.raise_for_status.return_value = None

    with patch(
        "competitive.collectors.jobs_collector.requests.get",
        side_effect=[mock_baseline, mock_collect],
    ):
        collector.baseline(greenhouse_competitor)
        collector.collect(greenhouse_competitor)

    conn = sqlite3.connect(in_memory_db)
    row = conn.execute(
        "SELECT still_open FROM ci_jobs_baseline WHERE competitor='alphasense' AND job_id='101'"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == 0  # marked closed


def test_collect_null_jobs_url_returns_empty(collector, null_jobs_competitor, in_memory_db):
    """collect() returns [] immediately when jobs_url is null — no API call made."""
    with patch("competitive.collectors.jobs_collector.requests.get") as mock_get:
        signals = collector.collect(null_jobs_competitor)

    assert signals == []
    mock_get.assert_not_called()


def test_collect_signal_confidence_vertical_expansion(
    collector, greenhouse_competitor, in_memory_db
):
    """collect() sets confidence=0.8 for vertical_expansion jobs."""
    # Fresh baseline (empty), then collect a private credit role
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "jobs": [
            {
                "id": 200,
                "title": "Solutions Engineer — Private Credit",
                "location": {"name": "New York"},
                "departments": [{"name": "Sales"}],
                "absolute_url": "https://example.com/jobs/200",
                "updated_at": "2026-04-15T00:00:00Z",
            }
        ]
    }
    mock_resp.raise_for_status.return_value = None

    with patch("competitive.collectors.jobs_collector.requests.get", return_value=mock_resp):
        # Empty baseline first (call baseline with empty jobs)
        pass

    mock_empty = MagicMock()
    mock_empty.json.return_value = {"jobs": []}
    mock_empty.raise_for_status.return_value = None

    with patch(
        "competitive.collectors.jobs_collector.requests.get",
        side_effect=[mock_empty, mock_resp],
    ):
        collector.baseline(greenhouse_competitor)
        signals = collector.collect(greenhouse_competitor)

    assert len(signals) == 1
    assert signals[0].confidence == 0.8
    assert signals[0].payload["category"] == "vertical_expansion"


def test_collect_engineering_surge(collector, ashby_competitor, in_memory_db):
    """collect() classifies new jobs as engineering_surge when 3+ engineering roles appear."""
    eng_jobs = [
        {
            "id": f"eng-{i}",
            "title": f"Backend Engineer {i}",
            "departmentName": "Engineering",
            "location": "Remote",
            "jobUrl": f"https://jobs.ashbyhq.com/rogo/eng-{i}",
            "publishedAt": "2026-04-15T00:00:00Z",
        }
        for i in range(3)
    ]

    mock_empty = MagicMock()
    mock_empty.json.return_value = {"jobs": []}
    mock_empty.raise_for_status.return_value = None

    mock_full = MagicMock()
    mock_full.json.return_value = {"jobs": eng_jobs}
    mock_full.raise_for_status.return_value = None

    with patch(
        "competitive.collectors.jobs_collector.requests.get",
        side_effect=[mock_empty, mock_full],
    ):
        collector.baseline(ashby_competitor)
        signals = collector.collect(ashby_competitor)

    assert len(signals) == 3
    for sig in signals:
        assert sig.payload["category"] == "engineering_surge"
        assert sig.confidence == 0.9


def test_collect_ashby_new_posting(collector, ashby_competitor, in_memory_db):
    """collect() correctly processes new Ashby postings."""
    mock_empty = MagicMock()
    mock_empty.json.return_value = {"jobs": []}
    mock_empty.raise_for_status.return_value = None

    mock_full = MagicMock()
    mock_full.json.return_value = ASHBY_RESPONSE
    mock_full.raise_for_status.return_value = None

    with patch(
        "competitive.collectors.jobs_collector.requests.get",
        side_effect=[mock_empty, mock_full],
    ):
        collector.baseline(ashby_competitor)
        signals = collector.collect(ashby_competitor)

    assert len(signals) == 2
    titles = {s.payload["title"] for s in signals}
    assert "Product Marketing Manager" in titles
    assert "Security Engineer" in titles

    # Product Marketing Manager -> launch_related
    pmm = next(s for s in signals if "Product Marketing" in s.payload["title"])
    assert pmm.payload["category"] == "launch_related"
    assert pmm.confidence == 0.7

    # Security Engineer -> enterprise_readiness
    se = next(s for s in signals if s.payload["title"] == "Security Engineer")
    assert se.payload["category"] == "enterprise_readiness"
    assert se.confidence == 0.6
