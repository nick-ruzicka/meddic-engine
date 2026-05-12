# Competitive Signal Engine v2 — Specification

> **Purpose:** Daily pipeline that surfaces leading indicators of competitor moves — signals that predict product launches, vertical expansion, or strategic shifts BEFORE the blog post goes live.

> **Database:** SQLite (existing `data/meddic.db`). Migrate to Postgres when signal volume exceeds ~10k/day or we add multi-tenant support.

---

## Competitors

Six competitors monitored. Config in `config/competitors.yaml`.

| Slug | Name | Tier | Why |
|------|------|------|-----|
| f2 | F2.ai | 1 | Direct challenger, publishing attack content against the platform |
| keye | Keye | 1 | PE-specific AI co-pilot, same buyer persona |
| blueflame | Blueflame AI | 1 | Acquired by Datasite, embedded in VDR workflow |
| rogo | Rogo | 1 | $165M raised, 25k users, expanding from IB into PE |
| alphasense | AlphaSense | 1 | $500M ARR incumbent, acquired Tegus |
| harvey | Harvey AI | 1 | $3B valuation, legal-adjacent, same enterprise buyer |

---

## Signal Types

Five collectors, each monitoring a different signal source:

| # | Collector | Signal Type | Lead Time | What It Detects |
|---|-----------|------------|-----------|-----------------|
| 1 | Sitemap | `new_url`, `content_change`, `url_removed` | Hours-days | New pages appearing before they're linked publicly |
| 2 | Jobs | `job_posting` | 60-90 days | Hiring patterns that predict launches or expansion |
| 3 | DNS | `new_subdomain` | Days-weeks | New subdomains for upcoming products/docs |
| 4 | GitHub | `commit_burst`, `new_repo`, `star_spike` | Weeks | Code activity signaling development velocity |
| 5 | Exa | `trending_mention` | Days | News/forum/Reddit mentions we haven't seen before |

---

## Collector Interface

Every collector implements this interface. Defined in `competitive/collectors/base.py`.

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from abc import ABC, abstractmethod


@dataclass
class RawSignal:
    competitor: str        # slug, e.g. "alphasense"
    source: str            # collector name: "sitemap" | "jobs" | "dns" | "github" | "exa"
    signal_type: str       # see Signal Types table above
    payload: dict          # type-specific data (see Payload Schemas below)
    observed_at: datetime  # when the collector detected it
    raw_url: Optional[str] = None  # link to the source
    confidence: float = 0.5        # 0.0-1.0, set by collector


class Collector(ABC):
    name: str  # "sitemap", "jobs", "dns", "github", "exa"

    @abstractmethod
    def collect(self, competitor: dict) -> list[RawSignal]:
        """Run once per competitor per day. Return raw signals detected since last run."""

    @abstractmethod
    def baseline(self, competitor: dict) -> None:
        """One-time: record current state as baseline for future diffs."""
```

---

## Classifier Interface

Defined in `competitive/classifier/base.py`. Consumes RawSignals, produces ClassifiedSignals.

```python
@dataclass
class ClassifiedSignal(RawSignal):
    category: str = ""              # see Classification Taxonomy below
    predictive_score: float = 0.0   # 0.0-1.0
    lead_time_estimate: str = ""    # "immediate" | "2-4 weeks" | "60-90 days"
    sales_takeaway: str = ""          # one sentence, sales-angle framing
```

---

## Classification Taxonomy

Workers MUST classify signals into exactly these categories. Examples are deterministic — if your signal matches an example pattern, use that category.

### `launch_signal` — Product is shipping or about to ship
- New `/product/...` or `/platform/...` URL appears in sitemap before any blog post links to it
- New subdomain `app-v2.competitor.com` or `beta.competitor.com` detected
- GitHub: 5+ commits/day to a new repo named after a product concept
- Exa: "launches", "announces", "now available" in headline about the competitor
- Job posting for "Product Marketing Manager" with launch-related keywords ("go-to-market", "launch", "announcement")

### `hiring_signal` — Hiring pattern predicts expansion 60-90 days out
- Job posting for "Solutions Engineer — Private Credit" or "Enterprise AE — Financial Services" (vertical expansion)
- 3+ engineering roles posted in same week (development acceleration)
- Job posting for "Head of [new vertical]" (new market entry)
- Job posting for "Security/Compliance Engineer" (enterprise readiness push)
- Job posting for "Developer Relations" or "Technical Writer" (platform/API play)

### `infrastructure_signal` — Technical infrastructure change
- New subdomain `docs.competitor.com` or `api.competitor.com` (API/platform launch incoming)
- New subdomain `status.competitor.com` (enterprise reliability positioning)
- GitHub: new repo with "sdk", "api", "client" in name
- SSL certificate issued for new subdomain

### `content_signal` — Strategic positioning or messaging shift
- New blog post URL in sitemap with competitive keywords ("vs", "alternative", "comparison", "migration")
- Exa: competitor mentioned in "best [category] tools" or "alternative to []"
- Case study URL appearing (customer-win signal)
- Pricing page content change detected via hash

### `noise` — Not actionable
- Sitemap timestamp change with no content change
- Blog post about industry trends with no competitive angle
- Job posting for generic roles (office manager, recruiter)
- GitHub: dependabot/renovate commits, typo fixes
- Navigation or footer link changes

---

## Payload Schemas

Each signal type produces a specific payload shape:

```python
# Sitemap signals
{"url": "https://...", "page_type": "product", "change": "new" | "modified" | "removed",
 "old_hash": "abc123" | None, "new_hash": "def456"}

