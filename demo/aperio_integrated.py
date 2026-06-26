"""
Aperio Integrated Demo — assembles all 7 technical modules into one pipeline.

Modules demonstrated:
  M1: Multi-subagent collaboration — 4 parallel code health sub-agents + summarizer
  M2: Skill system — all sub-agents load SKILL.md from demo/04_skills/
  M3: Context engineering — Write/Select/Compress/Isolate
  M4: Long-term memory — StoreBackend /memories/ for trend tracking
  M5: Security — virtual_mode=True filesystem isolation
  M6: Observability — PerformanceMiddleware + LangSmith (if configured)
  M7: Output — consolidated report in Markdown

Usage:
  conda activate llm-dev
  python demo/aperio_integrated.py
"""
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

_DEMO_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _DEMO_DIR.parent

from dotenv import load_dotenv
load_dotenv(_DEMO_DIR / ".env")

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend, CompositeBackend
from deepagents.backends.store import StoreBackend
from langgraph.store.memory import InMemoryStore
from langchain.chat_models import init_chat_model

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
WORKSPACE = str((_PROJECT_ROOT / "demo/workspace_integrated").resolve())
TARGET_CODE = "full-stack-fastapi-template-master/backend/app/core"
SKILLS_ROOT = _DEMO_DIR / "04_skills"


def _load_skill(name: str) -> str:
    """Load a SKILL.md — progressive disclosure."""
    path = SKILLS_ROOT / name / "SKILL.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return f"# {name}\n(Skill not found at {path})"


# ---------------------------------------------------------------------------
# Middleware (M6: Observability)
# ---------------------------------------------------------------------------

@dataclass
class Metrics:
    model_calls: int = 0
    model_time_ms: float = 0.0
    tool_calls: int = 0
    tool_time_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    events: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "model_calls": self.model_calls,
            "model_avg_ms": round(self.model_time_ms / max(1, self.model_calls), 1),
            "tool_calls": self.tool_calls,
            "tool_avg_ms": round(self.tool_time_ms / max(1, self.tool_calls), 1),
            "total_tokens": self.tokens_in + self.tokens_out,
            "events": self.events,
        }


class PerfMiddleware:
    def __init__(self, m: Metrics):
        self.m = m

    def wrap_model_call(self, call, *args, **kwargs):
        t0 = time.perf_counter()
        try:
            result = call(*args, **kwargs)
            dt = (time.perf_counter() - t0) * 1000
            usage = getattr(result, "usage_metadata", {}) or {}
            self.m.model_calls += 1
            self.m.model_time_ms += dt
            self.m.tokens_in += usage.get("input_tokens", 0)
            self.m.tokens_out += usage.get("output_tokens", 0)
            self.m.events.append({"type": "model", "ms": round(dt, 1)})
            return result
        except Exception:
            raise

    def wrap_tool_call(self, call, tool_name, *args, **kwargs):
        t0 = time.perf_counter()
        try:
            result = call(*args, **kwargs)
            dt = (time.perf_counter() - t0) * 1000
            self.m.tool_calls += 1
            self.m.tool_time_ms += dt
            self.m.events.append({"type": "tool", "name": tool_name, "ms": round(dt, 1)})
            return result
        except Exception:
            raise


# ---------------------------------------------------------------------------
# Main Pipeline
# ---------------------------------------------------------------------------

