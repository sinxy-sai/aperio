"""
Demo 08: Long-Term Memory — StoreBackend + Cross-Thread.
Based on Exercise 9 patterns: StoreBackend with /memories/ path,
multi-thread memory sharing, and trend tracking across sessions.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
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

WORKSPACE = str((_PROJECT_ROOT / "demo/workspace_08").resolve())
MEMORIES_DIR = str((_PROJECT_ROOT / "demo/longterm_memory").resolve())


def _build_model():
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY not found")
    return init_chat_model(
        model="openai:deepseek-v4-flash",
        api_key=api_key,
        base_url="https://api.deepseek.com",
    )


def main():
    print("=" * 60)
    print("Demo 08: Long-Term Memory — StoreBackend + Cross-Thread")
    print("=" * 60)

    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    Path(WORKSPACE).mkdir(parents=True, exist_ok=True)
    Path(MEMORIES_DIR).mkdir(parents=True, exist_ok=True)

    model = _build_model()

    # StoreBackend for persistent memory across threads
    store = InMemoryStore()

    backend = CompositeBackend(
        default=FilesystemBackend(root_dir=str(_PROJECT_ROOT), virtual_mode=True),
        routes={
            r"/memories/": StoreBackend(store=store, namespace="aperio"),
        },
    )

    system_prompt = """You are an agent with long-term memory. You can:
- Write preferences to /memories/preferences/<user>_<key>.json
- Write history to /memories/history/<user>_<type>.json
- Read /memories/ to recall past interactions
- Compare current results with historical data for trend analysis"""

    agent = create_deep_agent(
        model=model,
        backend=backend,
        system_prompt=system_prompt,
    )

    user = "demo_user"
    now = datetime.now(timezone.utc).isoformat()

    # ---- Thread A: Write preferences ----
    print("\n--- Thread A: Writing user preferences ---")
    agent.invoke(
        {
            "messages": [{
                "role": "user",
                "content": (
                    f"My user ID is '{user}'. Save my preferences:\n"
                    f"1. Tech stack: Python, FastAPI, React\n"
                    f"2. Risk threshold: High (only show Critical and High)\n"
                    f"3. PRD template: Standard with Acceptance Criteria focus\n"
                    f"Write each to /memories/preferences/{user}_<key>.json"
                ),
            }],
        },
        config={"configurable": {"thread_id": "thread_a"}},
    )

    # ---- Thread B: Store scan history ----
    print("\n--- Thread B: Storing scan history ---")
    agent.invoke(
        {
            "messages": [{
                "role": "user",
                "content": (
                    f"Simulate a code health scan for user '{user}':\n"
                    f"- Date: {now}\n"
                    f"- Files scanned: 47\n"
                    f"- Issues: 9 (2 Critical, 3 High, 3 Medium, 1 Low)\n"
                    f"- Health score: 72/100\n"
                    f"Write to /memories/history/{user}_scan.json\n"
                    f"Also save project context to /memories/context/proj_001.json"
                ),
            }],
        },
        config={"configurable": {"thread_id": "thread_b"}},
    )

    # ---- Thread C: New session — recall + compare ----
    print("\n--- Thread C: New session — recall preferences + trend comparison ---")
    result = agent.invoke(
        {
            "messages": [{
                "role": "user",
                "content": (
                    f"I'm back! My user ID is '{user}'.\n"
                    f"1. Read my preferences from /memories/preferences/\n"
                    f"2. Read previous scan from /memories/history/\n"
                    f"3. Simulate a NEW scan: 5 issues (0 Critical, 1 High, 3 Medium, 1 Low), score 85/100\n"
                    f"4. Compare with previous scan — show what improved and what's new\n"
                    f"5. Apply my risk threshold (only show Critical + High)\n"
                    f"6. Write trend report to {WORKSPACE}/trend_report.md"
                ),
            }],
        },
        config={"configurable": {"thread_id": "thread_c"}},
    )

    final = result["messages"][-1].content if result.get("messages") else "(empty)"
    print(f"\nFinal: {final[:300]}...")
    print(f"\n✅ Demo 08 complete!")
    print(f"   Trend report: {WORKSPACE}/trend_report.md")
    print(f"   Threads: A (write prefs) → B (write history) → C (read + compare)")
    print(f"   Key demo: cross-thread memory sharing, trend tracking, preference application")


if __name__ == "__main__":
    main()
