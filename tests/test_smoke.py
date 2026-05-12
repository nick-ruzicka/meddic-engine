"""Smoke tests for the MEDDIC Engine.

Covers: API auth, stats + contacts endpoints, dashboard page renders,
brief route. These are integration tests — they hit the real DB and
Flask app; make sure `data/meddic.db` exists and has been seeded.
"""

import os
import sys
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import create_app  # noqa: E402

API_KEY = os.getenv("API_KEY", "-demo-2026")


@pytest.fixture(scope="module")
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ── Auth ─────────────────────────────────────────────────────────────────

def test_stats_no_auth(client):
    r = client.get("/api/stats")
    assert r.status_code == 401


def test_stats_wrong_auth(client):
    r = client.get("/api/stats", headers={"X-API-Key": "not-the-key"})
    assert r.status_code == 401


# ── /api/stats ──────────────────────────────────────────────────────────

def test_stats_endpoint(client):
    r = client.get("/api/stats", headers={"X-API-Key": API_KEY})
    assert r.status_code == 200
    data = r.get_json()
    # stats returns a dict of aggregates
    for required in ("total_contacts", "total_firms", "strong_match",
                     "good_match", "pending"):
        assert required in data, f"missing key: {required}"
    assert data["total_contacts"] >= 0


# ── /api/contacts ───────────────────────────────────────────────────────

def test_contacts_endpoint(client):
    r = client.get("/api/contacts?limit=5",
                   headers={"X-API-Key": API_KEY})
    assert r.status_code == 200
    data = r.get_json()
    assert "contacts" in data
    assert isinstance(data["contacts"], list)
    assert "count" in data
    assert data["count"] == len(data["contacts"])


def test_contacts_bad_status(client):
    r = client.get("/api/contacts?status=bogus",
                   headers={"X-API-Key": API_KEY})
    assert r.status_code == 400


# ── Dashboard pages render ──────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/",
    "/analytics.html",
    "/ops.html",
    "/methodology.html",
])
def test_dashboard_page_renders(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert b"<!doctype html>" in r.data.lower() or b"<!DOCTYPE html>" in r.data


# ── Data JSON files served ──────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/contacts_data.json",
    "/analytics_data.json",
    "/ops_data.json",
])
def test_data_json_served(client, path):
    r = client.get(path)
    # 200 if generated, 404 if fresh checkout before update_*.py
    assert r.status_code in (200, 404)


# ── Brief route ─────────────────────────────────────────────────────────

def test_brief_route(client):
    # 200 if queue row exists, 404 otherwise — both acceptable
    r = client.get("/brief/1")
    assert r.status_code in (200, 404, 401)


# ── Invariants on real data ─────────────────────────────────────────────

def test_stats_totals_sane(client):
    r = client.get("/api/stats", headers={"X-API-Key": API_KEY})
    d = r.get_json()
    assert d["strong_match"] + d["good_match"] <= d["total_contacts"]
    assert d["pending"] + d["approved"] + d["skipped"] + d["flagged"] <= d["total_contacts"] + 200  # queue can exceed contacts if tier 2 placeholders exist
