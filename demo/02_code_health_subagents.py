"""
Demo 02: Code Health Multi-Subagent Orchestration.

Pipeline:
  Orchestrator (sync) spawns 4 analysis sub-agents in parallel (async),
  then Summarizer (sync) merges the results into a consolidated report.

Sub-agents: architect, security-analyst, dependency-checker, doc-reviewer, summarizer
Workspace: demo/workspace_02/
"""
import asyncio
import os
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

WORKSPACE = "demo/workspace_02"
# Relative path from project root to target code (used in task prompts)
TARGET_CODE_REL = "full-stack-fastapi-template-master/backend/app"
# Absolute path for existence checks
TARGET_CODE_ABS = str((_PROJECT_ROOT / TARGET_CODE_REL).resolve())


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
# virtual_mode=False so agents can read the real filesystem.
# root_dir is the project root so relative paths like "full-stack-..." resolve.
# Agents are explicitly instructed to write output into demo/workspace_02/.
# ---------------------------------------------------------------------------
_SHARED_BACKEND = FilesystemBackend(
    root_dir=str(_PROJECT_ROOT.resolve()),
    virtual_mode=False,
)


# ---------------------------------------------------------------------------
# Agent system prompts
# ---------------------------------------------------------------------------

ARCHITECT_PROMPT = """\
You are a **code architect**. Analyze the given codebase for architectural patterns.

Focus on:
1. Module structure and organization — how are files/directories laid out?
2. Design patterns in use (MVC, repository, dependency injection, etc.)
3. Coupling and cohesion — are modules well-separated or tangled?
4. Code layering — is there a clear separation between API, business logic, and data layers?
5. Strengths and weaknesses of the current architecture

Write your findings to `demo/workspace_02/architect.md` using the write_file tool,
then output a brief summary as your final message.  Use Chinese for your analysis.
"""


SECURITY_PROMPT = """\
You are a **security analyst**. Audit the given codebase for security issues.

Focus on:
1. Authentication and authorization — are there any gaps?
2. Input validation — is user input properly sanitized?
3. Secret management — are API keys, passwords, tokens handled safely?
4. SQL injection, XSS, CSRF risks
5. Unsafe deserialization, path traversal, or other OWASP Top-10 concerns

Write your findings to `demo/workspace_02/security.md` using the write_file tool,
then output a brief summary as your final message.  Use Chinese for your analysis.
"""


DEPENDENCY_PROMPT = """\
You are a **dependency checker**. Review the codebase's dependencies and imports.

Focus on:
1. External dependencies — what third-party packages are used? Are versions pinned?
2. Import graph — are there circular imports? Unused imports?
3. Dependency freshness — are any packages outdated or unmaintained?
4. Direct vs transitive — are dependencies explicitly declared?
5. Potential version conflicts or security advisories

Write your findings to `demo/workspace_02/dependencies.md` using the write_file tool,
then output a brief summary as your final message.  Use Chinese for your analysis.
"""


DOC_REVIEWER_PROMPT = """\
You are a **documentation reviewer**. Evaluate the codebase's documentation quality.

Focus on:
1. Module/class/function docstrings — are they present and useful?
2. Inline comments — do they explain *why*, not just *what*?
3. README / top-level docs — is there an onboarding guide?
4. API documentation — are endpoints, parameters, and responses documented?
5. Areas where documentation is missing or could be improved

Write your findings to `demo/workspace_02/doc_review.md` using the write_file tool,
then output a brief summary as your final message.  Use Chinese for your analysis.
"""


SUMMARIZER_PROMPT = """\
You are a **technical report summarizer**. You will be given four analysis reports
(architecture, security, dependencies, documentation). Your job is to merge them
into a single consolidated code health report.

Do the following:
1. First, read the four analysis files:
   - `demo/workspace_02/architect.md`
   - `demo/workspace_02/security.md`
   - `demo/workspace_02/dependencies.md`
   - `demo/workspace_02/doc_review.md`
2. Synthesize the key findings from each into a coherent report.
3. Prioritize issues by severity (critical / warning / info).
4. Write the final consolidated report to `demo/workspace_02/code_health_report.md`.

Use Chinese for the report.  The report should have clear sections for each
analysis dimension plus an executive summary at the top.
"""


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def _build_analysis_agent(system_prompt: str, model):
    """Build a single-purpose analysis sub-agent sharing the global backend."""
    return create_deep_agent(
        model=model,
        backend=_SHARED_BACKEND,
        system_prompt=system_prompt,
    )


