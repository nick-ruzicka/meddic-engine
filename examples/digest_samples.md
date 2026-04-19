# Digest Quality Bar — Example Outputs

These are hand-written examples of what the Monday digest should look like when the pipeline detects real signals. When tomorrow's real digest comes out, compare to these for format, tone, and specificity. If the real output doesn't match this quality bar, iterate before sending to Tom.

---

## Example Digest A — Harvey hiring signal

```
 COMPETITIVE SIGNALS — Week of April 14, 2026
3 leading indicators across 6 competitors

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HARVEY AI                                            [HIGH THREAT]

  [HIRING] Director of Product Marketing — Financial Services
           Lead time: 60-90 days
           → Harvey is hiring PMM for financial services specifically.
             This is not a legal-market hire — they're building a GTM
             team to sell into 's buyer. Expect a finance-vertical
             product launch or campaign in Q3 2026. Brief your AEs:
             Harvey is coming for finance, not just legal.
           Source: greenhouse.io/harvey (posted Apr 15)
           Confidence: 0.82

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

2 noise signals filtered (Harvey office manager posting, Keye
blog post about PE trends). Full log: /logs/signals_2026-04-14.json
```

**Why this is good:**
- Headline is the job title — specific, not "Harvey posted a job"
- Lead time is concrete: 60-90 days
- Takeaway tells Tom exactly what to DO: brief AEs, Harvey is expanding into finance
- Source is linked
- Noise is explicitly counted and explained, not silently dropped

---

## Example Digest B — AlphaSense sitemap signal

```
 COMPETITIVE SIGNALS — Week of April 14, 2026
4 leading indicators across 6 competitors

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ALPHASENSE                                           [HIGH THREAT]

  [LAUNCH] New URL: /enterprise/credit-analysts detected in sitemap
           Lead time: 2-4 weeks
           → AlphaSense added a page targeting credit analysts
             specifically — a segment where  has strong traction
             (Carlyle, BlackRock credit desks). This URL appeared in
             their sitemap before any blog post or announcement links
             to it. Likely a landing page for an upcoming product
             push or vertical campaign. Watch for the announcement.
           Source: sitemap diff (Apr 16)
           Confidence: 0.85

  [CONTENT] New blog post: /blog/alphasense-vs-point-solutions
            Lead time: immediate
            → AlphaSense published competitive positioning content
              framing themselves against "point solutions" — a term
              they've used before to describe  and Rogo. Brief
              your AEs on the rebuttal:  is not a point solution,
              it's the document intelligence layer for the full deal
              lifecycle.
            Source: sitemap diff (Apr 17)
            Confidence: 0.72

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ROGO                                                 [HIGH THREAT]

  [INFRASTRUCTURE] New subdomain: app-v2.rogo.ai detected
                   Lead time: 2-4 weeks
                   → Rogo stood up a new application subdomain,
                     likely a major product update or migration. When
                     a v2 subdomain appears, a launch is typically
                     2-4 weeks away. Monitor for the blog post
                     announcement.
                   Source: crt.sh certificate transparency (Apr 14)
                   Confidence: 0.88

  [HIRING] 4 engineering roles posted in one week
           Lead time: 60-90 days
           → Rogo posted 4 engineering roles (2 backend, 1 ML, 1
             platform) in a single week — an engineering surge signal.
             This level of hiring acceleration typically precedes a
             major product push by 2-3 months. Combined with the
             v2 subdomain above, Rogo appears to be in a build sprint.
           Source: ashby.com/rogo (Apr 13-17)
           Confidence: 0.90

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

3 noise signals filtered (F2 footer link change, Keye careers page
timestamp update, Blueflame blog about AI trends). Full log:
/logs/signals_2026-04-14.json
```

**Why this is good:**
- Multiple signals for the same competitor are grouped and tell a story (Rogo's v2 subdomain + hiring surge = they're building something big)
- Each takeaway is sales-angle: what does this mean for 's deals, not just "they did X"
- Confidence scores let Tom calibrate how much to trust each signal
- Noise count is transparent — Tom knows what was filtered and can check the full log
- Lead times help Tom prioritize: "immediate" means brief AEs now, "60-90 days" means track but don't panic

---

## What "bad" looks like (anti-patterns to avoid)

```
❌ BAD: "AlphaSense updated their website"
   → What page? What changed? So what?

❌ BAD: "New signal detected for Rogo (medium confidence)"
   → What signal? What should Tom do about it?

❌ BAD: "Harvey AI: 12 signals detected this week"
   → Which ones matter? Don't make Tom read 12 items.

❌ BAD: "F2.ai may be launching a product"
   → Based on what? When? What product? "May be" is useless.
```

Every signal in the digest must answer three questions:
1. **What happened?** (specific, with source)
2. **What does it mean for ?** (sales angle, not industry analysis)
3. **What should Tom do?** (brief AEs, watch for X, prepare rebuttal)
