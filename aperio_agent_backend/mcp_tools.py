from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import get_amap_api_key, get_enable_mcp_tools


@dataclass(frozen=True)
class McpToolset:
    shared: list[Any]
    general_purpose: list[Any]
    errors: list[str]


def load_mcp_tools(run_root: Path) -> McpToolset:
    """Load optional MCP tools for DeepAgents.

    Disabled by default because these tools can access the public network.
    Set APERIO_ENABLE_MCP=1 to enable the local web-search MCP server. Set
    AMAP_API_KEY to additionally enable Amap MCP tools.
    """
    try:
        from langchain_core.tools import StructuredTool
    except Exception as exc:
        return McpToolset(shared=[], general_purpose=[], errors=[f"local tool dependencies are not installed: {exc}"])

    errors: list[str] = []
    shared_tools: list[Any] = []
    general_tools: list[Any] = _local_tools(StructuredTool)

    if not get_enable_mcp_tools():
        return McpToolset(shared=shared_tools, general_purpose=general_tools, errors=errors)

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except Exception as exc:
        return McpToolset(shared=shared_tools, general_purpose=general_tools, errors=[f"MCP dependencies are not installed: {exc}"])

    server_path = Path(__file__).resolve().parent / "mcp_web_search_server.py"
    try:
        web_search_client = MultiServerMCPClient(
            {
                "web_search": {
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [str(server_path)],
                    "cwd": str(Path.cwd()),
                    "env": {
                        **os.environ,
                        "APERIO_OUTPUTS_DIR": str((run_root / "outputs").resolve()),
                        "FASTMCP_LOG_LEVEL": "ERROR",
                        "PYTHONIOENCODING": "utf-8",
                    },
                },
            },
            handle_tool_errors=True,
        )
        shared_tools = [_sync_tool_for_invoke(tool, StructuredTool) for tool in asyncio.run(web_search_client.get_tools())]
    except Exception as exc:
        errors.append(f"web-search MCP unavailable: {exc}")

    amap_key = get_amap_api_key()
    if amap_key:
        try:
            amap_client = MultiServerMCPClient(
                {
                    "amap": {
                        "url": f"https://mcp.amap.com/sse?key={amap_key}",
                        "transport": "sse",
                    },
                },
                handle_tool_errors=True,
            )
            general_tools.extend(_sync_tool_for_invoke(tool, StructuredTool) for tool in asyncio.run(amap_client.get_tools()))
        except Exception as exc:
            errors.append(f"Amap MCP unavailable: {exc}")

    return McpToolset(shared=shared_tools, general_purpose=general_tools, errors=errors)


def _local_tools(structured_tool_type: type) -> list[Any]:
    from .local_knowledge import search_project_knowledge
    from .safe_execution import run_safe_command

    def search_project_knowledge_tool(query: str, limit: int = 5) -> dict[str, Any]:
        """Search local project docs, README files, and optimization notes."""
        hits = search_project_knowledge(query, limit=limit)
        return {
            "query": query,
            "hits": [
                {
                    "path": hit.path,
                    "title": hit.title,
                    "content": hit.content[:1200],
                    "score": hit.score,
                }
                for hit in hits
            ],
        }

    def safe_run_command_tool(command: str, cwd: str = "", timeout_seconds: int = 20) -> dict[str, Any]:
        """Run a read-only allowlisted command inside the project workspace."""
        try:
            return run_safe_command(command, cwd=cwd or None, timeout_seconds=timeout_seconds).to_dict()
        except ValueError as exc:
            return {
                "ok": False,
                "exit_code": None,
                "command": command,
                "cwd": cwd,
                "stdout": "",
                "stderr": "",
                "reason": str(exc),
            }

    return [
        structured_tool_type.from_function(
            func=search_project_knowledge_tool,
            name="search_project_knowledge",
            description="Search local project knowledge indexed from README/docs/optimization notes. Use before answering project-specific questions.",
        ),
        structured_tool_type.from_function(
            func=safe_run_command_tool,
            name="safe_run_command",
            description="Run a conservative read-only allowlisted command in the project workspace. Shell metacharacters and non-allowlisted commands are rejected.",
        ),
    ]


def _sync_tool_for_invoke(tool: Any, structured_tool_type: type) -> Any:
    """Wrap async MCP StructuredTools so sync DeepAgents graphs can invoke them."""
    if not isinstance(tool, structured_tool_type) or getattr(tool, "func", None) is not None:
        return tool

    def invoke_async_tool(**kwargs: Any) -> Any:
        return asyncio.run(tool.ainvoke(kwargs))

    return structured_tool_type.from_function(
        func=invoke_async_tool,
        coroutine=tool.coroutine,
        name=tool.name,
        description=tool.description,
        args_schema=tool.args_schema,
    )
