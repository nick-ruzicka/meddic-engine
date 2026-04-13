# MEDDIC Engine — Presentation Outline

**Format:** 10 slides, 15–20 minute walkthrough, live demo in the middle.

---

## Slide 1 · Title

# MEDDIC Engine — GTM Take-Home

Nicholas Ruzicka · April 2026

One-liner:
> "Outbound intelligence for  — $25T AUM of financial institutions, scored, researched, and briefed before the first email."

---

## Slide 2 · The problem I chose, and why

**Problem:** Outbounding for . Specifically, turning a universe of 16,639 SEC-registered investment advisers into the 15–20 conversations per week that move the pipeline.

**Why this problem:**

- 's buyers don't respond to volume — the #1 sales barrier is compliance, not attention. One bad outbound burns a named account for 12 months.
- Clay and Apollo can hand any AE a list. The differentiator is **signal quality**, not contact volume — who's *actively* evaluating, not who *could* evaluate.
- Compounding: every approve/skip/flag decision is feedback. A system that learns what works for  specifically has a moat that a generic enrichment tool doesn't.

**Thesis:**
> Signal quality beats contact volume. The AE's scarce resource is attention, not leads.

---

## Slide 3 · System architecture

Seven-layer pipeline. Each layer is swappable; each has a narrow contract.

```
┌────────────────────────────────────────────────────────────┐
│  CONFIG  → YAML ICP rules + skill router (composable       │
│            Claude prompts)                                  │
├────────────────────────────────────────────────────────────┤
│  SIGNALS → Exa press, TwitterAPI.io, Apify LinkedIn,       │
│            SEC ADV filings, hiring                          │
├────────────────────────────────────────────────────────────┤
│  ENRICH  → Hunter → pattern → SEC Schedule A waterfall,    │
│            Exa team-page scrape for named contacts          │
├────────────────────────────────────────────────────────────┤
│  RESEARCH → NEW. Per-contact Exa: recent posts, speaking,  │
│             press. Haiku summarizes activity.               │
├────────────────────────────────────────────────────────────┤
│  ATTRIB  → NEW. Two-gate matcher attaches author signals   │
│            to named contacts (handle + last-name + ratio). │
├────────────────────────────────────────────────────────────┤
│  SCORING → Sonnet 4-dim score + Haiku MEDDIC classifier    │
│            (EB/CH/UC/UNKNOWN with confidence + reasoning). │
├────────────────────────────────────────────────────────────┤
│  BRIEF   → MEDDIC-framed account intelligence, now         │
│            referencing contact research by name + date.     │
├────────────────────────────────────────────────────────────┤
│  REVIEW  → Dashboard (approve/skip/flag) + multi-thread    │
│            firm coverage view (EB+CH ✓ / needs CH / etc).  │
└────────────────────────────────────────────────────────────┘
```

Callout: The RESEARCH, ATTRIB, and MEDDIC layers are new in this iteration — they close the loop between "we have a name" and "we have a *researched person with attributed signals*."

---

## Slide 4 · The ICP

**Who we target:** PE firms, investment banks, credit funds, hedge funds.

**Why these four:**
- Document-heavy workflows (diligence, IC memos, portfolio monitoring)
- Compliance-gated tech buying → 's data sovereignty story is the differentiator
- Sonja Renander / Oak Hill 6× ROI proof point lands hardest here

**The funnel, live from the pipeline:**

```
16,639  SEC-indexed investment advisers ($15.2T AUM)
 7,115  ICP-qualified (firm type + AUM tier + workflow fit)
   540  Actively monitored (40 tier-1 named · 500 tier-2 watchlist)
   569  Named contacts with titles
   497  With Hunter-verified emails (87%)
   463  With MEDDIC-framed account briefs
   269  In pending review queue
    18  Strong matches (score ≥ 75)
```

**The SEC ADV insight:** Investment advisers publicly disclose AUM, structure, ownership, and key personnel via Form ADV. Free, authoritative, machine-readable. No vendor lock-in, no rate limit, no decay. This is the cheapest firm universe anyone is going to build.

---

## Slide 5 · Signal intelligence

**Nine signal categories, each weighted by type and freshness:**

