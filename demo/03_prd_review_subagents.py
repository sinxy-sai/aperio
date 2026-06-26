"""
Demo 03: PRD Review Multi-Subagent Orchestration.

Pipeline:
  Writer (sync) composes an initial PRD,
  then 4 reviewers run in parallel (async),
  then Editor (sync) merges feedback into a final consolidated PRD.

Sub-agents: writer, product-strategist, technical-feasibility,
            ux-researcher, risk-analyst, editor
Workspace: demo/workspace_03/

Pattern: mirrors Demo 02 — asyncio.gather for parallel sub-agents,
         shared FilesystemBackend, try/except in each runner.
"""
import asyncio
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

WORKSPACE = "demo/workspace_03"
# Sample product concept the PRD will be written for
PRODUCT_CONCEPT = (
    "智慧校园导航助手 (Smart Campus Navigation Assistant) — "
    "一款基于AR/语音的校内导航App，帮助新生和访客快速找到教室、办公室和设施，"
    "同时提供校园活动推荐和实时拥挤度信息。"
)


def _build_model():
    """Create the shared model instance."""
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
# Shared backend
#
# virtual_mode=False so agents can write to and read from the workspace.
# root_dir is the project root so relative paths like "demo/workspace_03/" resolve.
# ---------------------------------------------------------------------------
_SHARED_BACKEND = FilesystemBackend(
    root_dir=str(_PROJECT_ROOT.resolve()),
    virtual_mode=False,
)


# ---------------------------------------------------------------------------
# Agent system prompts
# ---------------------------------------------------------------------------

WRITER_PROMPT = """\
You are a **senior product manager**. Your job is to write a high-quality PRD
(Product Requirements Document) for a given product concept.

Your PRD should include these sections:
1. Product Overview — problem statement, target users, value proposition
2. User Personas — 2-3 representative user types with goals and pain points
3. Core Features — prioritized feature list with P0/P1/P2 classification
4. User Stories — key user stories with acceptance criteria
5. Success Metrics — measurable KPIs for product success
6. Non-Functional Requirements — performance, security, accessibility
7. Assumptions & Constraints

Write the initial PRD to `demo/workspace_03/prd_v1.md` using the write_file tool,
then output a brief summary as your final message.  Use Chinese for the PRD.
"""


PRODUCT_STRATEGIST_PROMPT = """\
You are a **product strategy analyst**. Review the PRD at `demo/workspace_03/prd_v1.md`
and evaluate it from a strategic perspective.

Focus on:
1. Market positioning — is there a clear market need? Who are the competitors?
2. Business value — what is the ROI? Monetization strategy?
3. Differentiation — what makes this product unique vs existing solutions?
4. Roadmap alignment — does the feature set make sense for an MVP?
5. Go-to-market feasibility — can this be launched within a typical semester cycle?

Write your review to `demo/workspace_03/review_strategy.md` using the write_file tool,
then output a brief summary as your final message.  Use Chinese for your analysis.
"""


TECH_FEASIBILITY_PROMPT = """\
You are a **technical architect**. Review the PRD at `demo/workspace_03/prd_v1.md`
and evaluate it from a technical feasibility perspective.

Focus on:
1. Technology stack — what would be required? (AR framework, map SDK, backend stack)
2. Integration complexity — campus map data, indoor positioning, real-time data
3. Technical risks — what are the hardest engineering challenges?
4. Architecture viability — can this be built by a small team in a semester?
5. Dependencies — external APIs, data sources, infrastructure needs

Write your review to `demo/workspace_03/review_tech.md` using the write_file tool,
then output a brief summary as your final message.  Use Chinese for your analysis.
"""


UX_RESEARCHER_PROMPT = """\
You are a **UX researcher**. Review the PRD at `demo/workspace_03/prd_v1.md`
and evaluate it from a user experience perspective.

Focus on:
1. User personas — are they well-defined and representative?
2. User journey — is the core flow intuitive? Are edge cases covered?
3. Interaction design — AR navigation UX, voice commands, accessibility
4. Information architecture — how should features be organized?
5. UX risks — cognitive load, learning curve, multi-modal interaction conflicts

Write your review to `demo/workspace_03/review_ux.md` using the write_file tool,
then output a brief summary as your final message.  Use Chinese for your analysis.
"""


