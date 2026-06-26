"""
Demo 03: PRD Review Multi-Subagent Orchestration.

Pipeline:
  Writer (sync) composes an initial PRD,
  then 4 reviewers run in parallel (async),
  then Editor (sync) merges feedback into a final PRD v2.

Sub-agents: writer, product-strategist, technical-feasibility,
            ux-researcher, risk-analyst, editor
Workspace: demo/workspace_03/
"""
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain.chat_models import init_chat_model

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
_DEMO_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _DEMO_DIR.parent
load_dotenv(_DEMO_DIR / ".env")

WORKSPACE = str((_PROJECT_ROOT / "demo/workspace_03").resolve())

PRODUCT_CONCEPT = (
    "智慧校园导航助手 (Smart Campus Navigation Assistant) — "
    "一款基于AR/语音的校内导航App，帮助新生和访客快速找到教室、办公室和设施，"
    "同时提供校园活动推荐和实时拥挤度信息。"
)


def _build_model():
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY not found in environment or demo/.env\n"
            "Create demo/.env with: DEEPSEEK_API_KEY=your-key-here"
        )
    return init_chat_model(
        model="openai:deepseek-v4-flash",
        api_key=api_key,
        base_url="https://api.deepseek.com",
    )


# ---------------------------------------------------------------------------
# Sub-agent system prompts
# ---------------------------------------------------------------------------

WRITER_PROMPT = f"""\
You are a **senior product manager**. Write a high-quality PRD
(Product Requirements Document) for a given product concept.

Your PRD should include these sections:
1. Product Overview — problem statement, target users, value proposition
2. User Personas — 2-3 representative user types with goals and pain points
3. Core Features — prioritized feature list with P0/P1/P2 classification
4. User Stories — key user stories with acceptance criteria
5. Success Metrics — measurable KPIs for product success
6. Non-Functional Requirements — performance, security, accessibility
7. Assumptions & Constraints

Write the initial PRD to {WORKSPACE}/prd_v1.md using the write_file tool,
then output a brief summary as your final message. Use Chinese for the PRD.
"""

PRODUCT_STRATEGIST_PROMPT = f"""\
You are a **product strategy analyst**. Review the PRD at {WORKSPACE}/prd_v1.md
and evaluate it from a strategic perspective.

Focus on:
1. Market positioning — is there a clear market need? Who are the competitors?
2. Business value — what is the ROI? Monetization strategy?
3. Differentiation — what makes this product unique vs existing solutions?
4. Roadmap alignment — does the feature set make sense for an MVP?
5. Go-to-market feasibility — can this be launched within a typical semester cycle?

Write your review to {WORKSPACE}/review_strategy.md using the write_file tool,
then output a brief summary as your final message. Use Chinese for your analysis.
"""

TECH_FEASIBILITY_PROMPT = f"""\
You are a **technical architect**. Review the PRD at {WORKSPACE}/prd_v1.md
and evaluate it from a technical feasibility perspective.

Focus on:
1. Technology stack — what would be required? (AR framework, map SDK, backend)
2. Integration complexity — campus map data, indoor positioning, real-time data
3. Technical risks — what are the hardest engineering challenges?
4. Architecture viability — can this be built by a small team in a semester?
5. Dependencies — external APIs, data sources, infrastructure needs

Write your review to {WORKSPACE}/review_tech.md using the write_file tool,
then output a brief summary as your final message. Use Chinese for your analysis.
"""

UX_RESEARCHER_PROMPT = f"""\
You are a **UX researcher**. Review the PRD at {WORKSPACE}/prd_v1.md
and evaluate it from a user experience perspective.

Focus on:
1. User personas — are they well-defined and representative?
2. User journey — is the core flow intuitive? Are edge cases covered?
3. Interaction design — AR navigation UX, voice commands, accessibility
4. Information architecture — how should features be organized?
5. UX risks — cognitive load, learning curve, multi-modal interaction conflicts

Write your review to {WORKSPACE}/review_ux.md using the write_file tool,
then output a brief summary as your final message. Use Chinese for your analysis.
"""

RISK_ANALYST_PROMPT = f"""\
You are a **project risk analyst**. Review the PRD at {WORKSPACE}/prd_v1.md
and evaluate it from a risk management perspective.

Focus on:
1. Timeline risks — can the feature set be delivered in the assumed timeframe?
2. Resource risks — team composition, skill gaps, hardware/software needs
3. Data & privacy risks — location tracking, user data handling, compliance
4. Adoption risks — will users actually use it? What are the adoption barriers?
5. Dependency risks — external systems, approvals, campus partnerships

Write your review to {WORKSPACE}/review_risk.md using the write_file tool,
then output a brief summary as your final message. Use Chinese for your analysis.
"""

