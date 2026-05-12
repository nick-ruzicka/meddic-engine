# ICP Reference — Generic Template (human reference only)

> **Template notice.** This is the long-form research document for humans — it is **never injected
> into an API call** by the router (see `config/skills/ROUTER_SKILL.md`). Treat it as the source of
> truth that the section-tagged `config/skills/scoring/icp_scoring.md` and
> `config/skills/voice/outreach_voice.md` files are distilled from. Replace every `[PLACEHOLDER]`
> with your real ICP research before relying on it, and keep your customized copy — especially the
> verified proof points in section 7 — out of a public repository.

---

## 1. Product positioning

- **What the platform is** — `[PLACEHOLDER: one-paragraph plain-language description]`
- **Core differentiation** — `[PLACEHOLDER: the 2–3 things that are genuinely hard to copy]`
- **Where it loses** — `[PLACEHOLDER: honest list of weaknesses and non-fit situations]`

## 2. Industry context

- **Target vertical(s)** — `[PLACEHOLDER]`
- **Why now** — `[PLACEHOLDER: macro / regulatory / technology shifts driving adoption]`
- **Workflow map** — `[PLACEHOLDER: the specific workflows the product touches, by sub-segment]`

## 3. Buyer personas

For each persona: title patterns, what they own, what they care about, how they evaluate, and what
kills the deal with them. These feed the MEDDIC role classifier and the `reachability_rules` and
`angle_*` skill sections.

### Economic Buyer
- Titles — `[PLACEHOLDER: e.g. CIO, Managing Partner, COO]`
- Owns — `[PLACEHOLDER]`
- Cares about — `[PLACEHOLDER]`
- Evaluates on — `[PLACEHOLDER]`
- Deal-killer — `[PLACEHOLDER]`

### Champion
- Titles — `[PLACEHOLDER: e.g. Head of AI, Head of Data, Director of Research Operations]`
- Owns — `[PLACEHOLDER]`
- Cares about — `[PLACEHOLDER]`
- Evaluates on — `[PLACEHOLDER]`
- Deal-killer — `[PLACEHOLDER]`

### Influencer / User
- Titles — `[PLACEHOLDER]`
- Cares about — `[PLACEHOLDER]`

## 4. ICP segmentation

| Segment | Fit | Why | Workflow hook |
|---|---|---|---|
| `[PLACEHOLDER: e.g. tier-1 PE]` | High | `[PLACEHOLDER]` | `[PLACEHOLDER]` |
| `[PLACEHOLDER]` | Medium | `[PLACEHOLDER]` | `[PLACEHOLDER]` |
| `[PLACEHOLDER]` | Low / no-fit | `[PLACEHOLDER]` | — |

Mirror these into the `icp_*` sections of `icp_scoring.md`.

## 5. Competitive landscape

For each competitor: what they are, who they win with, their real strength, their real gap, and the
displacement-or-coexistence angle. One entry per `displacement_*` / `COMP_*` defined in
`config/skill_constants.py`.

- **`[PLACEHOLDER: competitor name — router tag displacement_*]`** — `[PLACEHOLDER: positioning |
  who they win with | real strength | real gap | angle]`
- `[PLACEHOLDER: repeat per competitor]`

## 6. Objection library

| Objection | Who raises it | Verified response | Source |
|---|---|---|---|
| `[PLACEHOLDER: e.g. data governance]` | `[PLACEHOLDER]` | `[PLACEHOLDER]` | `[PLACEHOLDER]` |
| `[PLACEHOLDER: "just an LLM wrapper"]` | `[PLACEHOLDER]` | `[PLACEHOLDER]` | `[PLACEHOLDER]` |
| `[PLACEHOLDER: procurement / budget]` | `[PLACEHOLDER]` | `[PLACEHOLDER]` | `[PLACEHOLDER]` |

Distill the verified responses into `objections_core` (scoring) and `angle_compliance_lead`
(voice).

## 7. Verified proof points

> **Do not commit this section's real contents to a public repo.** These are the only facts the
> scoring and voice skills are allowed to cite — every figure needs a source.

- `[PLACEHOLDER: customer logo + permission to name + source]`
- `[PLACEHOLDER: ROI figure + quote + source]`
- `[PLACEHOLDER: scale / benchmark metric + source]`
- `[PLACEHOLDER: certifications and data-handling posture + source]`