# Job signals
{"title": "Solutions Engineer — Private Credit", "location": "New York",
 "department": "Sales", "url": "https://jobs.ashbyhq.com/...",
 "posting_date": "2026-04-15", "keywords": ["private credit", "enterprise"]}

# DNS signals
{"subdomain": "docs.rogo.ai", "record_type": "A" | "CNAME",
 "first_seen": "2026-04-19", "resolves_to": "1.2.3.4"}

# GitHub signals
{"org": "alphasense", "repo": "new-repo-name",
 "event": "commit_burst" | "new_repo" | "star_spike",
 "count": 47, "period": "7d", "top_authors": ["alice", "bob"]}

# Exa signals
{"title": "AlphaSense launches new PE module",
 "url": "https://techcrunch.com/...", "published_at": "2026-04-18",
 "snippet": "...", "query_matched": "AlphaSense product launch"}
```

---

## Database Schema

Single SQLite database (`data/meddic.db`). Each collector owns its baseline table. The shared `ci_signals` table stores all classified signals.

### Shared signals table (migration 005)

```sql
CREATE TABLE IF NOT EXISTS ci_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    competitor TEXT NOT NULL,
    source TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    payload TEXT NOT NULL,  -- JSON
    observed_at TEXT NOT NULL,
    raw_url TEXT,
    confidence REAL NOT NULL DEFAULT 0.5,
    category TEXT,
    predictive_score REAL,
    lead_time_estimate TEXT,
    sales_takeaway TEXT,
    classified_at TEXT,
    sent_in_digest_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_ci_signals_observed ON ci_signals(observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_ci_signals_competitor ON ci_signals(competitor, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_ci_signals_category ON ci_signals(category);
```

### Collector baseline tables

Each collector owns its own baseline table for tracking state between runs:

- `ci_sitemap_baseline` — (competitor, url, content_hash, last_seen)
- `ci_jobs_baseline` — (competitor, job_id, title, url, first_seen, still_open)
- `ci_dns_baseline` — (competitor, subdomain, record_type, first_seen, last_seen)
- `ci_github_baseline` — (competitor, org, repo, last_commit_sha, last_checked)
- `ci_exa_baseline` — (competitor, url, title, first_seen)

---

## Pipeline Orchestration

`run_daily.py` (coordinator-owned) runs all 5 collectors, classifies signals, and triggers digest on Mondays.

```
6:00 AM ET daily:
  1. Load config/competitors.yaml
  2. For each collector × competitor: run collect()
  3. Deduplicate signals (same competitor + source + signal_type + raw_url = skip)
  4. Classify all new RawSignals → ClassifiedSignals
  5. Insert into ci_signals
  6. If Monday: generate weekly digest, format for email + Slack
  7. Log run stats (signals found, classified, cost)
```

---

## Digest Format

Monday digest for the GTM lead. Grouped by competitor, ranked by predictive_score.

```
 COMPETITIVE SIGNALS — Week of April 14, 2026
5 leading indicators across 6 competitors

---

ROGO [HIGH THREAT]
  [LAUNCH] New /product/portfolio-analytics URL detected in sitemap
           Lead time: 2-4 weeks
           → Rogo is likely about to launch a portfolio analytics
             product that competes with the platform's cross-document
             querying. Watch for blog post.
           Source: sitemap diff (Apr 16)

  [HIRING] 3 "Solutions Engineer — Private Credit" roles posted
           Lead time: 60-90 days
           → Rogo is building a private credit sales team. They're
             coming for the platform's core vertical.
           Source: ashby.com/rogo (Apr 15)

F2.AI [MEDIUM THREAT]
  [CONTENT] New comparison blog: "/blog/f2-vs-platform"
            Lead time: immediate
            → Direct attack content. Brief your AEs on the
              rebuttal points in the competitive brief.
            Source: sitemap diff (Apr 17)

---
3 noise signals filtered. Full log: [link]
```