def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    # ---- LangSmith (M6) ----
    ls_key = os.environ.get("LANGSMITH_API_KEY", "")
    if ls_key:
        os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
        os.environ.setdefault("LANGCHAIN_PROJECT", "aperio-integrated")
        print(f"LangSmith: ✅ enabled (project: aperio-integrated)")
    else:
        print("LangSmith: ⚠️  not configured — set LANGSMITH_API_KEY to enable")

    print("=" * 60)
    print("Aperio Integrated Demo")
    print("=" * 60)

    # ---- Validate ----
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("ERROR: DEEPSEEK_API_KEY not found in demo/.env")
        return

    target_path = _PROJECT_ROOT / TARGET_CODE
    if not target_path.exists():
        print(f"WARNING: target '{TARGET_CODE}' not found")
    else:
        print(f"Target: {TARGET_CODE}  ({len(list(target_path.glob('**/*.py')))} .py files)")

    Path(WORKSPACE).mkdir(parents=True, exist_ok=True)
    Path(WORKSPACE, "drafts").mkdir(exist_ok=True)

    # ---- Build backend (M4 + M5) ----
    store = InMemoryStore()
    backend = CompositeBackend(
        default=FilesystemBackend(root_dir=str(_PROJECT_ROOT), virtual_mode=True),
        routes={
            r"/memories/": StoreBackend(store=store, namespace=lambda rt: ("aperio",)),
        },
    )
    print("Backend: CompositeBackend (FS + StoreBackend /memories/)")

    # ---- Load skills (M2) ----
    skills = {
        "architect": _load_skill("code-health/code-architect"),
        "security": _load_skill("code-health/code-security"),
        "dependency": _load_skill("code-health/code-dependency"),
        "documentation": _load_skill("code-health/code-documentation"),
        "report-writing": _load_skill("general/report-writing"),
    }
    loaded = [k for k, v in skills.items() if not v.startswith("#")]
    print(f"Skills: {len(loaded)}/{len(skills)} loaded ({', '.join(loaded)})")

    # ---- Build model ----
    model = init_chat_model(
        model="openai:deepseek-v4-flash",
        api_key=api_key,
        base_url="https://api.deepseek.com",
    )

    # ---- Middleware (M6) ----
    metrics = Metrics()
    perf = PerfMiddleware(metrics)

    # ---- Orchestrator ----
    agent = create_deep_agent(
        model=model,
        backend=backend,
        middleware=[perf],
        system_prompt=f"""{skills['report-writing']}

You are the Aperio code health orchestrator. Your job:

1. Use write_todos to plan: 4 parallel analysis → summarizer
2. Spawn ALL 4 sub-agents IN PARALLEL:
   - architect — code architecture analysis
   - security-analyst — security vulnerability audit
   - dependency-checker — dependency health check
   - doc-reviewer — documentation quality review
3. Each sub-agent analyzes `{TARGET_CODE}` and writes findings to {WORKSPACE}/drafts/
4. After all 4 complete, spawn the summarizer to merge results
5. Summarizer saves the final report to {WORKSPACE}/code_health_report.md

CONTEXT ENGINEERING (M3):
- WRITE: all findings go to filesystem, not kept in conversation
- SELECT: summarizer uses ls to discover drafts, then reads only what it needs
- COMPRESS: each sub-agent outputs structured findings with severity ratings
- ISOLATE: each sub-agent has only its own skill, context is independent

LONG-TERM MEMORY (M4):
- Read previous scan from /memories/history/ to get trend data (if exists)
- After the report, save a summary to /memories/history/latest_scan.json""",
        subagents=[
            {
                "name": "architect",
                "description": "Analyze code architecture: module structure, coupling, layering",
                "system_prompt": skills["architect"],
            },
            {
                "name": "security-analyst",
                "description": "Audit code security: vulnerabilities, OWASP Top-10, secrets",
                "system_prompt": skills["security"],
            },
            {
                "name": "dependency-checker",
                "description": "Check dependencies: versions, CVEs, license compatibility",
                "system_prompt": skills["dependency"],
            },
            {
                "name": "doc-reviewer",
                "description": "Review documentation: README, docstrings, API docs",
                "system_prompt": skills["documentation"],
            },
            {
                "name": "summarizer",
                "description": "Merge all 4 analysis reports into a consolidated health report",
                "system_prompt": skills["report-writing"],
            },
        ],
    )

    # ---- Run ----
    print(f"\nRunning code health pipeline ...")
    print(f"  Stage 1: 4 parallel sub-agents (architect, security, dependency, documentation)")
    print(f"  Stage 2: summarizer merges findings\n")

    t0 = time.time()
    result = agent.invoke({
        "messages": [{
            "role": "user",
            "content": (
                f"Run the full code health pipeline on `{TARGET_CODE}`.\n\n"
                f"1. Spawn 4 sub-agents IN PARALLEL: architect, security-analyst, "
                f"dependency-checker, doc-reviewer\n"
                f"   Each writes findings to {WORKSPACE}/drafts/<name>.md\n\n"
                f"2. After all 4 complete, spawn the summarizer to:\n"
                f"   - ls {WORKSPACE}/drafts/ to discover reports\n"
                f"   - read each one\n"
                f"   - merge into {WORKSPACE}/code_health_report.md\n\n"
                f"3. Check /memories/history/latest_scan.json for previous results\n"
                f"   If it exists, include trend comparison in the report\n"
                f"4. Save this scan's summary to /memories/history/latest_scan.json\n"
            ),
        }],
    })
    elapsed = time.time() - t0

    # ---- Output ----
    final = result["messages"][-1].content if result.get("messages") else "(empty)"
    print(f"\n{'=' * 60}")

    # Performance report (M6)
    m = metrics.to_dict()
    print(f"📊 Performance:")
    print(f"   Model calls: {m['model_calls']}  |  Avg: {m['model_avg_ms']}ms")
    print(f"   Tool calls:  {m['tool_calls']}  |  Avg: {m['tool_avg_ms']}ms")
    print(f"   Total tokens: {m['total_tokens']}")
    print(f"   Wall time: {elapsed:.1f}s")

    # Save perf report
    perf_path = Path(WORKSPACE) / "performance.json"
    perf_path.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")

    # Show output files
    print(f"\n📁 Workspace: {WORKSPACE}/")
    for f in sorted(Path(WORKSPACE).rglob("*.md")):
        size = f.stat().st_size
        print(f"   {f.relative_to(WORKSPACE)} ({size} bytes)")
    for f in sorted(Path(WORKSPACE).rglob("*.json")):
        print(f"   {f.relative_to(WORKSPACE)} ({f.stat().st_size} bytes)")

    # Technical module coverage
    print(f"\n📋 Module Coverage:")
    print(f"   M1 ✅ Multi-subagent: 4 parallel + summarizer")
    print(f"   M2 ✅ Skills: {' + '.join(loaded)}")
    print(f"   M3 ✅ Context: Write → Select → Compress → Isolate")
    print(f"   M4 ✅ Memory: StoreBackend /memories/")
    print(f"   M5 ✅ Security: virtual_mode=True isolation")
    print(f"   M6 ✅ Observability: PerfMiddleware {'+ LangSmith' if ls_key else '(LangSmith latent)'}")
    print(f"   M7 ✅ Output: {WORKSPACE}/code_health_report.md")

    print(f"\n✅ Aperio Integrated Demo complete in {elapsed:.1f}s!")


if __name__ == "__main__":
    main()
