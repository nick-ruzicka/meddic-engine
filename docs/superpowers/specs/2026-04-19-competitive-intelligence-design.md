# Competitive Intelligence Tab — Design Spec

**Date:** 2026-04-19
**Status:** Approved with modifications

## Overview

Add a "Competitive Intelligence" tab to the MEDDIC Engine dashboard. Three views, each useful on its own:

1. **Briefs** — Structured competitive briefs per competitor, populated on day one
2. **Trajectories** — Historical narrative timelines of how each competitor evolved over 18 months
3. **Live Signals** — Real-time change detection feed (populates after 24h baseline)

## Agreed Modifications (from review)

- **v1 ingestion: sitemap + homepage + Exa search only.** No GitHub, no LinkedIn, no X.
- **Verify URLs** with HEAD request on first run. Flag 404s. Don't block pipeline.
- **UI:** Accordion for brief expansion (not modal). Vertical list for trajectories (not horizontal timeline). Match existing dashboard patterns.
- **Drop `monitorable_surfaces`** from Call A schema. Already known.
- **Standalone script.** `scripts/run_competitive.sh` on its own daily cron. `update_competitive.py` on the 10-min JSON refresh cycle. Don't touch `main.py`.
- **Start with tier 1 only (5 competitors):** AlphaSense, Rogo, F2.ai, Blueflame, Keye. Validate brief quality, then expand to all 15.
- **Token budget target:** $5-10/week. Flag if higher.

## Architecture

### Data Pipeline — `competitive_intel.py`

Standalone Python script. Does not integrate with `main.py`.

**Ingestion (first run + daily refresh):**

1. For each competitor, fetch `sitemap.xml`. Extract all URLs with `lastmod` dates.
2. Fetch homepage, `/about`, `/product` or `/platform`, `/customers`, `/pricing` if they exist. Extract full text.
3. Fetch up to 25 most recent blog post URLs from sitemap. Extract title + full text.
4. Run one Exa web search per competitor: `"{competitor_name}" funding OR Series OR acquired 2025 OR 2026`. Store top 10 results.
5. If sitemap.xml returns 404 or is empty, fall back to homepage scrape + crawl one level deep for blog/product pages.

**Rate limiting:** 1 request per second per domain. Respect robots.txt. Skip aggressively on failures.

### Database Schema

New tables in existing `data/.db`:

```sql
competitors (slug TEXT PRIMARY KEY, name TEXT, url TEXT, tier INTEGER, positioning TEXT, last_ingested TEXT)
competitor_pages (id INTEGER PRIMARY KEY, competitor_slug TEXT, url TEXT, lastmod TEXT, content TEXT, fetched_at TEXT)
competitor_news (id INTEGER PRIMARY KEY, competitor_slug TEXT, title TEXT, url TEXT, source TEXT, published_at TEXT, snippet TEXT)
competitor_briefs (id INTEGER PRIMARY KEY, competitor_slug TEXT, brief_json TEXT, generated_at TEXT, model TEXT)
competitor_trajectories (id INTEGER PRIMARY KEY, competitor_slug TEXT, trajectory_json TEXT, generated_at TEXT, model TEXT)
competitor_signals (id INTEGER PRIMARY KEY, competitor_slug TEXT, signal_type TEXT, summary TEXT, relevance TEXT, category TEXT, source_url TEXT, detected_at TEXT)
```

### Claude Analysis — Call A (Static Brief)

Runs after ingestion on first run. Refreshes weekly. Cache for 7 days.

Feed Claude all pages + news for one competitor. System prompt instructs structured JSON output:

```json
{
  "positioning_self": "How they describe themselves (2 sentences)",
  "positioning_actual": "How they actually compete (2-3 sentences)",
  "target_icp": "Who they sell to (specific segments)",
  "pricing_signals": "Any public signal on pricing/packaging/contract size",
  "key_differentiation": "The one thing they say makes them different",
  "weakness_vs_": "Where  likely wins (cite specific capability gap)",
  "strength_vs_": "Where they may be ahead (cite specific capability)",
  "recent_moves": ["3-5 bullets of most recent public activity"],
  "threat_level": "high | medium | low",
  "threat_reasoning": "One sentence on why"
}
```

Every claim must have source attribution traceable to a specific page URL.

### Claude Analysis — Call B (Historical Trajectory)

Runs on first ingestion. Refreshes monthly. Cache for 30 days.

Feed Claude all blog posts + news sorted chronologically:

