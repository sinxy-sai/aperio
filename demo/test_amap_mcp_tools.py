"""Smoke-test Amap MCP tools used by aperio_integrated.py.

This script runs on the host side, the same place where MCP tools are loaded
by the agent. These Amap tools are not Docker sandbox tools.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient


DEMO_DIR = Path(__file__).resolve().parent


def _content_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or item))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(value)


async def _load_amap_tools():
    load_dotenv(DEMO_DIR / ".env")
    amap_key = os.environ.get("AMAP_API_KEY", "").strip()
    if not amap_key:
        raise SystemExit("AMAP_API_KEY is not configured in demo/.env")

    client = MultiServerMCPClient(
        {
            "amap": {
                "url": f"https://mcp.amap.com/sse?key={amap_key}",
                "transport": "sse",
            },
        },
        handle_tool_errors=True,
    )
    tools = await client.get_tools()
    return {getattr(tool, "name", ""): tool for tool in tools}


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Smoke-test Amap MCP tools.")
    parser.add_argument("--ip", default="", help="Public IP for maps_ip_location. Empty usually returns no city.")
    parser.add_argument("--city", default="110000", help="City name or adcode for maps_weather. Default: Beijing adcode.")
    parser.add_argument("--schemas", action="store_true", help="Print tool schemas for maps_ip_location and maps_weather.")
    args = parser.parse_args()

    tools = await _load_amap_tools()
    print("Amap tools:")
    print(", ".join(sorted(name for name in tools if name.startswith("maps_"))))

    for name in ("maps_ip_location", "maps_weather"):
        tool = tools.get(name)
        if not tool:
            print(f"\n{name}: MISSING")
            continue
        print(f"\n{name}: OK")
        if args.schemas:
            schema = getattr(tool, "args_schema", None)
            if schema and hasattr(schema, "model_json_schema"):
                print(json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2))

    ip_tool = tools.get("maps_ip_location")
    if ip_tool:
        print(f"\nCALL maps_ip_location ip={args.ip!r}")
        result = await ip_tool.ainvoke({"ip": args.ip})
        print(_content_to_text(result))

    weather_tool = tools.get("maps_weather")
    if weather_tool:
        print(f"\nCALL maps_weather city={args.city!r}")
        result = await weather_tool.ainvoke({"city": args.city})
        print(_content_to_text(result))

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