RISK_ANALYST_PROMPT = """\
You are a **project risk analyst**. Review the PRD at `demo/workspace_03/prd_v1.md`
and evaluate it from a risk management perspective.

Focus on:
1. Timeline risks — can the feature set be delivered in the assumed timeframe?
2. Resource risks — team composition, skill gaps, hardware/software needs
3. Data & privacy risks — location tracking, user data handling, compliance
4. Adoption risks — will users actually use it? What are the adoption barriers?
5. Dependency risks — external systems, approvals, campus partnerships

Write your review to `demo/workspace_03/review_risk.md` using the write_file tool,
then output a brief summary as your final message.  Use Chinese for your analysis.
"""


EDITOR_PROMPT = """\
You are a **senior product editor**. You will receive four review reports
(product strategy, technical feasibility, UX research, risk analysis) for a PRD.
Your job is to merge all feedback and produce a final, polished PRD v2.

Do the following:
1. Read the original PRD: `demo/workspace_03/prd_v1.md`
2. Read the four review reports:
   - `demo/workspace_03/review_strategy.md`
   - `demo/workspace_03/review_tech.md`
   - `demo/workspace_03/review_ux.md`
   - `demo/workspace_03/review_risk.md`
3. Synthesize all feedback — incorporate valuable suggestions, resolve conflicts
4. Produce the final PRD v2 with these updated sections:
   - Executive Summary (with key decisions from review)
   - Product Overview (refined)
   - User Personas (with UX feedback incorporated)
   - Core Features (re-prioritized based on all reviews)
   - User Stories (updated)
   - Technical Approach (from feasibility review)
   - Risk Mitigation Plan (from risk review)
   - Success Metrics (refined)
5. Write the final PRD to `demo/workspace_03/prd_v2_final.md`

Use Chinese for the report.  Prioritize actionable, specific improvements.
"""


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def _build_analysis_agent(system_prompt: str, model):
    """Build a single-purpose sub-agent sharing the global backend."""
    return create_deep_agent(
        model=model,
        backend=_SHARED_BACKEND,
        system_prompt=system_prompt,
    )


async def run_reviewer(name: str, agent, task: str) -> dict:
    """Run one reviewer agent asynchronously and return its result metadata."""
    print(f"  [{name}] starting ...")
    t0 = time.time()
    try:
        result = await agent.ainvoke({
            "messages": [{"role": "user", "content": task}],
        })
        elapsed = time.time() - t0
        final_msg = result["messages"][-1].content if result.get("messages") else "(no response)"
        short_preview = final_msg[:120].replace("\n", " ")
        print(f"  [{name}] done in {elapsed:.1f}s -> {short_preview}...")
        return {"agent": name, "elapsed": elapsed, "ok": True, "preview": final_msg[:200]}
    except Exception as exc:
        elapsed = time.time() - t0
        print(f"  [{name}] FAILED in {elapsed:.1f}s: {exc}")
        return {"agent": name, "elapsed": elapsed, "ok": False, "error": str(exc)}