```json
{
  "eras": [
    {
      "period": "e.g., Q2 2025",
      "dominant_theme": "What they were about",
      "key_moments": ["3-5 specific events with dates"],
      "positioning": "How they were positioning"
    }
  ],
  "inflection_points": ["Moments where strategy shifted"],
  "trajectory_summary": "2-3 sentences on where they're heading"
}
```

### Signal Detection (daily, after baseline)

On every run after the first, compare current ingestion to previous snapshot. For new content (new blog post, sitemap change, new news item), call Claude:

```json
{
  "summary": "one sentence",
  "category": "product-launch | customer-win | funding | positioning-shift | hiring | cosmetic | other",
  "relevance": "high | medium | low",
  "relevance_reasoning": "one sentence",
  "source_url": "..."
}
```

## UI — `/competitive.html`

**Nav placement:** Top nav, between METHODOLOGY and CONTACTS. Label: **COMPETITIVE**. Route: `/competitive.html`.

**Design:** Dark theme matching `index.html`. Reuse all tokens from `shared.css`.

### Top Header

- Eyebrow: COMPETITIVE INTELLIGENCE
- H1 serif: "The competitive perimeter."
- Subline: "5 companies actively competing for 's buyer. Briefs refreshed weekly. Signals detected daily."
- Stat row: `5 COMPETITORS` / `{N} SIGNALS THIS WEEK` / `{N} HIGH THREAT` / `{last_updated}`

### Tab 1: "Briefs" (default)

Grid of competitor cards. Each card:
- Name, tier badge, threat-level dot (red/amber/gray)
- `positioning_actual` — one line
- `key_differentiation` — one line
- Last updated date
- Click expands **accordion** (not modal) with full brief fields

Below grid: "How these briefs are built" — one paragraph on methodology (trust anchor).

### Tab 2: "Trajectories"

Dropdown to select competitor (default: F2.ai). **Vertical list** (not horizontal timeline):
- Era headers with period label
- Each era shows dominant theme, key moments, positioning
- Inflection points highlighted
- Trajectory summary at bottom

### Tab 3: "Live Signals"

Reverse-chronological feed of signals from last 30 days. Card format:

```
[CATEGORY BADGE] [COMPETITOR] [RELEVANCE chip] [TIME AGO]
One-sentence summary
-> View source
```

Sidebar filters: tier, competitor, category, relevance.

Day-one state: "Live signals start appearing after 24h of monitoring."

## Competitor List (v1 — Tier 1 only)

```python
COMPETITORS_V1 = [
    ("alphasense", "AlphaSense", "https://www.alphasense.com", 1, "Incumbent, acquired Tegus 2024"),
    ("rogo", "Rogo", "https://rogo.ai", 1, "Dominant in sell-side IB, $75M Series C"),
    ("f2", "F2.ai", "https://f2.ai", 1, "Deterministic spreadsheet computation, explicit  challenger"),
    ("blueflame", "Blueflame AI", "https://blueflame.ai", 1, "Acquired by Datasite, embedded in VDR"),
    ("keye", "Keye", "https://keye.co", 1, "YC F24, Odin co-pilot, built by investors"),
]
```

Remaining 10 competitors (tier 2-3) added after validating brief quality on these 5.

## Scripts & Deployment

| Script | Purpose | Cadence |
|--------|---------|---------|
| `competitive_intel.py` | Ingestion + Claude analysis | Daily (own cron via `scripts/run_competitive.sh`) |
| `scripts/update_competitive.py` | Generate `competitive_data.json` from DB | Every 10 min (added to `refresh_dashboards.sh`) |
| `scripts/run_competitive.sh` | Wrapper for daily competitive pipeline | Daily cron, separate from main pipeline |

## Build Order

1. Build ingestion pipeline. Test on F2.ai only. Show brief output for validation.
2. If F2.ai brief quality is good, expand to all 5 tier-1 competitors.
3. Run Call A + Call B for all 5. Populate briefs + trajectories.
4. Build the UI page with three tabs. Tabs 1-2 from Call A/B. Tab 3 empty on day one.
5. Add to `refresh_dashboards.sh` for JSON regeneration.
6. Set up daily cron for `run_competitive.sh`.
7. Deploy to Hetzner same pattern as main signal engine.

## Cost Constraints

- Target: $5-10/week on Claude API calls
- Cache briefs for 7 days, trajectories for 30 days
- Signal detection is the only daily Claude call
- If costs exceed target, flag and decide on caching/trimming
