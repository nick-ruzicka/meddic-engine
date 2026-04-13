# MEDDIC Engine — GTM Take-Home

## Problem chosen: Outbounding

## What I built

A fully functional outbound intelligence system that monitors 16,639 SEC-registered investment advisers, filters to 7,115 ICP-qualified targets, and surfaces 247 contacts ready for outreach — scored, enriched, and briefed. The system ingests signals from Exa, Twitter, LinkedIn, and SEC filings; routes them through a MEDDIC-aware Claude scoring layer; and produces a reviewable queue with auto-generated first lines tuned to each contact's signal and buying stage. Every number on every dashboard is live from the pipeline.

## Assumptions I made

- **ICP = PE firms, investment banks, credit funds, hedge funds** — derived from 's confirmed customer list (Oak Hill Advisors, Centerview, Charlesbank, and the 6× ROI case study with Sonja Renander).
- **Primary buyer is CTO / Head of AI / CIO** — the compliance gate means the technical evaluator drives vendor selection, even when the business sponsor is an MD or Partner.
- **Compliance is the #1 objection** — lead every outbound motion with data sovereignty, not feature differentiation. Product talk comes after the legal hurdle is cleared.
- **Signal freshness <7 days = active evaluation window.** A hiring post or press mention more than a week old is context, not a trigger.
- **AlphaSense is the primary incumbent to displace** — but it's a different workflow layer (not a direct replacement), so the sales story is "what AlphaSense can't do," not "a cheaper AlphaSense."
- **Rogo customers are additive, not competitive** — Rogo serves quick Q&A;  serves multi-doc synthesis. Target Rogo firms as expansion land, not a rip-and-replace.
- **20 tier-1 firms is the right starting universe** — quality over quantity for enterprise outbound. A fifth AE meeting booked is worth more than a thousand ignored emails.
- **Tiered monitoring scales the long tail cheaply** — 500 tier-2 firms sit in firmographic-only watch until a signal fires; promotion to tier 1 is the expensive step.

## Architecture

A seven-layer pipeline, each with a narrow contract so layers can be swapped independently.

**CONFIG → SIGNALS → ENRICHMENT → SCORING → BRIEF → FIRST LINE → REVIEW QUEUE**

1. **Config** — YAML-driven ICP rules, scoring weights, and a skill router that assembles a targeted Claude prompt from composable skill files (scoring, voice, MEDDIC analysis) instead of one monolithic prompt.
2. **Signals** — Parallel collectors (Exa for press, TwitterAPI.io for social, Apify for LinkedIn, SEC ADV for firm universe). Each normalizes to a common signal record with type, freshness, and source URL.
3. **Enrichment** — Hunter.io email finding with a waterfall fallback (Hunter → pattern guess → SEC Schedule A). Exa team-page scraping backfills named contacts where Hunter misses.
4. **Scoring** — Four-dimension Claude call (ICP Fit 30%, AI Readiness 25%, Reachability 25%, Signal Freshness 20%). Tier-2 firms get a firmographic-only score (no LLM call, no network) until promoted.
5. **Brief** — MEDDIC-structured account summary: Metrics, Economic Buyer, Decision Criteria, Decision Process, Identify Pain, Champion. Written once per firm, reused across all contacts at that firm.
6. **First Line** — Per-contact opener generated from the signal + brief + voice skill file. Auto-drafted, never auto-sent — AE approves every send.
7. **Review Queue** — Dashboard with approve / skip / flag, filtered by tier, score band, status, and free-text search. One-click export to CSV for any outbound tool that doesn't have an API.

## Tools selected and why

| Tool | Purpose | Why vs. alternative |
|---|---|---|
| SEC ADV | Firm universe | Free, authoritative, 16,639 firms with AUM + structure. No vendor lock-in. |
| Exa AI | Press & web signals | Neural search finds the "deploying AI" post that keyword search misses. |
| TwitterAPI.io | Social signals | ~10× cheaper than the official X API for bulk historical reads. |
| Hunter.io | Email finding | ~80% hit rate on senior titles at known domains; predictable cost. |
| Apify | LinkedIn scraping | Handles anti-scrape rotation without us building it. |
| Claude Haiku | Bulk contact scoring | Fast, cheap ($0.0002/contact), reliable structured JSON output. |
| Claude Sonnet | Account briefs | Better reasoning when a MEDDIC analysis has to hold together. |