async def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    print("=" * 60)
    print("Demo 03: PRD Review Multi-Subagent Orchestration")
    print("=" * 60)

    model = _build_model()
    print(f"Product concept: {PRODUCT_CONCEPT}")

    # Ensure workspace directory exists
    Path(WORKSPACE).mkdir(parents=True, exist_ok=True)

    # ---- Phase 1: Writer (sync) composes initial PRD -------------------------------
    print("\n[Phase 1] Writer composing initial PRD (sync) ...")
    writer = _build_analysis_agent(WRITER_PROMPT, model)
    t_phase1 = time.time()
    try:
        writer_result = writer.invoke({
            "messages": [{
                "role": "user",
                "content": (
                    f"Write a PRD for this product concept:\n\n{PRODUCT_CONCEPT}\n\n"
                    "First explore the workspace, then write the PRD to "
                    "demo/workspace_03/prd_v1.md using the write_file tool. "
                    "Output a brief summary as your final message. "
                    "使用中文撰写PRD。"
                ),
            }],
        })
        phase1_elapsed = time.time() - t_phase1
        final_msg = writer_result["messages"][-1].content if writer_result.get("messages") else "(no response)"
        print(f"  Writer done in {phase1_elapsed:.1f}s -> {final_msg[:150]}")
    except Exception as exc:
        phase1_elapsed = time.time() - t_phase1
        print(f"  Writer FAILED in {phase1_elapsed:.1f}s: {exc}")
        print(f"\n{'=' * 60}")
        print("Demo 03 aborted — Writer failed.")
        print(f"{'=' * 60}")
        return

    # ---- Phase 2: Run 4 reviewers in parallel --------------------------------------
    print(f"\n[Phase 2] Running 4 reviewers in parallel ...")

    reviewers = {
        "product-strategist":  _build_analysis_agent(PRODUCT_STRATEGIST_PROMPT,  model),
        "technical-feasibility": _build_analysis_agent(TECH_FEASIBILITY_PROMPT, model),
        "ux-researcher":       _build_analysis_agent(UX_RESEARCHER_PROMPT,       model),
        "risk-analyst":        _build_analysis_agent(RISK_ANALYST_PROMPT,        model),
    }

    review_task_template = (
        "Review the PRD document at `demo/workspace_03/prd_v1.md`. "
        "First use read_file to read it, then analyze it according to your "
        "system prompt role. Write your review to the output file specified "
        "in your system prompt using the write_file tool. "
        "Output a brief summary as your final message. "
        "使用中文进行分析。"
    )

    t_phase2 = time.time()
    results = await asyncio.gather(*(
        run_reviewer(name, agent, review_task_template)
        for name, agent in reviewers.items()
    ))
    phase2_elapsed = time.time() - t_phase2
    print(f"\n  All 4 reviewers completed in {phase2_elapsed:.1f}s total (parallel).")

    ok_count = sum(1 for r in results if r["ok"])
    print(f"  Success: {ok_count}/4")

    # ---- Phase 3: Editor (sync) merges feedback ------------------------------------
    print(f"\n[Phase 3] Editor merging feedback into final PRD v2 (sync) ...")
    editor = create_deep_agent(
        model=model,
        backend=_SHARED_BACKEND,
        system_prompt=EDITOR_PROMPT,
    )
    t_phase3 = time.time()
    try:
        editor_result = editor.invoke({
            "messages": [{
                "role": "user",
                "content": (
                    "The initial PRD and all four review reports have been written "
                    "to the workspace. Read them:\n"
                    "  - demo/workspace_03/prd_v1.md\n"
                    "  - demo/workspace_03/review_strategy.md\n"
                    "  - demo/workspace_03/review_tech.md\n"
                    "  - demo/workspace_03/review_ux.md\n"
                    "  - demo/workspace_03/review_risk.md\n\n"
                    "Merge all feedback into a final PRD v2 at "
                    "demo/workspace_03/prd_v2_final.md using write_file. "
                    "最后输出中文摘要。"
                ),
            }],
        })
    except Exception as exc:
        phase3_elapsed = time.time() - t_phase3
        print(f"  Editor FAILED in {phase3_elapsed:.1f}s: {exc}")
        print(f"\n{'=' * 60}")
        print("Demo 03 complete!")
        print(f"  Phase 1 (Writer):      {phase1_elapsed:.1f}s")
        print(f"  Phase 2 (4x parallel): {phase2_elapsed:.1f}s")
        print(f"  Phase 3 (Editor):      FAILED ({exc})")
        print(f"  Workspace: {WORKSPACE}/")
        print(f"  Agents: writer, product-strategist, technical-feasibility, ux-researcher, risk-analyst, editor")
        print(f"{'=' * 60}")
        return
    phase3_elapsed = time.time() - t_phase3
    print(f"  Editor done in {phase3_elapsed:.1f}s")
    final_content = editor_result["messages"][-1].content if editor_result.get("messages") else "(no response)"
    print(f"  Final: {final_content[:200]}")

    # ---- Done -----------------------------------------------------------------------
    print(f"\n{'=' * 60}")
    print("Demo 03 complete!")
    print(f"  Phase 1 (Writer):      {phase1_elapsed:.1f}s")
    print(f"  Phase 2 (4x parallel): {phase2_elapsed:.1f}s")
    print(f"  Phase 3 (Editor):      {phase3_elapsed:.1f}s")
    print(f"  Workspace: {WORKSPACE}/")
    print(f"  Agents: writer, product-strategist, technical-feasibility, ux-researcher, risk-analyst, editor")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(main())
