# ICP Scoring Skill — Generic Template

> **Template notice.** This is a generic template shipped with the repo so the skill router runs
> end-to-end out of the box. Customize it for your deployment by replacing every `[PLACEHOLDER]`
> marker with your verified ICP definitions, scoring rules, and proof points (the long-form
> research it is distilled from lives in `config/skills/reference/icp_reference.md`). **Never
> commit production proof points, customer logos, or internal pitch language to a public
> repository** — keep your customized copy out of version control (it is gitignored if you follow
> the README) or store it in a private config repo.
>
> The router (`config/skill_router.py`) reads the HTML-comment-delimited blocks below and injects
> only the sections relevant to each account. Section names must match the `SECTION_*` constants
> in `config/skill_constants.py`, and every section must be non-empty. See
> `config/skills/ROUTER_SKILL.md` for the exact tag format and routing rules. Displacement
> sections are named after the competitors enumerated as `COMP_*` in `skill_constants.py` — rename
> both together if your competitive set differs.

---

<!-- section: scoring_core -->
## Scoring core (always injected)

You score a (firm, contact, signals) triple on a 0–100 composite across four dimensions:

- **ICP Fit (30%)** — does the firm match the ideal-customer profile? Firm type, size / AUM tier,
  workflow fit. Use the `icp_*` section injected for this firm type.
- **AI Readiness (25%)** — buying-stage evidence: published AI strategy, AI hiring, named AI
  leadership, firm-wide deployment signals.
- **Reachability (25%)** — see `reachability_rules`.
- **Signal Freshness (20%)** — recency of the most recent qualifying signal. Last 30 days scores
  strongly; older signals decay.

Return a JSON object: `score` (int 0–100), `label` (one of: Strong Match / Good Match / Moderate /
Weak), the four dimension sub-scores, and `reasoning` (2–4 sentences citing the specific signals
and firm facts that drove the score). Never invent facts. If a dimension lacks evidence, score it
conservatively and say so in the reasoning. Thresholds: `[PLACEHOLDER: e.g. 75+ Strong, 55–74
Good, 35–54 Moderate, <35 Weak]`.
<!-- /section -->

<!-- section: reachability_rules -->
## Reachability rules (always injected)

Score Reachability higher when:

- The contact has a verified work email (not a guessed pattern).
- The contact's title clearly matches a buyer or champion persona for this product
  (`[PLACEHOLDER: e.g. CTO, Head of AI, Head of Data, Managing Partner]`).
- A public profile (LinkedIn / firm bio) confirms the role.