**Why NOT Clay:** Clay is a table-stakes enrichment tool — if it's your primary source, every competitor running the same Clay workflow has the same data. The competitive moat is in signal collection and ICP scoring, not in contact enrichment. Clay could slot in as a Hunter fallback, but it shouldn't be the spine.

## Rollout plan

**Phase 1 · Week 1–2 — Pilot.**
Deploy for 2–3 AEs on top 20 tier-1 accounts. All signal review manual. Success metric: first line approval rate. Kill any signal source with <40% approval by end of Week 2.

**Phase 2 · Month 1 — Expand watchlist.**
Turn on the 500 tier-2 watchlist with auto-promote on signal trigger. Add Slack alerts for deploying-stage firms so AEs get notified within 24h of a fresh signal. Start tracking signal-to-meeting conversion.

**Phase 3 · Month 2+ — Full territory deployment.**
Full AE territory mapping, per-rep voice profiles (so the first line sounds like the AE, not like the system), sequence integration (Outreach / Apollo push on approve), and a Pattern Analyst that surfaces scoring drift after each campaign so the skill files stay sharp.

## How I'd measure success

| Metric | Target | Current |
|---|---|---|
| Contacts READY per week | 50+ | 247 total in queue |
| First line approval rate | >60% | Measuring at pilot |
| Signal-to-meeting conversion | >5% | TBD — need pilot data |
| Cost per qualified contact | <$1 | **$0.0002** |
| AE time saved per account | 40 min | Estimated |

The cost-per-contact number is the one that matters most. At $0.0002, the system is effectively free — the constraint isn't spend, it's AE attention. Every metric above is downstream of "is the queue good enough that AEs actually work it."

## Risks and blockers

1. **Signal quality** — LinkedIn and Twitter scrapers are fragile; API changes break collectors overnight.
   *Mitigation:* Exa as the primary source (editorial coverage is more stable than social). Twitter and Apify are secondary, so one collector failing doesn't blind the system.

2. **Email coverage** — Hunter free tier caps and decay on senior-title emails.
   *Mitigation:* Waterfall (Hunter → pattern guess → SEC Schedule A team page). Apollo as a documented third step if Hunter yield drops.

3. **Compliance at enterprise prospects** — 's own buyers will ask where *their* prospect data lives before they let an AE email them.
   *Mitigation:* SQLite on a local / internal server, nothing written to a third-party CRM until the AE explicitly approves. The architecture is CRM-optional by design.

4. **Scoring drift** — Claude model versions change behavior in subtle ways.
   *Mitigation:* Skill files version-controlled; a Pattern Analyst (planned) diffs scoring output between runs and flags step-changes in any dimension's distribution.

5. **Scale** — SQLite handles 1M rows fine but not 1M concurrent writes.
   *Mitigation:* Architecture supports Postgres swap via the `get_db()` abstraction; no SQLite-specific SQL in the hot path.

## What I'd iterate

1. **Slack push alerts** — "Centerview just posted about AI governance" delivered to the territory AE within 24h of the signal firing, with the scored contact attached.
2. **CRM sync** — Approved contacts push to Salesforce/HubSpot on approve, with the signal + first line as an activity note.
3. **Reply feedback loop** — Open, reply, and meeting-booked rates feed back into scoring weights, so the system learns which signal types actually convert for  specifically.
4. **Territory view** — Filter the queue by AE book so reps see only their accounts.
5. **Voice profiles per AE** — First line generator reads the AE's approved history and mimics tone. Removes the "this smells like AI" tell.

## Demo

- **Live:** http://localhost:8765 (contacts dashboard · analytics · ops · methodology)
- **GitHub:** https://github.com/nick-ruzicka/meddic-engine
- **Loom:** [to be recorded]

---

*Generated from actual system data. All numbers above are live from the pipeline — re-run `python scripts/update_analytics.py` to refresh.*
