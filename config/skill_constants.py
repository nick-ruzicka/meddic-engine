# skill_constants.py
# Single source of truth for all skill section names.
# skill_router.py references these constants. The section parser lives inside skill_router.py.
# If you rename a section, rename it here — nowhere else.

# ── Always-injected sections ──────────────────────────────────────────────────
SECTION_SCORING_CORE        = "scoring_core"
SECTION_REACHABILITY_RULES  = "reachability_rules"

# ── ICP sections — injected by firm type ─────────────────────────────────────
SECTION_ICP_PE              = "icp_pe"
SECTION_ICP_HF              = "icp_hf"
SECTION_ICP_IB              = "icp_ib"
SECTION_ICP_CREDIT          = "icp_credit"

# ── Signal sections — injected when signal type detected ─────────────────────
SECTION_SIGNALS_HIRING      = "signals_hiring"
SECTION_SIGNALS_CONTENT     = "signals_content_intent"
SECTION_SIGNALS_PRESS       = "signals_press"

# ── Displacement sections — injected when competitor signal detected ──────────
SECTION_DISP_ALPHASENSE     = "displacement_alphasense"
SECTION_DISP_ROGO           = "displacement_rogo"
SECTION_DISP_BLOOMBERG      = "displacement_bloomberg"
SECTION_DISP_STACK_AI       = "displacement_stack_ai"
SECTION_DISP_BUDGET         = "displacement_budget"   # Hudson Labs, Quartr, Portrait

# ── Buying stage sections — injected by job posting classification ────────────
SECTION_STAGE_DEPLOYING     = "language_deploying"
SECTION_STAGE_EVALUATING    = "language_evaluating"
SECTION_STAGE_EXPLORING     = "language_exploring"

# ── Objection handling — injected when relevant signals present ───────────────
SECTION_OBJECTIONS_CORE     = "objections_core"       # AI wrapper + compliance

# ── Voice / personalization sections — injected by build_personalization_prompt ──
SECTION_VOICE_CORE              = "voice_core"
SECTION_ANGLE_PE_CREDIT         = "angle_pe_credit"
SECTION_ANGLE_HF                = "angle_hf"
SECTION_ANGLE_IB                = "angle_ib"
SECTION_ANGLE_TWITTER           = "angle_twitter_signal"
SECTION_ANGLE_PRESS             = "angle_press_quote"
SECTION_ANGLE_HIRING            = "angle_hiring_signal"
SECTION_ANGLE_ALPHASENSE        = "angle_alphasense_displacement"
SECTION_ANGLE_COMPLEMENTARY     = "angle_complementary_tool"
SECTION_ANGLE_COMPLIANCE        = "angle_compliance_lead"
SKILL_FILE_SCORING  = "config/skills/scoring/icp_scoring.md"
SKILL_FILE_VOICE    = "config/skills/voice/outreach_voice.md"
SKILL_FILE_REF      = "config/skills/reference/icp_reference.md"  # never injected

# ── Firm type values — used in account records and routing logic ───────────────
FIRM_TYPE_PE        = "pe"
FIRM_TYPE_HF        = "hedge_fund"
FIRM_TYPE_IB        = "investment_bank"
FIRM_TYPE_CREDIT    = "credit"
FIRM_TYPE_LAW       = "law_firm"
FIRM_TYPE_OTHER     = "other"

# ── Buying stage values — output of job posting classifier ───────────────────
STAGE_DEPLOYING     = "deploying"
STAGE_EVALUATING    = "evaluating"
STAGE_EXPLORING     = "exploring"
STAGE_UNKNOWN       = "unknown"

# ── Competitor values — used in displacement routing ─────────────────────────
COMP_ALPHASENSE     = "alphasense"
COMP_ROGO           = "rogo"
COMP_BLOOMBERG      = "bloomberg"
COMP_STACK_AI       = "stack_ai"
COMP_BUDGET         = "budget"      # Hudson Labs, Quartr, Portrait
COMP_HARVEY         = "harvey"      # routes to icp_ib + objections_core
COMP_NONE           = "none"
