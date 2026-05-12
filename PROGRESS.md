# Build Progress — Competitive Signal Engine v2

## Terminal 1 — Sitemap
- [ ] Baseline capture for all 6 competitors
- [ ] Diff logic (new URL, removed URL, content change)
- [ ] Content hash filter (ignore timestamps, tracking params)
- [ ] Tests
- [ ] Integration: emits RawSignal conforming to collectors/base.py

## Terminal 2 — Jobs
- [ ] Adapter per job source: greenhouse, ashby, lever, workday
- [ ] Baseline: current open roles
- [ ] Diff: new postings since last run
- [ ] Keyword classification (vertical expansion, launch-related, generic)
- [ ] Tests
- [ ] Integration

## Terminal 3 — DNS
- [ ] Baseline: current subdomains via crt.sh
- [ ] Daily diff: new subdomains
- [ ] Tests
- [ ] Integration

## Terminal 4 — GitHub
- [ ] Per-org: commit count, new repo detection, star tracking
- [ ] Bot filter (dependabot, renovate, github-actions)
- [ ] Commit burst detection (5+ commits/day threshold)
- [ ] Tests
- [ ] Integration

## Terminal 5 — Exa + Classifier + Digest
- [ ] Exa trending diff layer (new mentions vs baseline)
- [ ] Signal classifier (rule-based first pass, Claude second pass for ambiguous)
- [ ] Predictive scoring
- [ ] Monday digest: email format
- [ ] Tests
- [ ] Integration

## Blockers / Cross-cutting
*(post here when you need something from another terminal)*

## Integration Milestones
- [ ] All collectors produce valid RawSignals (coordinator verifies)
- [ ] Classifier produces valid ClassifiedSignals for synthetic input
- [ ] Dry-run digest renders correctly
- [ ] Live run against real competitor data
- [ ] First Monday digest reviewed by the user before it goes out