async def run_analysis_agent(name: str, agent, task: str) -> dict:
    """Run one analysis agent asynchronously and return its result metadata."""
    print(f"  [{name}] starting ...")
    t0 = time.time()
    try:
        result = await agent.ainvoke({
            "messages": [{"role": "user", "content": task}],
        })
        elapsed = time.time() - t0
        final_msg = result["messages"][-1].content
        # Replace emoji and other non-ASCII chars that can crash GBK console
        short_preview = final_msg[:120].replace("\n", " ")
        # Sanitize: keep only printable ASCII + common CJK for preview
        safe = []
        for ch in short_preview:
            if ord(ch) < 0x10000:  # drop high-plane chars that break GBK
                safe.append(ch)
        safe_preview = "".join(safe)
        print(f"  [{name}] done in {elapsed:.1f}s -> {safe_preview}...")
        return {"agent": name, "elapsed": elapsed, "ok": True, "preview": safe_preview}
    except Exception as exc:
        elapsed = time.time() - t0
        print(f"  [{name}] FAILED in {elapsed:.1f}s: {exc}")
        return {"agent": name, "elapsed": elapsed, "ok": False, "error": str(exc)}


async def main():
    print("=" * 60)
    print("Demo 02: Code Health Multi-Subagent Orchestration")
    print("=" * 60)

    model = _build_model()

    # Verify target exists
    target_path = Path(TARGET_CODE_ABS)
    if not target_path.exists():
        print(f"WARNING: target code path '{TARGET_CODE_REL}' not found - agents may fail.")
    else:
        py_files = list(target_path.glob("**/*.py"))
        print(f"Target: {TARGET_CODE_REL}  ({len(py_files)} .py files found)")

    # Ensure workspace directory exists
    Path(WORKSPACE).mkdir(parents=True, exist_ok=True)

    task_template = (
        f"Analyze the codebase under '{TARGET_CODE_REL}'. "
        "First use ls and glob tools to explore the directory structure, "
        "then use read_file and grep to examine the source code in depth. "
        "Write your report using write_file (see your system prompt for the exact output path), "
        "then output a brief summary as your final message. "
        "Use Chinese for your analysis."
    )

    # ---- Phase 1: Build all agents ------------------------------------------------
    print("\n[Phase 1] Building agents ...")
    agents = {
        "architect":          _build_analysis_agent(ARCHITECT_PROMPT,     model),
        "security-analyst":   _build_analysis_agent(SECURITY_PROMPT,      model),
        "dependency-checker": _build_analysis_agent(DEPENDENCY_PROMPT,    model),
        "doc-reviewer":       _build_analysis_agent(DOC_REVIEWER_PROMPT,  model),
    }

    # ---- Phase 2: Run 4 analysis agents in parallel --------------------------------
    print(f"\n[Phase 2] Running 4 analysis sub-agents in parallel ...")
    t_phase2 = time.time()
    results = await asyncio.gather(*(
        run_analysis_agent(name, agent, task_template)
        for name, agent in agents.items()
    ))
    phase2_elapsed = time.time() - t_phase2
    print(f"\n  All 4 completed in {phase2_elapsed:.1f}s total (parallel).")

    ok_count = sum(1 for r in results if r["ok"])
    print(f"  Success: {ok_count}/4")

    # ---- Phase 3: Summarizer (sync) ------------------------------------------------
    print(f"\n[Phase 3] Running Summarizer (sync) ...")
    summarizer = create_deep_agent(
        model=model,
        backend=_SHARED_BACKEND,
        system_prompt=SUMMARIZER_PROMPT,
    )
    t_phase3 = time.time()
    summary_result = summarizer.invoke({
        "messages": [{
            "role": "user",
            "content": (
                "The four analysis reports have been written to the workspace. "
                "Read demo/workspace_02/architect.md, demo/workspace_02/security.md, "
                "demo/workspace_02/dependencies.md, and demo/workspace_02/doc_review.md. "
                "Then merge them into a consolidated demo/workspace_02/code_health_report.md. "
                "最后输出中文摘要。"
            ),
        }],
    })
    phase3_elapsed = time.time() - t_phase3
    print(f"  Summarizer done in {phase3_elapsed:.1f}s")
    final_content = summary_result["messages"][-1].content
    print(f"  Final: {final_content[:200]}")

    # ---- Done ---------------------------------------------------------------------
    print(f"\n{'=' * 60}")
    print("Demo 02 complete!")
    print(f"  Phase 2 (4x parallel): {phase2_elapsed:.1f}s")
    print(f"  Phase 3 (summarizer):  {phase3_elapsed:.1f}s")
    print(f"  Workspace: {WORKSPACE}/")
    print(f"  Agents: architect, security-analyst, dependency-checker, doc-reviewer, summarizer")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(main())
