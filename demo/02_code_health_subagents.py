"""
Demo 02: Code Health Multi-Subagent Orchestration.

Pipeline:
  Orchestrator (sync) spawns 4 analysis sub-agents in parallel,
  then Summarizer merges results into a consolidated report.

Sub-agents: architect, security-analyst, dependency-checker, doc-reviewer, summarizer
Workspace: demo/workspace_02/
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

WORKSPACE = str((_PROJECT_ROOT / "demo/workspace_02").resolve())
TARGET_CODE_REL = "full-stack-fastapi-template-master/backend/app/core"
TARGET_CODE_ABS = str((_PROJECT_ROOT / TARGET_CODE_REL).resolve())


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

ARCHITECT_PROMPT = f"""\
You are a **code architect**. Analyze the given codebase for architectural patterns.

Focus on:
1. Module structure and organization — how are files/directories laid out?
2. Design patterns in use (MVC, repository, dependency injection, etc.)
3. Coupling and cohesion — are modules well-separated or tangled?
4. Code layering — is there clear separation between API, business logic, and data layers?
5. Strengths and weaknesses of the current architecture

Write your findings to {WORKSPACE}/architect.md using the write_file tool,
then output a brief summary as your final message. Use Chinese for your analysis.
"""

SECURITY_PROMPT = f"""\
You are a **security analyst**. Audit the given codebase for security issues.

Focus on:
1. Authentication and authorization — are there any gaps?
2. Input validation — is user input properly sanitized?
3. Secret management — are API keys, passwords, tokens handled safely?
4. SQL injection, XSS, CSRF risks
5. Unsafe deserialization, path traversal, or other OWASP Top-10 concerns

Write your findings to {WORKSPACE}/security.md using the write_file tool,
then output a brief summary as your final message. Use Chinese for your analysis.
"""

DEPENDENCY_PROMPT = f"""\
You are a **dependency checker**. Review the codebase's dependencies and imports.

Focus on:
1. External dependencies — what third-party packages are used? Are versions pinned?
2. Import graph — are there circular imports? Unused imports?
3. Dependency freshness — are any packages outdated or unmaintained?
4. Direct vs transitive — are dependencies explicitly declared?
5. Potential version conflicts or security advisories

Write your findings to {WORKSPACE}/dependencies.md using the write_file tool,
then output a brief summary as your final message. Use Chinese for your analysis.
"""

DOC_REVIEWER_PROMPT = f"""\
You are a **documentation reviewer**. Evaluate the codebase's documentation quality.

Focus on:
1. Module/class/function docstrings — are they present and useful?
2. Inline comments — do they explain *why*, not just *what*?
3. README / top-level docs — is there an onboarding guide?
4. API documentation — are endpoints, parameters, and responses documented?
5. Areas where documentation is missing or could be improved

Write your findings to {WORKSPACE}/doc_review.md using the write_file tool,
then output a brief summary as your final message. Use Chinese for your analysis.
"""

SUMMARIZER_PROMPT = f"""\
You are a **technical report summarizer**. You will be given four analysis reports
(architecture, security, dependencies, documentation). Merge them into one
consolidated code health report.

Do the following:
1. Read the four analysis files:
   - {WORKSPACE}/architect.md
   - {WORKSPACE}/security.md
   - {WORKSPACE}/dependencies.md
   - {WORKSPACE}/doc_review.md
2. Synthesize key findings from each into a coherent report.
3. Prioritize issues by severity (critical / warning / info).
4. Write the final consolidated report to {WORKSPACE}/code_health_report.md.

Use Chinese for the report. Include an executive summary at the top."""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    print("=" * 60)
    print("Demo 02: Code Health Multi-Subagent Orchestration")
    print("=" * 60)

    model = _build_model()

    # Verify target exists
    target_path = Path(TARGET_CODE_ABS)
    if not target_path.exists():
        print(f"WARNING: target code path '{TARGET_CODE_REL}' not found.")
    else:
        py_files = list(target_path.glob("**/*.py"))
        print(f"Target: {TARGET_CODE_REL}  ({len(py_files)} .py files found)")

    # Ensure workspace exists
    Path(WORKSPACE).mkdir(parents=True, exist_ok=True)

    backend = FilesystemBackend(
        root_dir=str(_PROJECT_ROOT.resolve()),
        virtual_mode=False,
    )

    agent = create_deep_agent(
        model=model,
        backend=backend,
        system_prompt=f"""You are a code health orchestrator. Your job:

1. Use write_todos to plan: 4 parallel analysis sub-agents → summarizer.
2. Spawn ALL 4 analysis sub-agents IN PARALLEL to analyze the code at `{TARGET_CODE_REL}`.
   Each sub-agent knows its own focus area and will write its report to {WORKSPACE}/.
3. Wait for ALL 4 to finish, then spawn the summarizer to merge results.
4. Tell the summarizer to write the final report to {WORKSPACE}/code_health_report.md.

IMPORTANT: The 4 analysis sub-agents MUST run in parallel, not sequentially.""",
        subagents=[
            {
                "name": "architect",
                "description": "Analyze code architecture: module structure, coupling, layering, design patterns",
                "system_prompt": ARCHITECT_PROMPT,
            },
            {
                "name": "security-analyst",
                "description": "Audit code security: injection, secrets, deserialization, OWASP Top-10",
                "system_prompt": SECURITY_PROMPT,
            },
            {
                "name": "dependency-checker",
                "description": "Check dependencies: versions, CVEs, license compatibility, imports",
                "system_prompt": DEPENDENCY_PROMPT,
            },
            {
                "name": "doc-reviewer",
                "description": "Review documentation: README, docstrings, comments, API docs",
                "system_prompt": DOC_REVIEWER_PROMPT,
            },
            {
                "name": "summarizer",
                "description": "Merge all 4 analysis reports into a consolidated code health report",
                "system_prompt": SUMMARIZER_PROMPT,
            },
        ],
    )

    t0 = time.time()
    result = agent.invoke({
        "messages": [{
            "role": "user",
            "content": (
                f"Run the full code health pipeline on `{TARGET_CODE_REL}`:\\n\\n"
                f"1. Spawn 4 sub-agents IN PARALLEL: architect, security-analyst, "
                f"dependency-checker, doc-reviewer\\n"
                f"2. After all 4 complete, spawn the summarizer to produce "
                f"{WORKSPACE}/code_health_report.md\\n"
            ),
        }],
    })
    elapsed = time.time() - t0

    final = result["messages"][-1].content if result.get("messages") else "(empty)"
    print(f"\\n✅ Demo 02 complete in {elapsed:.1f}s!")
    print(f"   Agents: architect, security-analyst, dependency-checker, doc-reviewer, summarizer")
    print(f"   Workspace: {WORKSPACE}/")
    print(f"   Final report: {WORKSPACE}/code_health_report.md")


if __name__ == "__main__":
    main()
