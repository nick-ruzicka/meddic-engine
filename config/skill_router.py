# skill_router.py
# Assembles a minimal, targeted skill prompt for each Claude API call.
# Reads section-tagged markdown files and injects only relevant sections.
# Never injects the full reference file — that lives in config/skills/reference/.
#
# ARCHITECTURE NOTE:
# See config/skills/ROUTER_SKILL.md for full architecture documentation.
# See config/skill_constants.py for all section name constants.
# Do not hardcode section names here — always import from skill_constants.

import re
import logging
from pathlib import Path
from skill_constants import (
    SECTION_SCORING_CORE, SECTION_REACHABILITY_RULES,
    SECTION_ICP_PE, SECTION_ICP_HF, SECTION_ICP_IB, SECTION_ICP_CREDIT,
    SECTION_SIGNALS_HIRING, SECTION_SIGNALS_CONTENT, SECTION_SIGNALS_PRESS,
    SECTION_DISP_ALPHASENSE, SECTION_DISP_ROGO, SECTION_DISP_BLOOMBERG,
    SECTION_DISP_STACK_AI, SECTION_DISP_BUDGET,
    SECTION_STAGE_DEPLOYING, SECTION_STAGE_EVALUATING, SECTION_STAGE_EXPLORING,
    SECTION_OBJECTIONS_CORE,
    SECTION_VOICE_CORE, SECTION_ANGLE_PE_CREDIT, SECTION_ANGLE_HF,
    SECTION_ANGLE_IB, SECTION_ANGLE_TWITTER, SECTION_ANGLE_PRESS,
    SECTION_ANGLE_HIRING, SECTION_ANGLE_ALPHASENSE, SECTION_ANGLE_COMPLEMENTARY,
    SECTION_ANGLE_COMPLIANCE,
    SKILL_FILE_SCORING, SKILL_FILE_VOICE,
    FIRM_TYPE_PE, FIRM_TYPE_HF, FIRM_TYPE_IB, FIRM_TYPE_CREDIT,
    STAGE_DEPLOYING, STAGE_EVALUATING, STAGE_EXPLORING,
    COMP_ALPHASENSE, COMP_ROGO, COMP_BLOOMBERG, COMP_STACK_AI,
    COMP_BUDGET, COMP_HARVEY, COMP_NONE
)

logger = logging.getLogger(__name__)

# ── Section parser ────────────────────────────────────────────────────────────

def load_sections(skill_file_path: str) -> dict[str, str]:
    """
    Parse a section-tagged markdown file into a dict of {section_name: content}.
    
    Tag format in markdown files:
        <!-- section: section_name -->
        ... content ...
        <!-- /section -->
    
    Raises ValueError if any section tag is malformed.
    Raises FileNotFoundError if the skill file doesn't exist.
    """
    path = Path(skill_file_path)
    if not path.exists():
        raise FileNotFoundError(f"Skill file not found: {skill_file_path}")

    content = path.read_text(encoding="utf-8")
    pattern = r'<!--\s*section:\s*(\S+)\s*-->(.*?)<!--\s*/section\s*-->'
    matches = re.findall(pattern, content, re.DOTALL)

    if not matches:
        raise ValueError(f"No valid section tags found in {skill_file_path}")

    sections = {}
    for name, body in matches:
        name = name.strip()
        body = body.strip()
        if not body:
            raise ValueError(f"Empty section '{name}' in {skill_file_path}. "
                             f"Check section tags for typos or missing content.")
        sections[name] = body

    return sections


def get_section(sections: dict, section_name: str, skill_file: str) -> str:
    """
    Retrieve a section by name. Raises KeyError with a clear message if missing.
    """
    if section_name not in sections:
        raise KeyError(
            f"Section '{section_name}' not found in {skill_file}. "
            f"Available sections: {list(sections.keys())}. "
            f"Check skill_constants.py and the skill file are in sync."
        )
    return sections[section_name]


# ── Routing logic ─────────────────────────────────────────────────────────────