EDITOR_PROMPT = f"""\
You are a **senior product editor**. You will receive four review reports
for a PRD. Your job is to merge all feedback into a polished PRD v2.

Do the following:
1. Read the original PRD: {WORKSPACE}/prd_v1.md
2. Read the four review reports:
   - {WORKSPACE}/review_strategy.md
   - {WORKSPACE}/review_tech.md
   - {WORKSPACE}/review_ux.md
   - {WORKSPACE}/review_risk.md
3. Synthesize all feedback — incorporate valuable suggestions, resolve conflicts
4. Produce the final PRD v2 with:
   - Executive Summary (key decisions from review)
   - Product Overview (refined)
   - User Personas (with UX feedback)
   - Core Features (re-prioritized)
   - User Stories (updated)
   - Technical Approach (from feasibility review)
   - Risk Mitigation Plan (from risk review)
   - Success Metrics (refined)
5. Write the final PRD to {WORKSPACE}/prd_v2_final.md

Use Chinese for the report. Prioritize actionable, specific improvements."""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    print("=" * 60)
    print("Demo 03: PRD Review Multi-Subagent Orchestration")
    print("=" * 60)

    model = _build_model()
    print(f"Product concept: {PRODUCT_CONCEPT}")

    # Ensure workspace exists
    Path(WORKSPACE).mkdir(parents=True, exist_ok=True)

    backend = FilesystemBackend(
        root_dir=str(_PROJECT_ROOT.resolve()),
        virtual_mode=False,
    )

    agent = create_deep_agent(
        model=model,
        backend=backend,
        system_prompt=f"""You are a PRD review orchestrator. Your job:

1. Write a PRD using the **writer** sub-agent for this concept:
   "{PRODUCT_CONCEPT}"
   The writer will save it to {WORKSPACE}/prd_v1.md.

2. After the PRD is written, spawn ALL 4 reviewer sub-agents IN PARALLEL:
   - product-strategist (strategy & market)
   - technical-feasibility (tech stack & architecture)
   - ux-researcher (user experience & personas)
   - risk-analyst (risks & mitigation)
   Each reviewer will read the PRD and write their review to {WORKSPACE}/.

3. After all 4 reviews are done, spawn the **editor** to merge all feedback
   and produce the final PRD v2 at {WORKSPACE}/prd_v2_final.md.

IMPORTANT: The 4 reviewers MUST run in parallel, not sequentially.""",
        subagents=[
            {
                "name": "writer",
                "description": "Write a structured PRD from a product concept",
                "system_prompt": WRITER_PROMPT,
            },
            {
                "name": "product-strategist",
                "description": "Review PRD from strategy perspective: market, business value, differentiation",
                "system_prompt": PRODUCT_STRATEGIST_PROMPT,
            },
            {
                "name": "technical-feasibility",
                "description": "Review PRD from tech perspective: stack, architecture, integration, risks",
                "system_prompt": TECH_FEASIBILITY_PROMPT,
            },
            {
                "name": "ux-researcher",
                "description": "Review PRD from UX perspective: personas, journey, interaction, accessibility",
                "system_prompt": UX_RESEARCHER_PROMPT,
            },
            {
                "name": "risk-analyst",
                "description": "Review PRD from risk perspective: timeline, resources, privacy, adoption",
                "system_prompt": RISK_ANALYST_PROMPT,
            },
            {
                "name": "editor",
                "description": "Merge all reviews into a final polished PRD v2",
                "system_prompt": EDITOR_PROMPT,
            },
        ],
    )

    t0 = time.time()
    result = agent.invoke({
        "messages": [{
            "role": "user",
            "content": (
                f"Run the full PRD review pipeline for the Smart Campus Navigation Assistant.\\n\\n"
                f"Step 1: Use the writer to compose the PRD → {WORKSPACE}/prd_v1.md\\n"
                f"Step 2: Spawn 4 reviewers IN PARALLEL (product-strategist, technical-feasibility, "
                f"ux-researcher, risk-analyst)\\n"
                f"Step 3: Use the editor to produce {WORKSPACE}/prd_v2_final.md\\n"
            ),
        }],
    })
    elapsed = time.time() - t0

    final = result["messages"][-1].content if result.get("messages") else "(empty)"
    print(f"\\n✅ Demo 03 complete in {elapsed:.1f}s!")
    print(f"   Agents: writer, product-strategist, technical-feasibility, ux-researcher, risk-analyst, editor")
    print(f"   Workspace: {WORKSPACE}/")
    print(f"   Final PRD: {WORKSPACE}/prd_v2_final.md")


if __name__ == "__main__":
    main()
