#!/usr/bin/env python3
"""scripts/backfill_linkedin.py

Backfill contacts.linkedin_url for already-loaded contacts.

For each firm that has contacts with no LinkedIn URL:
  1. Fetch that firm's team page directly (uses firms.domain to guess /team,
     /leadership, /people, /our-team; stops at first 200)
  2. Extract all linkedin.com/in/<slug> URLs from raw HTML
  3. Match each existing contact (by first+last token overlap on slug)
  4. UPDATE contacts.linkedin_url where a confident match is found

Safe to re-run — skips contacts that already have linkedin_url set.
No Claude calls. No Hunter calls. Pure HTTP + regex.
"""
from __future__ import annotations

import os
import sys
import time
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv()

import requests
from enrichment.exa_enricher import _extract_linkedin_urls, _match_linkedin

UA = "Mozilla/5.0 (MEDDIC Engine research@.com)"
TIMEOUT = 15
SLEEP = 0.6

TEAM_PATHS = ["/team", "/leadership", "/our-team", "/people", "/professionals",
              "/our-people", "/about/team", "/about/leadership"]


def fetch_team_html(domain: str) -> str:
    """Try common team-page URL patterns; return raw HTML of the first 200."""
    if not domain:
        return ""
    for scheme in ("https://www.", "https://"):
        for path in TEAM_PATHS:
            url = f"{scheme}{domain}{path}"
            try:
                r = requests.get(url, headers={"User-Agent": UA},
                                 timeout=TIMEOUT, allow_redirects=True)
                if r.status_code == 200 and len(r.text) > 2000:
                    return r.text
            except Exception:
                continue
            time.sleep(0.05)
    return ""


def main() -> int:
    from database import get_db
    conn = get_db()
    conn.execute("PRAGMA busy_timeout = 30000")

    firms = conn.execute("""
        SELECT f.id, f.name, f.domain,
               COUNT(c.id) AS total,
               SUM(CASE WHEN COALESCE(c.linkedin_url,'') = '' THEN 1 ELSE 0 END) AS missing
          FROM firms f
     LEFT JOIN contacts c ON c.firm_id = f.id
         WHERE f.domain IS NOT NULL AND f.domain != ''
           AND f.tier = 1
         GROUP BY f.id
        HAVING missing > 0 AND total > 0
         ORDER BY missing DESC
    """).fetchall()

    print(f"Scanning {len(firms)} firms with missing LinkedIn URLs\n")

    total_updated = 0
    for firm in firms:
        html = fetch_team_html(firm["domain"])
        if not html:
            print(f"  ✗ {firm['name']:<32} — no team page found")
            time.sleep(SLEEP)
            continue

        li_urls = _extract_linkedin_urls(html)
        if not li_urls:
            print(f"  · {firm['name']:<32} — page fetched, 0 LinkedIn URLs in HTML")
            time.sleep(SLEEP)
            continue

        contacts = conn.execute(
            """SELECT id, name FROM contacts
                WHERE firm_id = ? AND COALESCE(linkedin_url,'') = ''""",
            (firm["id"],)
        ).fetchall()

        matched = 0
        for c in contacts:
            li = _match_linkedin(c["name"], li_urls)
            if li:
                conn.execute(
                    "UPDATE contacts SET linkedin_url = ?, updated_at = datetime('now') WHERE id = ?",
                    (li, c["id"])
                )
                matched += 1
        conn.commit()
        total_updated += matched
        print(f"  ✓ {firm['name']:<32} {len(li_urls):>3} URLs on page → {matched} matched")
        time.sleep(SLEEP)

    print(f"\n✓ Backfill complete — {total_updated} contacts now have linkedin_url")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