def build_scoring_prompt(account: dict) -> tuple[str, list[str]]:
    """
    Assemble a targeted scoring prompt for a single account.
    
    Returns:
        (prompt_text, section_list) — section_list is logged with every
        scoring decision for debugging and audit purposes.
    
    Account dict expected fields:
        firm_type       : str  (use FIRM_TYPE_* constants)
        competitor       : str  (use COMP_* constants, or COMP_NONE)
        buying_stage    : str  (use STAGE_* constants)
        signal_types    : list[str]  e.g. ["hiring", "content_intent", "press"]
        has_objections  : bool  (True if AI wrapper or compliance objection signals present)
    """
    sections_raw = load_sections(SKILL_FILE_SCORING)
    selected = []

    # ── Always inject ─────────────────────────────────────────────────────────
    selected.append(SECTION_SCORING_CORE)
    selected.append(SECTION_REACHABILITY_RULES)

    # ── Firm type ─────────────────────────────────────────────────────────────
    firm_type = account.get("firm_type", "")
    firm_type_map = {
        FIRM_TYPE_PE:     SECTION_ICP_PE,
        FIRM_TYPE_HF:     SECTION_ICP_HF,
        FIRM_TYPE_IB:     SECTION_ICP_IB,
        FIRM_TYPE_CREDIT: SECTION_ICP_CREDIT,
    }
    if firm_type in firm_type_map:
        selected.append(firm_type_map[firm_type])
    else:
        # Law firms and other types use IB rules as closest proxy
        selected.append(SECTION_ICP_IB)
        logger.warning(f"Unknown firm type '{firm_type}' — defaulting to IB scoring rules")

    # ── Signal types ──────────────────────────────────────────────────────────
    signal_types = account.get("signal_types", [])
    signal_map = {
        "hiring":         SECTION_SIGNALS_HIRING,
        "content_intent": SECTION_SIGNALS_CONTENT,
        "press":          SECTION_SIGNALS_PRESS,
    }
    for sig in signal_types:
        if sig in signal_map:
            selected.append(signal_map[sig])

    # ── Competitor displacement ───────────────────────────────────────────────
    competitor = account.get("competitor", COMP_NONE)
    competitor_map = {
        COMP_ALPHASENSE: SECTION_DISP_ALPHASENSE,
        COMP_ROGO:       SECTION_DISP_ROGO,
        COMP_BLOOMBERG:  SECTION_DISP_BLOOMBERG,
        COMP_STACK_AI:   SECTION_DISP_STACK_AI,
        COMP_BUDGET:     SECTION_DISP_BUDGET,
        COMP_HARVEY:     SECTION_DISP_ALPHASENSE,  # Harvey → use AlphaSense displacement rules
    }
    if competitor in competitor_map:
        selected.append(competitor_map[competitor])

    # ── Buying stage ──────────────────────────────────────────────────────────
    buying_stage = account.get("buying_stage", "")
    stage_map = {
        STAGE_DEPLOYING:  SECTION_STAGE_DEPLOYING,
        STAGE_EVALUATING: SECTION_STAGE_EVALUATING,
        STAGE_EXPLORING:  SECTION_STAGE_EXPLORING,
    }
    if buying_stage in stage_map:
        selected.append(stage_map[buying_stage])

    # ── Objection handling ────────────────────────────────────────────────────
    if account.get("has_objections", False):
        selected.append(SECTION_OBJECTIONS_CORE)

    # ── Deduplicate while preserving order ───────────────────────────────────
    seen = set()
    selected_deduped = []
    for s in selected:
        if s not in seen:
            seen.add(s)
            selected_deduped.append(s)

    # ── Assemble prompt ───────────────────────────────────────────────────────
    assembled_sections = []
    for section_name in selected_deduped:
        content = get_section(sections_raw, section_name, SKILL_FILE_SCORING)
        assembled_sections.append(content)

    prompt = "\n\n---\n\n".join(assembled_sections)

    logger.info(f"Scoring prompt assembled | sections={selected_deduped} | "
                f"firm_type={firm_type} | competitor={competitor} | "
                f"buying_stage={buying_stage}")

    return prompt, selected_deduped


