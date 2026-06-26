"""
Demo 07: Middleware & LangSmith Observability.
Based on Exercise 13 patterns: PerformanceMiddleware (token tracking, timing),
AuditMiddleware (sensitive operation logging), and LangSmith tracing integration.
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
from deepagents.backends import FilesystemBackend
from langchain.chat_models import init_chat_model

WORKSPACE = "demo/workspace_07"


# ---- Performance Middleware ----

@dataclass
class Metrics:
    model_calls: int = 0
    model_time_ms: float = 0.0
    tool_calls: int = 0
    tool_time_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    events: list = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "model_calls": self.model_calls,
            "model_avg_ms": round(self.model_time_ms / max(1, self.model_calls), 1),
            "tool_calls": self.tool_calls,
            "tool_avg_ms": round(self.tool_time_ms / max(1, self.tool_calls), 1),
            "total_tokens_in": self.tokens_in,
            "total_tokens_out": self.tokens_out,
            "total_tokens": self.tokens_in + self.tokens_out,
            "events": self.events,
        }


class PerfMiddleware:
    """Tracks model calls, tool calls, tokens, and timing."""

    def __init__(self, metrics: Metrics):
        self.metrics = metrics

    def wrap_model_call(self, call, *args, **kwargs):
        start = time.perf_counter()
        try:
            result = call(*args, **kwargs)
            dt = (time.perf_counter() - start) * 1000
            usage = getattr(result, "usage_metadata", {}) or {}
            ti = usage.get("input_tokens", 0)
            to = usage.get("output_tokens", 0)
            self.metrics.model_calls += 1
            self.metrics.model_time_ms += dt
            self.metrics.tokens_in += ti
            self.metrics.tokens_out += to
            self.metrics.events.append({"type": "model", "ms": round(dt, 1), "tok_in": ti, "tok_out": to})
            return result
        except Exception as e:
            self.metrics.events.append({"type": "model_error", "error": str(e)})
            raise

    def wrap_tool_call(self, call, tool_name, *args, **kwargs):
        start = time.perf_counter()
        try:
            result = call(*args, **kwargs)
            dt = (time.perf_counter() - start) * 1000
            self.metrics.tool_calls += 1
            self.metrics.tool_time_ms += dt
            self.metrics.events.append({"type": "tool", "name": tool_name, "ms": round(dt, 1)})
            return result
        except Exception as e:
            self.metrics.events.append({"type": "tool_error", "name": tool_name, "error": str(e)})
            raise


# ---- Audit Middleware ----

class AuditLog:
    """Logs sensitive operations for security audit trail."""

    def __init__(self):
        self.entries = []

    def log(self, action: str, detail: str, approved: bool = None):
        self.entries.append({
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "action": action,
            "detail": detail,
            "approved": approved,
        })

    def save(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.entries, f, indent=2, ensure_ascii=False)


# ---- Main ----

def main():
    print("=" * 60)
    print("Demo 07: Middleware & LangSmith Observability")
    print("=" * 60)

    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    # LangSmith
    ls_key = os.environ.get("LANGCHAIN_API_KEY", "")
    if ls_key:
        os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
        os.environ.setdefault("LANGCHAIN_PROJECT", "aperio")
        print("LangSmith: ✅ enabled (project: aperio)")
    else:
        print("LangSmith: ⚠️  not configured (set LANGCHAIN_API_KEY to enable)")

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("ERROR: DEEPSEEK_API_KEY not found")
        return

    model = init_chat_model(
        model="openai:deepseek-v4-flash",
        api_key=api_key,
        base_url="https://api.deepseek.com",
    )

    backend = FilesystemBackend(root_dir=str(_PROJECT_ROOT), virtual_mode=False)
    Path(WORKSPACE).mkdir(parents=True, exist_ok=True)

    # Initialize middleware
    metrics = Metrics()
    perf = PerfMiddleware(metrics)
    audit = AuditLog()

    agent = create_deep_agent(
        model=model,
        backend=backend,
        middleware=[perf],
        system_prompt="You are a helpful assistant. Complete tasks step by step using the tools provided.",
    )

    # Run a task that exercises model + tool calls
    print("\nRunning task (exercising model + tool calls)...")
    audit.log("task_start", "Demo 07 observability test")

    result = agent.invoke({
        "messages": [{
            "role": "user",
            "content": (
                f"Do the following:\n"
                f"1. Write a short analysis of why observability matters for AI agents "
                f"(3 bullet points, Chinese) to {WORKSPACE}/analysis.md\n"
                f"2. Read {WORKSPACE}/analysis.md back\n"
                f"3. Summarize what you wrote in one sentence"
            ),
        }],
    })

    audit.log("task_complete", "Demo 07 finished")

    # Output performance report
    s = metrics.summary()
    print(f"\n{'=' * 40}")
    print(f"📊 Performance Report")
    print(f"{'=' * 40}")
    print(f"  Model Calls:      {s['model_calls']}")
    print(f"  Avg Model Time:   {s['model_avg_ms']}ms")
    print(f"  Tool Calls:       {s['tool_calls']}")
    print(f"  Avg Tool Time:    {s['tool_avg_ms']}ms")
    print(f"  Total Tokens:     {s['total_tokens']} (in:{s['total_tokens_in']} out:{s['total_tokens_out']})")

    # Save reports
    perf_path = f"{WORKSPACE}/performance_report.json"
    with open(perf_path, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)

    audit_path = f"{WORKSPACE}/audit_log.json"
    audit.save(audit_path)

    print(f"\n  Performance report: {perf_path}")
    print(f"  Audit log:          {audit_path}")
    print(f"\n✅ Demo 07 complete!")


if __name__ == "__main__":
    main()