Score lower when the email is unverified, the contact is a placeholder ("Head of Technology
(TBD)"), or the only contact is a generic inbox. Do not penalize a strong firm for a weak contact
— score it and flag "needs better contact" in the reasoning.
<!-- /section -->

<!-- section: icp_pe -->
## ICP — Private equity firms

Strong ICP Fit signals: `[PLACEHOLDER: target AUM tiers, deal volume, in-house deal teams,
existing data / AI function]`. Highest-fit sub-segment: `[PLACEHOLDER: e.g. tier-1 megafunds doing
buy-side diligence on large data rooms]`. Lower-fit: `[PLACEHOLDER: e.g. sub-scale funds with no
technical staff]`. Workflow hook: `[PLACEHOLDER: e.g. due-diligence document review, IC memo prep,
portfolio monitoring]`.
<!-- /section -->

<!-- section: icp_hf -->
## ICP — Hedge funds

Strong ICP Fit signals: `[PLACEHOLDER: AUM tier, research-team size, qualitative vs. quantitative
strategy mix]`. Workflow hook: `[PLACEHOLDER: e.g. analyst productivity on qualitative research,
earnings / transcript synthesis, expert-network note triage]`.
<!-- /section -->

<!-- section: icp_ib -->
## ICP — Investment banks / advisory

Strong ICP Fit signals: `[PLACEHOLDER: deal flow, coverage-team size, existing workflow tooling]`.
Workflow hook: `[PLACEHOLDER: e.g. pitch / CIM preparation, comps, deal-velocity document
synthesis with compliance-reviewable output]`. Unmapped firm types (law firms, "other") fall back
to these rules.
<!-- /section -->

<!-- section: icp_credit -->
## ICP — Private credit funds

Strong ICP Fit signals: `[PLACEHOLDER: AUM tier, direct-lending vs. tradeable credit, underwriting
team size]`. Workflow hook: `[PLACEHOLDER: e.g. credit-agreement review, covenant extraction,
underwriting memo prep]`.
<!-- /section -->

<!-- section: signals_hiring -->
## Signal type — hiring

A relevant job posting is an AI-Readiness signal. Weight higher when the role is senior and owns
the relevant workflow (`[PLACEHOLDER: e.g. "Head of AI", "Director of AI Strategy", "ML Engineer —
Investments"]`); lower for generic IT roles. A hiring signal also implies a buying stage — see the
`language_*` section.
<!-- /section -->

<!-- section: signals_content_intent -->
## Signal type — content / intent

The firm (or a named exec) publicly discussing AI adoption — posts, panels, comparison content,
"evaluating X" language — is a buying-intent signal. Higher weight when a named decision-maker is
the author and the content is recent. Quote the specific phrase in the reasoning; do not
paraphrase intent that isn't there.
<!-- /section -->

<!-- section: signals_press -->
## Signal type — press

Press coverage (the firm's AI initiative, a relevant deal, a vendor case study mentioning them, a
conference appearance) is a moderate AI-Readiness signal. Verify the firm is the subject, not just
mentioned in passing. Recency-weight it.
<!-- /section -->

<!-- section: displacement_alphasense -->
## Displacement — incumbent: market-intelligence search platform (router tag `displacement_alphasense`)

The prospect already uses an incumbent in this category — do NOT score them out, score the
displacement angle. Likely gap to surface: `[PLACEHOLDER: e.g. reasoning over private / uploaded
documents vs. public-content search]`. Decision criterion to introduce: `[PLACEHOLDER]`. Avoid:
`[PLACEHOLDER: claims you cannot substantiate against this incumbent]`.
<!-- /section -->

<!-- section: displacement_rogo -->
## Displacement — incumbent: finance-workflow AI assistant (router tag `displacement_rogo`)

Position as `[PLACEHOLDER: complementary vs. replacement]`. Likely gap: `[PLACEHOLDER]`. Decision
criterion to introduce: `[PLACEHOLDER]`. Avoid: `[PLACEHOLDER]`.
<!-- /section -->

<!-- section: displacement_bloomberg -->
## Displacement — incumbent: legacy data terminal (router tag `displacement_bloomberg`)

The angle is usually `[PLACEHOLDER: workflow automation the terminal doesn't do — not data-feed
replacement]`. Likely gap: `[PLACEHOLDER]`. Avoid: `[PLACEHOLDER]`.
<!-- /section -->

<!-- section: displacement_stack_ai -->
## Displacement — incumbent: generic LLM-wrapper / workflow builder (router tag `displacement_stack_ai`)

Surface `[PLACEHOLDER: domain depth, accuracy on their document types, maintenance burden of a
DIY build]`. Likely gap: `[PLACEHOLDER]`. Avoid: `[PLACEHOLDER]`.
<!-- /section -->

<!-- section: displacement_budget -->
## Displacement — budget / point-tool incumbents (router tag `displacement_budget`)

The prospect stitches together cheap point tools (`[PLACEHOLDER: list the categories]`). Angle:
`[PLACEHOLDER: consolidation, depth, enterprise security]`. Likely gap: `[PLACEHOLDER]`. Don't
disparage a tool that solves a real narrow need — position on scope and rigor instead.
<!-- /section -->

<!-- section: language_deploying -->
## Buying stage — deploying

Signals indicate the firm is actively rolling out AI. Timing favors near-term engagement; note the
deployment evidence in the reasoning. Recommended posture: `[PLACEHOLDER: e.g. land-and-expand —
reference the specific workflow they're deploying into]`.
<!-- /section -->

<!-- section: language_evaluating -->
## Buying stage — evaluating

Signals indicate active evaluation (RFPs, "comparing vendors", pilots). Posture: build the
champion relationship, supply decision criteria, pre-empt the known objection. `[PLACEHOLDER]`.
<!-- /section -->

<!-- section: language_exploring -->
## Buying stage — exploring

Signals indicate early exploration (thought-leadership posts, conference attendance, a first AI
hire). Posture: nurture, don't hard-sell. `[PLACEHOLDER]`.
<!-- /section -->

<!-- section: objections_core -->
## Objection handling (injected when objection signals present)

Common objections in regulated / enterprise sales and the verified responses you may use:

- **Data governance / security** — respond only with `[PLACEHOLDER: your documented
  certifications and data-handling posture, e.g. SOC 2, data-retention policy]`. Do not assert
  deployment-model details (SaaS / VPC / on-prem) unless documented; otherwise say "confirm with
  the platform team".
- **"It's just an LLM wrapper"** — respond with `[PLACEHOLDER: your domain-specific accuracy
  evidence and architecture differentiation]`.
- **Procurement / budget** — respond with `[PLACEHOLDER: ROI proof points, pilot pricing]`.

Never invent a certification, customer, or metric to answer an objection. If you have no sourced
response, say the rep should follow up rather than bluff.
<!-- /section -->