def build_personalization_prompt(account: dict) -> tuple[str, list[str]]:
    """
    Assemble a targeted personalization prompt for first-line generation.
    Uses the voice skill file rather than the scoring skill file.
    Same routing logic — injects only relevant sections.
    """
    sections_raw = load_sections(SKILL_FILE_VOICE)
    selected = []

    # Voice file always starts with tone and avoid rules
    selected.append(SECTION_VOICE_CORE)

    firm_type = account.get("firm_type", "")
    if firm_type in (FIRM_TYPE_PE, FIRM_TYPE_CREDIT):
        selected.append(SECTION_ANGLE_PE_CREDIT)
    elif firm_type == FIRM_TYPE_HF:
        selected.append(SECTION_ANGLE_HF)
    elif firm_type == FIRM_TYPE_IB:
        selected.append(SECTION_ANGLE_IB)

    # Signal-specific angle
    signal_types = account.get("signal_types", [])
    if "content_intent" in signal_types:
        selected.append(SECTION_ANGLE_TWITTER)
    if "press" in signal_types:
        selected.append(SECTION_ANGLE_PRESS)
    if "hiring" in signal_types:
        selected.append(SECTION_ANGLE_HIRING)

    # Competitor angle
    competitor = account.get("competitor", COMP_NONE)
    if competitor == COMP_ALPHASENSE:
        selected.append(SECTION_ANGLE_ALPHASENSE)
    elif competitor in (COMP_ROGO, COMP_STACK_AI):
        selected.append(SECTION_ANGLE_COMPLEMENTARY)

    # Objection pre-emption
    if account.get("has_objections", False):
        selected.append(SECTION_ANGLE_COMPLIANCE)

    seen = set()
    selected_deduped = [s for s in selected if not (s in seen or seen.add(s))]

    assembled = []
    for section_name in selected_deduped:
        content = get_section(sections_raw, section_name, SKILL_FILE_VOICE)
        assembled.append(content)

    prompt = "\n\n---\n\n".join(assembled)

    logger.info(f"Personalization prompt assembled | sections={selected_deduped}")

    return prompt, selected_deduped


# ── Test harness ──────────────────────────────────────────────────────────────

def test_routing():
    """
    Smoke test — run with: python skill_router.py
    Validates routing logic for known account types without hitting the API.
    """
    test_accounts = [
        {
            "name": "Blackstone (PE, no competitor)",
            "firm_type": FIRM_TYPE_PE,
            "competitor": COMP_NONE,
            "buying_stage": STAGE_EVALUATING,
            "signal_types": ["hiring", "press"],
            "has_objections": False,
            "expected_sections": [
                SECTION_SCORING_CORE, SECTION_REACHABILITY_RULES,
                SECTION_ICP_PE, SECTION_SIGNALS_HIRING,
                SECTION_SIGNALS_PRESS, SECTION_STAGE_EVALUATING
            ]
        },
        {
            "name": "Moelis (IB, Rogo customer)",
            "firm_type": FIRM_TYPE_IB,
            "competitor": COMP_ROGO,
            "buying_stage": STAGE_DEPLOYING,
            "signal_types": ["hiring"],
            "has_objections": False,
            "expected_sections": [
                SECTION_SCORING_CORE, SECTION_REACHABILITY_RULES,
                SECTION_ICP_IB, SECTION_SIGNALS_HIRING,
                SECTION_DISP_ROGO, SECTION_STAGE_DEPLOYING
            ]
        },
        {
            "name": "William Blair (IB, AlphaSense + objections)",
            "firm_type": FIRM_TYPE_IB,
            "competitor": COMP_ALPHASENSE,
            "buying_stage": STAGE_EVALUATING,
            "signal_types": ["content_intent"],
            "has_objections": True,
            "expected_sections": [
                SECTION_SCORING_CORE, SECTION_REACHABILITY_RULES,
                SECTION_ICP_IB, SECTION_SIGNALS_CONTENT,
                SECTION_DISP_ALPHASENSE, SECTION_STAGE_EVALUATING,
                SECTION_OBJECTIONS_CORE
            ]
        },
    ]

    print("Running skill router tests...\n")
    all_passed = True

    for account in test_accounts:
        try:
            _, section_list = build_scoring_prompt(account)
            expected = account["expected_sections"]
            passed = set(section_list) == set(expected)
            status = "✓ PASS" if passed else "✗ FAIL"
            print(f"{status} | {account['name']}")
            if not passed:
                print(f"  Expected: {sorted(expected)}")
                print(f"  Got:      {sorted(section_list)}")
                all_passed = False
        except Exception as e:
            print(f"✗ ERROR | {account['name']} | {e}")
            all_passed = False

    print(f"\n{'All tests passed.' if all_passed else 'Some tests failed — check routing logic.'}")


if __name__ == "__main__":
    test_routing()
