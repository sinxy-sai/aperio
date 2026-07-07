from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware import AgentMiddleware


@dataclass
class RunTelemetry:
    model_calls: int = 0
    model_time_ms: float = 0.0
    tool_calls: int = 0
    tool_time_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    by_agent: dict[str, dict[str, int]] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_calls": self.model_calls,
            "model_avg_ms": round(self.model_time_ms / max(1, self.model_calls), 1),
            "tool_calls": self.tool_calls,
            "tool_avg_ms": round(self.tool_time_ms / max(1, self.tool_calls), 1),
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "total_tokens": self.tokens_in + self.tokens_out,
            "by_agent": self.by_agent,
            "events": self.events[-300:],
        }

    def record_agent(self, agent_name: str, kind: str) -> None:
        bucket = self.by_agent.setdefault(agent_name, {"model": 0, "tool": 0})
        bucket[kind] = bucket.get(kind, 0) + 1


class TelemetryMiddleware(AgentMiddleware):
    """Collect lightweight model/tool timing for a single Aperio run."""

    def __init__(self, telemetry: RunTelemetry, agent_name: str, event_callback: Any | None = None) -> None:
        super().__init__()
        self.telemetry = telemetry
        self.agent_name = agent_name
        self.event_callback = event_callback

    def wrap_model_call(self, request, handler):
        started = time.perf_counter()
        try:
            response = handler(request)
        except Exception as exc:
            self._record_model(time.perf_counter() - started, error=type(exc).__name__)
            raise
        self._record_model(time.perf_counter() - started, response=response)
        return response

    async def awrap_model_call(self, request, handler):
        started = time.perf_counter()
        try:
            response = await handler(request)
        except Exception as exc:
            self._record_model(time.perf_counter() - started, error=type(exc).__name__)
            raise
        self._record_model(time.perf_counter() - started, response=response)
        return response

    def wrap_tool_call(self, request, handler):
        started = time.perf_counter()
        tool_name = _tool_name(request)
        try:
            response = handler(request)
        except Exception as exc:
            self._record_tool(tool_name, time.perf_counter() - started, error=type(exc).__name__)
            raise
        self._record_tool(tool_name, time.perf_counter() - started)
        return response

    async def awrap_tool_call(self, request, handler):
        started = time.perf_counter()
        tool_name = _tool_name(request)
        try:
            response = await handler(request)
        except Exception as exc:
            self._record_tool(tool_name, time.perf_counter() - started, error=type(exc).__name__)
            raise
        self._record_tool(tool_name, time.perf_counter() - started)
        return response

    def _record_model(self, elapsed: float, response: Any | None = None, error: str = "") -> None:
        self.telemetry.model_calls += 1
        self.telemetry.model_time_ms += elapsed * 1000
        self.telemetry.record_agent(self.agent_name, "model")
        usage = _usage_metadata(response)
        self.telemetry.tokens_in += usage.get("input_tokens", 0)
        self.telemetry.tokens_out += usage.get("output_tokens", 0)
        event = {
            "type": "model",
            "agent": self.agent_name,
            "elapsed_ms": round(elapsed * 1000, 1),
        }
        if usage:
            event["tokens"] = usage
        if error:
            event["error"] = error
        self.telemetry.events.append(event)
        _emit_event(self.event_callback, event)

    def _record_tool(self, tool_name: str, elapsed: float, error: str = "") -> None:
        self.telemetry.tool_calls += 1
        self.telemetry.tool_time_ms += elapsed * 1000
        self.telemetry.record_agent(self.agent_name, "tool")
        event = {
            "type": "tool",
            "agent": self.agent_name,
            "tool": tool_name,
            "elapsed_ms": round(elapsed * 1000, 1),
        }
        if error:
            event["error"] = error
        self.telemetry.events.append(event)
        _emit_event(self.event_callback, event)


def _tool_name(request: Any) -> str:
    tool = getattr(request, "tool", None)
    if tool is not None:
        return str(getattr(tool, "name", "unknown"))
    call = getattr(request, "tool_call", {}) or {}
    return str(call.get("name") or "unknown")


def _emit_event(event_callback: Any | None, event: dict[str, Any]) -> None:
    if event_callback is None:
        return
    try:
        event_callback(event)
    except Exception:
        return


def _usage_metadata(response: Any | None) -> dict[str, int]:
    usage = getattr(response, "usage_metadata", None)
    if not isinstance(usage, dict):
        content = getattr(response, "content", None)
        usage = getattr(content, "usage_metadata", None)
    if not isinstance(usage, dict):
        return {}
    return {
        "input_tokens": int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or usage.get("completion_tokens") or 0),
    }
