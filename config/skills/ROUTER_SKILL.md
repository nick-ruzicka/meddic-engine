# ROUTER_SKILL.md
# Architecture documentation for the MEDDIC Engine skill routing system.
# Read this before touching skill_router.py, skill_constants.py, or any skill file.
# This is the source of truth for how skills are structured and injected.

---

## What This System Does

Instead of injecting a full skill file into every Claude API call, the router
assembles a minimal, targeted prompt from only the sections relevant to the
specific account being scored or personalized.

This solves three problems:
1. **Context bloat** — long prompts cause models to weight early sections more
   heavily than late ones. Targeted injection means every section gets full attention.
2. **Token cost** — at scale, injecting 150 lines instead of 600 per call compounds.
3. **Debuggability** — every call logs which sections were injected, so wrong
   scoring decisions trace back to a specific section, not a black box.

---

## File Map

```
config/
├── skill_constants.py              ← Single source of truth for all section names
├── skill_router.py                 ← Assembles prompts from section-tagged files
└── skills/
    ├── scoring/
    │   └── icp_scoring.md     ← Section-tagged scoring rules (injected by router)
    ├── voice/
    │   └── outreach_voice.md  ← Section-tagged voice rules (injected by router)
    └── reference/
        └── icp_reference.md   ← Full research doc (NEVER injected — human use only)
```

---

## Section Tag Format

Every skill file uses this exact tag format:

```markdown
<!-- section: section_name -->
## Section Title

Content here...

<!-- /section -->
```

**Rules:**
- `section_name` must exactly match a constant in `skill_constants.py`
- No spaces in section names — use underscores
- Content must be non-empty — the parser raises an error on empty sections
- Tags must be on their own lines

---

## How To Add a New Section

1. Add the constant to `skill_constants.py`:
   ```python
   SECTION_NEW_THING = "new_thing"
   ```

2. Add the tagged section to the appropriate skill file:
   ```markdown
   <!-- section: new_thing -->
   ## New Thing Rules
   ...
   <!-- /section -->
   ```

3. Add routing logic to `skill_router.py` in the appropriate block:
   ```python
   if account.get("some_condition"):
       selected.append(SECTION_NEW_THING)
   ```

4. Add a test case to `test_routing()` in `skill_router.py`.

5. Run `python skill_router.py` — all tests must pass before deploying.

**Never hardcode section name strings in skill_router.py. Always import from skill_constants.py.**

---

## How To Update Existing Sections

Edit the content inside the section tags in the skill file.
Do NOT rename sections without updating skill_constants.py and all routing logic.

If you must rename a section:
1. Update the constant value in `skill_constants.py`
2. Update the tag in the skill file
3. Routing logic uses the constant — no other changes needed
4. Run tests

---

## Routing Decision Logic

`build_scoring_prompt(account)` assembles sections in this order:

| Priority | Sections | Trigger |
|----------|---------|---------|
| 1 | `scoring_core` + `reachability_rules` | Always |
| 2 | `icp_[firm_type]` | firm_type field on account |
| 3 | `signals_[type]` | signal_types list on account |
| 4 | `displacement_[competitor]` | competitor field on account |
| 5 | `language_[buying_stage]` | buying_stage field on account |
| 6 | `objections_core` | has_objections flag on account |

`build_personalization_prompt(account)` follows the same logic
but reads from `outreach_voice.md` instead.

---

## Account Dict Schema

Both routing functions expect this structure:

```python
account = {
    "firm_type":      str,   # FIRM_TYPE_* constant
    "competitor":     str,   # COMP_* constant, or COMP_NONE
    "buying_stage":   str,   # STAGE_* constant
    "signal_types":   list,  # ["hiring", "content_intent", "press"]
    "has_objections": bool,  # True if AI wrapper or compliance objection detected
}
```

---

## Logging

Every routing call logs at INFO level:

```
Scoring prompt assembled | sections=['scoring_core', 'reachability_rules', 'icp_pe', ...] 
| firm_type=pe | competitor=alphasense | buying_stage=evaluating
```

This section list is also stored in `scoring_decisions.json` alongside the score
and reasoning for every account. If scoring looks wrong, check the section list first.

---

## Testing

Run tests with:
```bash
python skill_router.py
```

Tests validate routing logic against known account types without hitting the API.
All tests must pass before any deploy. Add a test case for every new routing rule.

---

## What NOT To Do

- **Never inject `icp_reference.md` into an API call.** It's a human
  reference document, not a scoring prompt. It's long-form research and would
  overwhelm the context window.

- **Never hardcode section names as strings.** Always import from `skill_constants.py`.
  Hardcoded strings break silently when sections are renamed.

- **Never skip the empty-section validation.** A missing section injects nothing
  and causes silent scoring failures. The parser raises on empty sections
  precisely to prevent this.

- **Never add narrative context or research sourcing to scoring skill files.**
  Scoring files are rules only. Research context lives in the reference file.

---

## Current Skill Files

| File | Sections | Purpose |
|------|---------|---------|
| `icp_scoring.md` | 18 sections | Scoring rules for all firm types and signal combinations |
| `outreach_voice.md` | 10 sections | Voice rules and personalization angles |
| `icp_reference.md` | N/A (no tags) | Full research doc — human use only |

---

## When To Update Skill Files

- After each campaign: update scoring weights based on response rate data
- When a new competitor is confirmed: add displacement section + constant
- When a new firm type is targeted: add ICP section + constant + routing rule
- When buyer language patterns change: update relevant signal sections
- Never update based on theory — only update when real data supports the change
