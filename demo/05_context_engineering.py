"""
Demo 05: Context Engineering — Write / Select / Compress / Isolate.
Demonstrates all four pillars using the code health pipeline.

Pillars:
  Write    → Sub-agents write findings to filesystem, not keeping all in context
  Select   → Summarizer uses ls to discover then read_file to selectively read
  Compress → Each sub-agent outputs compact JSON, not full conversation history
  Isolate  → Each sub-agent has only its own skill, context is independent
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

_DEMO_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _DEMO_DIR.parent

from dotenv import load_dotenv
load_dotenv(_DEMO_DIR / ".env")

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain.chat_models import init_chat_model

WORKSPACE = str((_PROJECT_ROOT / "demo/workspace_05").resolve())


def _load_skill(name: str) -> str:
    """Progressive disclosure: load a skill on demand (Select pattern)."""
    path = _DEMO_DIR / "04_skills" / name / "SKILL.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return f"# {name}\n(Skill file not found — using default prompt)"


def _build_model():
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY not found in demo/.env")
    return init_chat_model(
        model="openai:deepseek-v4-flash",
        api_key=api_key,
        base_url="https://api.deepseek.com",
    )


async def _run_agent(name: str, agent, task: str) -> dict:
    """Run an agent and return compressed result (Compress pattern)."""
    print(f"  [{name}] starting ...")
    t0 = time.time()
    try:
        result = await agent.ainvoke({"messages": [{"role": "user", "content": task}]})
        elapsed = time.time() - t0
        content = result["messages"][-1].content if result.get("messages") else "(empty)"
        # COMPRESS: return structured dict, not full conversation
        compressed = {
            "agent": name,
            "elapsed_s": round(elapsed, 1),
            "ok": True,
            "summary": content[:200],
            "output_length": len(content),
        }
        print(f"  [{name}] done in {elapsed:.1f}s")
        return compressed
    except Exception as exc:
        elapsed = time.time() - t0
        print(f"  [{name}] FAILED in {elapsed:.1f}s: {exc}")
        return {"agent": name, "elapsed_s": round(elapsed, 1), "ok": False, "error": str(exc)}


async def main():
    print("=" * 60)
    print("Demo 05: Context Engineering — Write / Select / Compress / Isolate")
    print("=" * 60)

    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    Path(WORKSPACE).mkdir(parents=True, exist_ok=True)
    Path(WORKSPACE, "drafts").mkdir(exist_ok=True)

    model = _build_model()
    backend = FilesystemBackend(root_dir=str(_PROJECT_ROOT), virtual_mode=True)

    # ISOLATE: each sub-agent loads ONLY its own skill
    architect_skill = _load_skill("code-health/code-architect")
    security_skill = _load_skill("code-health/code-security")
    report_skill = _load_skill("general/report-writing")

    # Main orchestrator has the report-writing skill + context engineering instructions
    orchestrator_prompt = f"""{report_skill}

You are demonstrating context engineering. Your job:

1. WRITE: Spawn 2 sub-agents who write findings to files
2. SELECT: After they finish, use ls to discover what they wrote
3. COMPRESS: Verify each output is compact (JSON-like structure)
4. ISOLATE: Confirm no sub-agent knows what the other analyzed

After the sub-agents complete, write a report to {WORKSPACE}/context_engineering_report.md
explaining how each pillar was demonstrated."""

    agent = create_deep_agent(
        model=model,
        backend=backend,
        system_prompt=orchestrator_prompt,
        subagents=[
            {
                "name": "architect",
                "description": "Architecture analysis (ISOLATE: only has architect skill)",
                "system_prompt": architect_skill,
            },
            {
                "name": "security-analyst",
                "description": "Security scanning (ISOLATE: only has security skill)",
                "system_prompt": security_skill,
            },
        ],
    )

    # Target code
    target = "full-stack-fastapi-template-master/backend/app/core"

    result = agent.invoke({
        "messages": [{
            "role": "user",
            "content": (
                f"Demonstrate all four context engineering pillars:\n\n"
                f"Step 1 (WRITE): Have architect and security-analyst analyze `{target}`.\n"
                f"Each MUST write findings to {WORKSPACE}/drafts/<name>.json with format:\n"
                f'{{"agent": "<name>", "pillar": "Write", "files_scanned": N, "findings": [...]}}\n\n'
                f"Step 2 (SELECT): Use ls to discover files in {WORKSPACE}/drafts/,\n"
                f"then read_file only the ones you need for merging.\n\n"
                f"Step 3 (COMPRESS): Verify each output is compact JSON (not full conversation).\n"
                f"Note the token savings vs passing full agent histories.\n\n"
                f"Step 4 (ISOLATE): Confirm architect did NOT scan for security issues\n"
                f"and security-analyst did NOT analyze architecture. Each was isolated.\n\n"
                f"Write final report to {WORKSPACE}/context_engineering_report.md\n"
                f"with a section for each pillar explaining what was demonstrated."
            ),
        }],
    })

    final = result["messages"][-1].content if result.get("messages") else "(empty)"
    print(f"\n✅ Demo 05 complete!")
    print(f"   Report: {WORKSPACE}/context_engineering_report.md")
    print(f"   Drafts: {WORKSPACE}/drafts/")
    print(f"   Pillars demonstrated: Write, Select, Compress, Isolate")


if __name__ == "__main__":
    asyncio.run(main())
