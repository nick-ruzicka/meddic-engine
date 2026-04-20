"""
data/seed/seed_accounts.py
Seeds the database with 20 real financial institutions, named contacts,
and realistic signals derived from our ICP research.

Run: python data/seed/seed_accounts.py
Safe to run multiple times — checks for existing records before inserting.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from database import get_db, init_db
from utils.helpers import now_iso

FIRMS = [
    # ── Tier 1 PE — Active Prospects ──────────────────────────────────────────
    {
        "name": "Blackstone",
        "domain": "blackstone.com",
        "firm_type": "pe",
        "tier": 1,
        "aum_range": "$1T+",
        "geography": "US",
        "_status": "prospect",
        "competitor": "none",
        "buying_stage": "evaluating",
        "has_objections": 0,
        "notes": "Head of Private Credit Tech at PMF 2025. John Stecher (CTO) at Reuters Momentum Finance. John Fitzpatrick (Sr MD CTO) in Canoe AI case study.",
    },
    {
        "name": "Apollo Global Management",
        "domain": "apolloglobal.com",
        "firm_type": "pe",
        "tier": 1,
        "aum_range": "$500B+",
        "geography": "US",
        "_status": "prospect",
        "competitor": "none",
        "buying_stage": "deploying",
        "has_objections": 0,
        "notes": "Katia Walsh (AI Lead) authored Winning With Generative AI 2025. AI Lead at PMF conference. Deploying AI for M&A due diligence.",
    },
    {
        "name": "Ares Management",
        "domain": "aresmgmt.com",
        "firm_type": "credit",
        "tier": 1,
        "aum_range": "$400B+",
        "geography": "US",
        "_status": "prospect",
        "competitor": "none",
        "buying_stage": "evaluating",
        "has_objections": 0,
        "notes": "Nicolai Wadstrom Co-Head AI Innovation Group. Constellation AI Forum Sept 2025 — actively in build vs partner vs buy debate.",
    },
    {
        "name": "Silver Lake",
        "domain": "silverlake.com",
        "firm_type": "pe",
        "tier": 1,
        "aum_range": "$100B+",
        "geography": "US",
        "_status": "prospect",
        "competitor": "none",
        "buying_stage": "deploying",
        "has_objections": 0,
        "notes": "Stuart Sim hired as MD Head of AI 2025. Previously hired Apoorv Saxena ex-Google/JPM Global Head of AI.",
    },
    {
        "name": "Vista Equity Partners",
        "domain": "vistaequitypartners.com",
        "firm_type": "pe",
        "tier": 1,
        "aum_range": "$100B+",
        "geography": "US",
        "_status": "prospect",
        "competitor": "none",
        "buying_stage": "deploying",
        "has_objections": 0,
        "notes": "Agentic AI Factory model via Gainsight. Former AI leads Leland Lockhart and Dr. Ben Herndon.",
    },
    # ── Tier 1 IB — Active Prospects ──────────────────────────────────────────
    {
        "name": "Evercore",
        "domain": "evercore.com",
        "firm_type": "investment_bank",
        "tier": 1,
        "aum_range": "N/A",
        "geography": "US",
        "_status": "prospect",
        "competitor": "none",
        "buying_stage": "evaluating",
        "has_objections": 0,
        "notes": "VP Stefano Combi at AlphaSummit: AI buyer lists look better than analysts. Marc Harris hosted Digital Finance Summit. Selective AI pilots in progress.",
    },
    {
        "name": "PJT Partners",
        "domain": "pjtpartners.com",
        "firm_type": "investment_bank",
        "tier": 1,
        "aum_range": "N/A",
        "geography": "US",
        "_status": "prospect",
        "competitor": "none",
        "buying_stage": "deploying",
        "has_objections": 0,
        "notes": "Chief AI Officer in place. Yimei Guo Head of Data & AI at AI for Finance Summit June 2025 discussed compound multi-agent systems. Chris Kovel CTO. Evaluating -class tools for RAG depth.",
    },
    {
        "name": "Houlihan Lokey",
        "domain": "hl.com",
        "firm_type": "investment_bank",
        "tier": 1,
        "aum_range": "N/A",
        "geography": "US",
        "_status": "prospect",
        "competitor": "none",
        "buying_stage": "exploring",
        "has_objections": 1,
        "notes": "Allen Fazio CIO: 3-year search for AI orchestration layer. VP explicitly rejected Copilot. Culture-building not tool-deploying. Long cycle — 18 month pipeline.",
    },
    # ── Rogo Customers — Additive Play ────────────────────────────────────────
    {
        "name": "Moelis & Company",
        "domain": "moelis.com",
        "firm_type": "investment_bank",
        "tier": 1,
        "aum_range": "N/A",
        "geography": "US",
        "_status": "prospect",
        "competitor": "rogo",
        "buying_stage": "deploying",
        "has_objections": 0,
        "notes": "Heavy Rogo user for modeling/decks. Actively recruiting AI Thought Leadership role to evaluate GenAI/LLMs. Ted Ferguson CTO.  = document synthesis layer Rogo doesnt cover.",
    },
    {
        "name": "William Blair",
        "domain": "williamblair.com",
        "firm_type": "investment_bank",
        "tier": 2,
        "aum_range": "N/A",
        "geography": "US",
        "_status": "prospect",
        "competitor": "rogo",
        "buying_stage": "evaluating",
        "has_objections": 0,
        "notes": "CTO James Connors integrating Rogo + BlueFlame + Fellow.ai. Evaluating Claude as platform-level intelligence layer. High urgency multi-vendor evaluation.",
    },
    {
        "name": "Piper Sandler",
        "domain": "pipersandler.com",
        "firm_type": "investment_bank",
        "tier": 2,
        "aum_range": "N/A",
        "geography": "US",
        "_status": "prospect",
        "competitor": "rogo",
        "buying_stage": "deploying",
        "has_objections": 0,
        "notes": "Kenny Vargas MD Head of Software & Data Engineering. Job posting confirms Rogo and auxi for pitch books. Deploying stage confirmed.",
    },
    # ── AlphaSense Displacement Targets ──────────────────────────────────────
    {
        "name": "Jefferies",
        "domain": "jefferies.com",
        "firm_type": "investment_bank",
        "tier": 2,
        "aum_range": "N/A",
        "geography": "US",
        "_status": "prospect",
        "competitor": "alphasense",
        "buying_stage": "deploying",
        "has_objections": 0,
        "notes": "Confirmed AlphaSense institutional customer for market intelligence.  angle: private document synthesis AlphaSense doesnt cover.",
    },
    # ── Credit Funds ──────────────────────────────────────────────────────────
    {
        "name": "Oaktree Capital Management",
        "domain": "oaktreecapital.com",
        "firm_type": "credit",
        "tier": 1,
        "aum_range": "$100B+",
        "geography": "US",
        "_status": "prospect",
        "competitor": "none",
        "buying_stage": "evaluating",
        "has_objections": 0,
        "notes": "Brookfield subsidiary. Direct beneficiary of $10B AI infrastructure fund. Natural fit for credit workflow automation.",
    },
    # ── Mid-Market PE ─────────────────────────────────────────────────────────
    {
        "name": "Francisco Partners",
        "domain": "franciscopartners.com",
        "firm_type": "pe",
        "tier": 1,
        "aum_range": "$50B+",
        "geography": "US",
        "_status": "prospect",
        "competitor": "none",
        "buying_stage": "deploying",
        "has_objections": 0,
        "notes": "CONFIRMED: Using Anthropic Claude for PE due diligence — performance metrics and valuations. Anthropic case study Oct 2025. Highest-signal mid-market PE target.",
    },
    {
        "name": "LLR Partners",
        "domain": "llrpartners.com",
        "firm_type": "pe",
        "tier": 2,
        "aum_range": "$5B+",
        "geography": "US",
        "_status": "prospect",
        "competitor": "none",
        "buying_stage": "deploying",
        "has_objections": 0,
        "notes": "Director of AI Strategy hired — explicitly to automate due diligence workflows with LLMs. Job posting confirmed deploying stage.",
    },
    {
        "name": "Warburg Pincus",
        "domain": "warburgpincus.com",
        "firm_type": "pe",
        "tier": 1,
        "aum_range": "$60B+",
        "geography": "US",
        "_status": "prospect",
        "competitor": "none",
        "buying_stage": "evaluating",
        "has_objections": 0,
        "notes": "Raj Kushwaha Co-Head Value Creation and Chief Digital Officer. Portfolio AI investments. Evaluating AI for internal GP workflows.",
    },
    {
        "name": "Brookfield Asset Management",
        "domain": "brookfield.com",
        "firm_type": "pe",
        "tier": 1,
        "aum_range": "$900B+",
        "geography": "US",
        "_status": "prospect",
        "competitor": "none",
        "buying_stage": "deploying",
        "has_objections": 0,
        "notes": "AI Value Creation Office tracking 800+ AI use cases. $10B AI infrastructure fund. Sikander Rashid Global Head of AI Infrastructure. Announced €20B AI program with Macron Feb 2025.",
    },
    {
        "name": "Baird",
        "domain": "rwbaird.com",
        "firm_type": "investment_bank",
        "tier": 2,
        "aum_range": "N/A",
        "geography": "US",
        "_status": "prospect",
        "competitor": "rogo",
        "buying_stage": "deploying",
        "has_objections": 0,
        "notes": "Craig Kennison Director of Research Operations. Equity Research using Rogo for earnings synthesis. Job posting confirms Rogo for M&A due diligence.  = additive document layer.",
    },
    {
        "name": "Genstar Capital",
        "domain": "genstarcapital.com",
        "firm_type": "pe",
        "tier": 2,
        "aum_range": "$20B+",
        "geography": "US",
        "_status": "prospect",
        "competitor": "none",
        "buying_stage": "evaluating",
        "has_objections": 0,
        "notes": "Dr. Donal McMahon Head of AI & Data Science. Dedicated AI/DS function — formal AI infrastructure confirmed. Deploying AI across portfolio compliance and risk.",
    },
    {
        "name": "General Atlantic",
        "domain": "generalatlantic.com",
        "firm_type": "pe",
        "tier": 1,
        "aum_range": "$70B+",
        "geography": "US",
        "_status": "prospect",
        "competitor": "none",
        "buying_stage": "evaluating",
        "has_objections": 0,
        "notes": "Cory Eaves Operating Partner on AI/ML value creation panels. Minority investment in OneStream — AI-first GTM. AI-first portfolio orientation.",
    },
]

CONTACTS = [
    # Blackstone
    {"firm_name": "Blackstone", "name": "John Stecher", "title": "Chief Technology Officer", "role_type": "executive_sponsor", "notes": "Reuters Momentum Finance 2025. Deploying Vectra internally."},
    {"firm_name": "Blackstone", "name": "John Fitzpatrick", "title": "Senior MD & CTO, Alternative Asset Mgmt Technology", "role_type": "technical_champion", "notes": "Canoe AI case study 2025. AI will underachieve in 2 years but overachieve in 10."},
    # Apollo
    {"firm_name": "Apollo Global Management", "name": "Katia Walsh", "title": "AI Lead, Portfolio Performance Solutions", "role_type": "both", "notes": "Authored Winning With Generative AI 2025. MIT study cited her work."},
    # Ares
    {"firm_name": "Ares Management", "name": "Nicolai Wadstrom", "title": "Partner, Co-Head of Ares AI Innovation Group", "role_type": "both", "notes": "Constellation AI Forum Sept 2025. Build vs partner vs buy debate."},
    {"firm_name": "Ares Management", "name": "Carsten Weber", "title": "Senior Technology Advisor", "role_type": "technical_champion", "notes": "PEI Operating Partners Technology Forum panelist."},
    # Silver Lake
    {"firm_name": "Silver Lake", "name": "Stuart Sim", "title": "Managing Director and Head of AI", "role_type": "both", "notes": "2025 hire. Large-scale AI/compute architectures."},
    # Evercore
    {"firm_name": "Evercore", "name": "Stefano Combi", "title": "Vice President", "role_type": "technical_champion", "notes": "AlphaSummit: AI tools produce buyer lists better than analysts."},
    {"firm_name": "Evercore", "name": "Sandeep Saini", "title": "Technology Leader", "role_type": "executive_sponsor", "notes": "25yr experience. Formulates tech strategy for global markets."},
    {"firm_name": "Evercore", "name": "Marc Harris", "title": "Director of Research", "role_type": "technical_champion", "notes": "Hosted inaugural Digital Finance Summit late 2025."},
    # PJT Partners
    {"firm_name": "PJT Partners", "name": "Chris Kovel", "title": "Chief Technology Officer", "role_type": "executive_sponsor", "notes": "Spearheads technology strategy."},
    {"firm_name": "PJT Partners", "name": "Yimei Guo", "title": "Head of Data & AI", "role_type": "both", "notes": "AI for Finance Summit panelist June 2025. Future of Knowledge Management panel."},
    # Houlihan Lokey
    {"firm_name": "Houlihan Lokey", "name": "Allen Fazio", "title": "Chief Information Officer", "role_type": "executive_sponsor", "notes": "3-year search for AI orchestration layer. Building innovation culture not deploying tools."},
    # Moelis
    {"firm_name": "Moelis & Company", "name": "Ted Ferguson", "title": "Chief Technology Officer", "role_type": "executive_sponsor", "notes": "Oversees global tech platforms. Recruiting AI thought leadership."},
    # William Blair
    {"firm_name": "William Blair", "name": "James Connors", "title": "Chief Technology Officer", "role_type": "executive_sponsor", "notes": "Integrating Rogo + BlueFlame + Fellow.ai. Evaluating Claude as platform intelligence layer."},
    # Piper Sandler
    {"firm_name": "Piper Sandler", "name": "Kenny Vargas", "title": "Managing Director, Head of Software & Data Engineering", "role_type": "technical_champion", "notes": "Confirmed Rogo and auxi deployment."},
    # Francisco Partners
    {"firm_name": "Francisco Partners", "name": "Head of Technology (TBD)", "title": "CTO / Head of Technology", "role_type": "executive_sponsor", "notes": "Contact TBD — Hunter enrichment needed. Firm confirmed using Claude for PE due diligence (Anthropic case study Oct 2025)"},
    # LLR Partners
    {"firm_name": "LLR Partners", "name": "Director of AI Strategy (TBD)", "title": "Director of AI Strategy & Value Creation", "role_type": "both", "notes": "Contact TBD — Hunter enrichment needed. Job posting confirms DD workflow automation with LLMs"},
    # Brookfield
    {"firm_name": "Brookfield Asset Management", "name": "Sikander Rashid", "title": "Global Head of AI Infrastructure", "role_type": "executive_sponsor", "notes": "€20B AI announcement with Macron Feb 2025. Forbes SHOOK Summit."},
    # Genstar
    {"firm_name": "Genstar Capital", "name": "Dr. Donal McMahon", "title": "Head of AI & Data Science", "role_type": "both", "notes": "Dedicated AI/DS function. Formal infrastructure confirmed."},
    # Baird
    {"firm_name": "Baird", "name": "Craig Kennison", "title": "Senior Analyst, Director of Research Operations", "role_type": "technical_champion", "notes": "Rogo for earnings synthesis and thesis development."},
]

SIGNALS = [
    # Real signals from research
    {
        "firm_name": "Evercore",
        "contact_name": "Stefano Combi",
        "signal_type": "press",
        "signal_subtype": "transformation",
        "content": "VP at Evercore spoke at AlphaSummit praising AI deep research tools for automating buyer lists, stating outputs look better than anything analysts produced recently. Notes AI is pushing junior analysts up the workstream of opportunity.",
        "source_url": "https://www.alphasense.com/alphasummit",
        "author_name": "Stefano Combi",
        "signal_date": "2025-11-12",
        "buying_stage": "evaluating",
    },
    {
        "firm_name": "PJT Partners",
        "contact_name": "Yimei Guo",
        "signal_type": "press",
        "signal_subtype": "transformation",
        "content": "Head of Data & AI at PJT Partners participated in the AI for Finance Summit June 2025 discussing the shift from basic AI copilots to compound multi-agent decision-makers in the Future of Knowledge Management in Finance panel.",
        "source_url": "https://aifinancesummit.com",
        "author_name": "Yimei Guo",
        "signal_date": "2025-06-04",
        "buying_stage": "deploying",
    },
    {
        "firm_name": "Ares Management",
        "contact_name": "Nicolai Wadstrom",
        "signal_type": "press",
        "signal_subtype": "evaluation",
        "content": "Co-Head of Ares AI Innovation Group spoke at Constellation AI Forum Sept 2025 on physical agentic AI systems and the build vs. partner vs. buy debate — confirming active vendor evaluation mode.",
        "source_url": "https://constellationr.com/ai-forum",
        "author_name": "Nicolai Wadstrom",
        "signal_date": "2025-09-15",
        "buying_stage": "evaluating",
    },
    {
        "firm_name": "Apollo Global Management",
        "contact_name": "Katia Walsh",
        "signal_type": "press",
        "signal_subtype": "transformation",
        "content": "Apollo AI Lead authored Winning With Generative AI book in 2025. MIT study cited her work on AI reasoning systems. Confirmed deployment of AI for M&A due diligence automation.",
        "source_url": "https://www.apolloglobal.com",
        "author_name": "Katia Walsh",
        "signal_date": "2025-08-01",
        "buying_stage": "deploying",
    },
    {
        "firm_name": "Blackstone",
        "contact_name": "John Fitzpatrick",
        "signal_type": "press",
        "signal_subtype": "transformation",
        "content": "Blackstone Senior MD & CTO featured in Canoe AI case study 2025. Publicly stated AI will underwhelm in 2 years but overachieve in 10 — signals measured, production-focused adoption strategy.",
        "source_url": "https://canoe.ai/case-studies/blackstone",
        "author_name": "John Fitzpatrick",
        "signal_date": "2025-07-15",
        "buying_stage": "evaluating",
    },
    {
        "firm_name": "Francisco Partners",
        "contact_name": None,
        "signal_type": "press",
        "signal_subtype": "evaluation",
        "content": "Francisco Partners confirmed using Anthropic Claude for PE due diligence — specifically for performance metrics and valuations analysis. Named in Anthropic case study on advancing Claude for financial services.",
        "source_url": "https://www.anthropic.com/news/advancing-claude-for-financial-services",
        "author_name": None,
        "signal_date": "2025-10-01",
        "buying_stage": "deploying",
    },
    {
        "firm_name": "LLR Partners",
        "contact_name": None,
        "signal_type": "hiring",
        "signal_subtype": "transformation",
        "content": "LLR Partners posted for Director of AI Strategy & Value Creation — role explicitly tasked with automating due diligence workflows with LLMs. Deploying stage confirmed by job posting language.",
        "source_url": "https://www.linkedin.com/jobs",
        "author_name": None,
        "signal_date": "2025-11-01",
        "buying_stage": "deploying",
    },
    {
        "firm_name": "William Blair",
        "contact_name": "James Connors",
        "signal_type": "hiring",
        "signal_subtype": "evaluation",
        "content": "William Blair Lead Data Scientist/AI Engineer posting reveals active integration of Rogo, BlueFlame, Fellow.ai via APIs. Separately evaluating Claude as platform-level intelligence layer to enhance legacy infrastructure.",
        "source_url": "https://www.linkedin.com/jobs",
        "author_name": None,
        "signal_date": "2026-01-15",
        "buying_stage": "evaluating",
    },
    {
        "firm_name": "Brookfield Asset Management",
        "contact_name": "Sikander Rashid",
        "signal_type": "press",
        "signal_subtype": "transformation",
        "content": "Brookfield Global Head of AI Infrastructure announced €20B AI infrastructure investment program in France alongside President Macron. Firm tracking 800+ AI use cases across global industrial portfolio through dedicated AI Value Creation Office.",
        "source_url": "https://www.brookfield.com",
        "author_name": "Sikander Rashid",
        "signal_date": "2025-02-10",
        "buying_stage": "deploying",
    },
    {
        "firm_name": "Moelis & Company",
        "contact_name": None,
        "signal_type": "hiring",
        "signal_subtype": "evaluation",
        "content": "Moelis actively recruiting for Thought Leadership & Enablement role tasked with researching generative AI, LLMs, and workflow automation tools. Role designed to benchmark Moelis against market trends and build internal AI integration playbooks.",
        "source_url": "https://www.linkedin.com/jobs",
        "author_name": None,
        "signal_date": "2026-02-01",
        "buying_stage": "evaluating",
    },
    # Twitter signals
    {
        "firm_name": None,
        "contact_name": None,
        "signal_type": "twitter",
        "signal_subtype": "evaluation",
        "content": "The finance AI tool stack looks like this today: ChatGPT, Claude, Gemini or Perplexity for basic research... AlphaSense, , or Rogo for detailed dives into 10K/10Qs, equity research reports and transcripts... The real challenge is not creating a compelling product. It is having to jump through compliance review hoops at every financial services company.",
        "source_url": "https://twitter.com/BoringBiz_",
        "author_handle": "BoringBiz_",
        "author_name": "BoringBiz_",
        "signal_date": "2026-02-04",
        "buying_stage": "evaluating",
    },
    {
        "firm_name": None,
        "contact_name": None,
        "signal_type": "twitter",
        "signal_subtype": "pain",
        "content": "The Three-Layer Cake of Hedge Fund Due Diligence... it will probably take us 3-4 weeks... 150 hours on a name... Desktop Research... Primary Research... Insight Formation... AI adoption has been quite a bit slower among most institutional stock pickers than you might think.",
        "source_url": "https://twitter.com/FundamentEdge",
        "author_handle": "FundamentEdge",
        "author_name": "Brett Caughran",
        "signal_date": "2026-03-24",
        "buying_stage": "exploring",
    },
    {
        "firm_name": None,
        "contact_name": None,
        "signal_type": "twitter",
        "signal_subtype": "competitor_frustration",
        "content": "Tried getting Alphasense into the research process, didn't realize it was THAT expensive... recreating AlphaSense summaries is common but not alpha.",
        "source_url": "https://twitter.com/bucketshopcap",
        "author_handle": "bucketshopcap",
        "author_name": "bucketshopcap",
        "signal_date": "2026-04-02",
        "buying_stage": "evaluating",
    },
]


def seed():
    """Seed database with firms, contacts, and signals."""
    init_db()
    conn = get_db()
    c = conn.cursor()

    firm_name_to_id = {}

    # ── Insert firms ──────────────────────────────────────────────────────────
    print("Seeding firms...")
    for firm in FIRMS:
        existing = c.execute(
            "SELECT id FROM firms WHERE name = ?", (firm["name"],)
        ).fetchone()
        if existing:
            firm_name_to_id[firm["name"]] = existing["id"]
            print(f"  SKIP (exists): {firm['name']}")
            continue

        c.execute("""
            INSERT INTO firms (name, domain, firm_type, tier, aum_range, geography,
                               _status, competitor, buying_stage, has_objections, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            firm["name"], firm["domain"], firm["firm_type"], firm["tier"],
            firm["aum_range"], firm["geography"], firm["_status"],
            firm["competitor"], firm["buying_stage"], firm["has_objections"],
            firm["notes"]
        ))
        firm_id = c.lastrowid
        firm_name_to_id[firm["name"]] = firm_id
        print(f"  + {firm['name']} (id={firm_id})")

    conn.commit()

    # ── Insert contacts ───────────────────────────────────────────────────────
    print("\nSeeding contacts...")
    for contact in CONTACTS:
        firm_id = firm_name_to_id.get(contact["firm_name"])
        if not firm_id:
            print(f"  SKIP (no firm): {contact['name']} at {contact['firm_name']}")
            continue

        existing = c.execute(
            "SELECT id FROM contacts WHERE name = ? AND firm_id = ?",
            (contact["name"], firm_id)
        ).fetchone()
        if existing:
            print(f"  SKIP (exists): {contact['name']}")
            continue

        c.execute("""
            INSERT INTO contacts (firm_id, name, title, role_type, notes)
            VALUES (?, ?, ?, ?, ?)
        """, (firm_id, contact["name"], contact["title"],
              contact["role_type"], contact["notes"]))
        print(f"  + {contact['name']} at {contact['firm_name']}")

    conn.commit()

    # ── Insert signals ────────────────────────────────────────────────────────
    print("\nSeeding signals...")
    from utils.helpers import calculate_freshness_days

    for signal in SIGNALS:
        firm_id = firm_name_to_id.get(signal.get("firm_name"))
        contact_id = None

        if signal.get("contact_name") and firm_id:
            row = c.execute(
                "SELECT id FROM contacts WHERE name = ? AND firm_id = ?",
                (signal["contact_name"], firm_id)
            ).fetchone()
            if row:
                contact_id = row["id"]

        freshness = calculate_freshness_days(signal.get("signal_date", ""))

        existing = c.execute(
            "SELECT id FROM signals WHERE content = ?",
            (signal["content"][:100],)
        ).fetchone()
        if existing:
            print(f"  SKIP (exists): {signal['signal_type']} signal")
            continue

        c.execute("""
            INSERT INTO signals (firm_id, contact_id, signal_type, signal_subtype,
                                 content, source_url, author_handle, author_name,
                                 signal_date, freshness_days, buying_stage)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            firm_id, contact_id, signal["signal_type"], signal["signal_subtype"],
            signal["content"], signal.get("source_url"),
            signal.get("author_handle"), signal.get("author_name"),
            signal.get("signal_date"), freshness, signal.get("buying_stage")
        ))
        print(f"  + {signal['signal_type']} signal: {signal['content'][:60]}...")

    conn.commit()
    conn.close()
    print(f"\n✓ Seed complete — {len(FIRMS)} firms, {len(CONTACTS)} contacts, {len(SIGNALS)} signals")


if __name__ == "__main__":
    seed()
