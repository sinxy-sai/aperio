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
    if not get_enable_mcp_tools():
        return McpToolset(shared=[], general_purpose=[], errors=[])

    try:
        from langchain_core.tools import StructuredTool
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except Exception as exc:
        return McpToolset(shared=[], general_purpose=[], errors=[f"MCP dependencies are not installed: {exc}"])

    errors: list[str] = []
    shared_tools: list[Any] = []
    general_tools: list[Any] = []

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
            general_tools = [_sync_tool_for_invoke(tool, StructuredTool) for tool in asyncio.run(amap_client.get_tools())]
        except Exception as exc:
            errors.append(f"Amap MCP unavailable: {exc}")

    return McpToolset(shared=shared_tools, general_purpose=general_tools, errors=errors)


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