| Signal | Weight rule | Source |
|---|---|---|
| Named executive tweet on AI eval | **+40** | TwitterAPI.io |
| Hiring: Chief AI Officer / Head of AI | +35 | LinkedIn job posts |
| Named executive quote in WSJ/FT/BBG | +35 | Exa press |
| Deploying-stage announcement | +40 | Exa + press |
| Competitor frustration (AlphaSense etc) | +25 | Social + press |
| Compliance / data sovereignty post | +20 | Social + press |
| Firm AI hiring cluster | +15 | LinkedIn, aggregate |
| Junior analyst post (same topic) | +15 | Social |
| Press mention without quote | +10 | Exa |

**The attribution problem we solved:**

Before this iteration, every collector hardcoded `contact_id: None`. That silently killed the single most valuable scoring rule: "on-record executive quote outweighs all other signal types." Claude was scoring signals with `author_name="Sonja Renander"` but no way to match that to Oak Hill's contact record.

Now: a two-gate matcher (handle-exact OR last-name-exact + first-initial + similarity ≥ 0.80) attaches 28 signals to named contacts. The matcher is intentionally conservative — a 90% ratio on "David Chen" vs "David Chang" was the failure mode I refused to ship.

---

## Slide 6 · Scoring + MEDDIC

**Four-dimension composite:**

```
score = 0.30·ICP_Fit + 0.25·AI_Readiness + 0.25·Reachability + 0.20·Signal_Freshness
```

Each dimension scored 0–100 by Claude Sonnet given firm + contact + signals.

**Score bands → actions:**
- **75+** Strong Match → Approve
- **55–74** Good Match → Review
- **35–54** Moderate → Enrich
- **<35** Weak → Flag

**MEDDIC role, now Claude-assigned with confidence:**

The old system stamped every Managing Director as "Economic Buyer" via title regex. That was wrong: a Managing Director of Investor Relations is NOT an EB for an AI platform. A Co-CIO might not be either if their remit is investment, not technology.

New behavior (live from the pipeline, verification run):

| Contact | Title | Old regex | Claude | Confidence |
|---|---|---|---|---|
| Stuart Sim | Managing Director and Head of AI | EB | **CH** | 0.85 |
| John Stecher | Chief Technology Officer (Blackstone) | EB | EB | 0.85 |
| David Golob | Co-Chief Investment Officer (Francisco Partners) | EB | **UNKNOWN** | 0.35 |
| Brian Maury | Chief Technology Officer | EB | **CH** | 0.85 |

Every classification carries a one-sentence reasoning, persisted for audit. Confidence <0.5 gets demoted to UNKNOWN in the UI; 0.5–0.79 shows an asterisk ("inferred — verify before outreach").

**Account intelligence brief — Stuart Sim example:**

> **Identified Pain:** Silver Lake's new AI Infrastructure mandate (Stuart Sim, Nov 2025 hire) signals active technology evaluation — the firm is building a data science / AI org from zero, which means vendor selection is an open decision, not a defended incumbent.
>
> **Decision Criteria:** Speed to value, data sovereignty, ROI proof. Silver Lake is in early evaluation stage and the AI function was just stood up, so fit-to-workflow and governance posture will outrank raw capability.
>
> **Champion (Stuart Sim):** Joined Silver Lake as Head of AI in November 2025, leading a team of data scientists, engineers, and architects (LinkedIn announcement). He's actively forming vendor views right now — the highest-leverage window for a first touch.

Critical detail: "joined in November 2025, leading a team…" is sourced from Stuart's own LinkedIn announcement, not invented. The research enricher only persists facts that appear in the title or first 200 chars of Exa highlights.

---

## Slide 7 · Live demo

**Switch to the dashboard.** http://localhost:8765

Walkthrough order:
1. Landing page — 769 contacts in the queue, 269 pending review, latest signal timestamp.
2. Open Stuart Sim's side panel — show the new **Recent Activity** section (posts + press), then the account brief referencing his November 2025 hire.
3. Point at his MEDDIC pill — **CH** instead of EB. Hover tooltip shows "Champion · conf 0.85 — …reasoning…"
4. Click Silver Lake firm badge — shows **needs EB** (we have a Champion but no high-confidence Economic Buyer yet).
5. Switch to Analytics — the skill-router call distribution, the email-source waterfall, the signal timing breakdown.
6. Switch to Methodology page — shows the scoring formula, the MEDDIC mapping, the router architecture.
7. Switch to Pipeline Ops — live freshness window, collector health, cost ledger.

