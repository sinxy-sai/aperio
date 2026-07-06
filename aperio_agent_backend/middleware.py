from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage, ToolMessage


class ToolAllowlistMiddleware(AgentMiddleware):
    """Hide and block tools outside a role-specific allowlist."""

    def __init__(self, allowed_tools: set[str], label: str) -> None:
        super().__init__()
        self.allowed_tools = allowed_tools
        self.label = label

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(request.override(tools=self._filtered_tools(request)))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return await handler(request.override(tools=self._filtered_tools(request)))

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        tool_name = _request_tool_name(request)
        if tool_name in self.allowed_tools:
            return handler(request)
        return _blocked_tool_message(
            request,
            f"{self.label}: tool `{tool_name}` is not allowed for this agent role.",
        )

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        tool_name = _request_tool_name(request)
        if tool_name in self.allowed_tools:
            return await handler(request)
        return _blocked_tool_message(
            request,
            f"{self.label}: tool `{tool_name}` is not allowed for this agent role.",
        )

    def _filtered_tools(self, request: Any) -> list[Any]:
        return [
            tool
            for tool in getattr(request, "tools", [])
            if _declared_tool_name(tool) in self.allowed_tools
        ]


class FinalOutputGuardMiddleware(AgentMiddleware):
    """Stop follow-up tool calls after declared final artifacts are written."""

    def __init__(self, final_paths: set[str], label: str, completed_paths: set[str] | None = None) -> None:
        super().__init__()
        self.final_paths = {_normalize_virtual_path(path) for path in final_paths}
        self.label = label
        self.completed_paths = completed_paths if completed_paths is not None else set()

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        if self._is_complete():
            return _blocked_tool_message(
                request,
                f"{self.label}: final output was already written; stop without extra tool calls.",
            )

        result = handler(request)
        self._record_final_write(request)
        return result

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        if self._is_complete():
            return _blocked_tool_message(
                request,
                f"{self.label}: final output was already written; stop without extra tool calls.",
            )

        result = await handler(request)
        self._record_final_write(request)
        return result

    def _is_complete(self) -> bool:
        return bool(self.final_paths) and self.final_paths.issubset(self.completed_paths)

    def _record_final_write(self, request: Any) -> None:
        if _request_tool_name(request) != "write_file":
            return
        path = _normalize_virtual_path(_write_file_path(_request_args(request)))
        if path in self.final_paths:
            self.completed_paths.add(path)


class RouterToolGuardMiddleware(AgentMiddleware):
    """Keep the main router as a pure delegator."""

    allowed_tools = {"task"}

    def __init__(self, router_prompt: str) -> None:
        super().__init__()
        self.router_prompt = router_prompt

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(
            request.override(
                tools=self._filtered_tools(request),
                system_message=SystemMessage(content=self.router_prompt),
            )
        )

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return await handler(
            request.override(
                tools=self._filtered_tools(request),
                system_message=SystemMessage(content=self.router_prompt),
            )
        )

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        tool_name = _request_tool_name(request)
        if tool_name in self.allowed_tools:
            return handler(request)
        return _blocked_tool_message(
            request,
            "Main Router is a pure routing agent. Direct tool use is blocked here; delegate to a registered agent with the task tool.",
        )

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        tool_name = _request_tool_name(request)
        if tool_name in self.allowed_tools:
            return await handler(request)
        return _blocked_tool_message(
            request,
            "Main Router is a pure routing agent. Direct tool use is blocked here; delegate to a registered agent with the task tool.",
        )

    def _filtered_tools(self, request: Any) -> list[Any]:
        return [
            tool
            for tool in getattr(request, "tools", [])
            if _declared_tool_name(tool) in self.allowed_tools
        ]


def _declared_tool_name(tool: Any) -> str:
    if isinstance(tool, dict):
        function = tool.get("function")
        if isinstance(function, dict) and function.get("name"):
            return str(function["name"])
        if tool.get("name"):
            return str(tool["name"])
        return ""
    return str(getattr(tool, "name", ""))


def _request_tool_name(request: Any) -> str:
    call = getattr(request, "tool_call", {}) or {}
    return str(call.get("name") or "")


def _request_args(request: Any) -> dict[str, Any]:
    call = getattr(request, "tool_call", {}) or {}
    args = call.get("args", {})
    return args if isinstance(args, dict) else {}


def _blocked_tool_message(request: Any, content: str) -> ToolMessage:
    call = getattr(request, "tool_call", {}) or {}
    tool_name = str(call.get("name") or "unknown")
    tool_call_id = str(call.get("id") or "")
    return ToolMessage(
        content=content,
        tool_call_id=tool_call_id,
        name=tool_name,
        status="error",
    )


def _write_file_path(args: dict[str, Any]) -> str:
    return str(
        args.get("file_path")
        or args.get("path")
        or args.get("filepath")
        or args.get("file")
        or args.get("filename")
        or args.get("name")
        or ""
    ).replace("\\", "/")


def _normalize_virtual_path(path: str) -> str:
    return "/" + path.replace("\\", "/").strip("/")