Return to slides.

---

## Slide 8 · Rollout plan

**Phase 1 · Weeks 1–2 · Pilot.**
2–3 AEs, top 20 tier-1 accounts, all review manual. Success metric: first-line approval rate. Kill any signal source under 40% approval by end of Week 2.

**Phase 2 · Month 1 · Expand watchlist.**
500 tier-2 firms flip to auto-promote on signal trigger. Slack push for deploying-stage firms. Start signal-to-meeting tracking.

**Phase 3 · Month 2+ · Full territory.**
AE territory mapping, per-rep voice profiles, sequence integration (Outreach/Apollo on approve), Pattern Analyst for scoring drift between runs.

**Signal-triggered promotion:** Tier-2 firms sit in firmographic-only monitoring (no Claude call, no cost). When any signal fires on a tier-2 firm, it promotes to tier-1 — contacts get enriched, contact research runs, MEDDIC classifies, the brief writes. Promotion is the expensive step; monitoring is ~free.

---

## Slide 9 · How I'd measure success

| Metric | Target | Current |
|---|---|---|
| Firms monitored | 500+ | **540** |
| Named contacts | 400+ | **569** |
| Verified emails | >80% | **87%** |
| Account briefs generated | >75% of scored | **81%** |
| Strong matches (≥75) | 15+ | **18** |
| Cost / scored + briefed contact | <$1 | **~$0.002** |
| First-line approval rate | >60% | Pilot required |
| Signal-to-meeting conversion | >5% | Pilot required |

The cost line is the one that changes the conversation. At ~$0.002 per contact, the constraint is never budget — it's AE attention. The whole system exists to make AE attention go further.

Every conversion metric above ("first-line approval rate", "signal-to-meeting") is downstream of one question: **is the queue good enough that AEs actually work it?** That's what the pilot tests.

---

## Slide 10 · Risks + what I'd build next

**Known risks:**

1. **Signal scraper fragility** — TwitterAPI.io and Apify change without notice. Mitigation: Exa as primary; socials are secondary.
2. **Email decay** — Hunter yield drops on senior titles over time. Mitigation: waterfall to pattern + SEC Schedule A; Apollo as documented step 3.
3. **Enterprise compliance** — 's own buyers will ask where *their* prospect data lives. Mitigation: SQLite local, CRM write only on explicit AE approve.
4. **Scoring drift** — Claude model versions shift subtly. Mitigation: skill files version-controlled; Pattern Analyst (planned) diffs score distributions between runs.

**What I'd build next, in order of ROI:**

1. **Reply feedback loop.** The single biggest quality lever. Scoring weights today are my guesses; after 50 sent emails with replies, they should move based on what converts — not on what I asserted. This is the compounding-value step.
2. **Slack push alerts.** Dashboards are pull; signals are push events. A CTO announcing an AI hire Tuesday afternoon should be a Slack ping, not a discovery on the next queue review. Every hour of latency is a conversion-point lost.
3. **CRM write-through.** Approvals flow to Salesforce/HubSpot with signal + brief + first line as an activity note. Also the foundation for the feedback loop — outcomes live in the CRM.

**Honest gap:** the research enricher has so far only run on 8 contacts in this verification pass. The architecture and the quality are proven; the scaled backfill (all 569 tier-1 contacts) is the next compute spend. Budget: ~$0.005 × 569 = **~$3 to populate every contact with research and a MEDDIC role.** This is the cheapest fix in the product.

---

## Appendix / backup slides

- **Architecture deep-dive:** `database.py` schema, skill router composition, collector contract.
- **Sample prompts:** scoring system prompt, MEDDIC system prompt, brief system prompt.
- **Cost model:** per-contact, per-week, per-100-accounts.
- **Comparison to Clay:** what Clay does well, where it ends, why it's a tool not a strategy.
