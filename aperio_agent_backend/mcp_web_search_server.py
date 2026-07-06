from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastmcp import FastMCP


PRD_REVIEW_SEARCH_BUDGET = {
    "/outputs/prd_review/raw/web_search/writer-research.json": 1,
    "/outputs/prd_review/raw/web_search/product-strategy.json": 1,
}

mcp = FastMCP("aperio-web-search")


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)


def _outputs_root() -> Path:
    raw = os.environ.get("APERIO_OUTPUTS_DIR", "").strip()
    if not raw:
        raise RuntimeError("APERIO_OUTPUTS_DIR is not set")
    return Path(raw).resolve()


def _state_path() -> Path:
    return _outputs_root() / "_mcp_web_search_state.json"


def _read_counts() -> dict[str, int]:
    path = _state_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    counts = data.get("search_counts", {}) if isinstance(data, dict) else {}
    if not isinstance(counts, dict):
        return {}
    return {str(key): int(value) for key, value in counts.items()}


def _write_counts(counts: dict[str, int]) -> None:
    _state_path().write_text(
        json.dumps({"search_counts": counts}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _validate_save_path(save_path: str) -> str:
    save_path = (save_path or "").strip().replace("\\", "/")
    if not save_path:
        return ""
    if (
        not save_path.startswith("/outputs/")
        or not save_path.endswith(".json")
        or "/../" in save_path
        or save_path.startswith("/outputs/../")
    ):
        raise ValueError("save_path must be a JSON file under /outputs/")
    return save_path


def _write_evidence(save_path: str, payload: dict[str, Any]) -> None:
    outputs_root = _outputs_root()
    rel = save_path.removeprefix("/outputs/").lstrip("/")
    target = (outputs_root / rel).resolve()
    try:
        target.relative_to(outputs_root)
    except ValueError as exc:
        raise ValueError("save_path resolves outside APERIO_OUTPUTS_DIR") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


@mcp.tool
def internet_search(query: str, max_results: int = 3, save_path: str = "") -> str:
    """Search public web results and optionally save JSON evidence under /outputs/."""
    query = (query or "").strip()
    if not query:
        return _json({"ok": False, "error": "query is empty", "results": []})

    try:
        save_path = _validate_save_path(save_path)
    except ValueError as exc:
        return _json({"ok": False, "query": query, "error": str(exc), "results": []})

    if save_path.startswith("/outputs/prd_review/raw/web_search/"):
        allowed = ", ".join(sorted(PRD_REVIEW_SEARCH_BUDGET))
        if save_path not in PRD_REVIEW_SEARCH_BUDGET:
            return _json(
                {
                    "ok": False,
                    "query": query,
                    "save_path": save_path,
                    "error": f"PRD review web searches must save evidence to one allowed path: {allowed}",
                    "results": [],
                }
            )
        search_counts = _read_counts()
        current_count = search_counts.get(save_path, 0)
        max_count = PRD_REVIEW_SEARCH_BUDGET[save_path]
        if current_count >= max_count:
            return _json(
                {
                    "ok": False,
                    "query": query,
                    "save_path": save_path,
                    "error": f"PRD review web search budget exceeded for {save_path}: {current_count}/{max_count}",
                    "results": [],
                }
            )

    try:
        limit = max(1, min(int(max_results), 5))
    except (TypeError, ValueError):
        limit = 3

    try:
        from ddgs import DDGS
    except Exception as exc:
        return _json({"ok": False, "query": query, "error": f"ddgs is not available: {exc}", "results": []})

    try:
        with DDGS() as ddgs:
            raw_results = list(ddgs.text(query, max_results=limit, safesearch="moderate"))
    except Exception as exc:
        return _json({"ok": False, "query": query, "error": f"search failed: {exc}", "results": []})

    results = []
    for item in raw_results[:limit]:
        title = str(item.get("title") or "").strip()
        snippet = str(item.get("body") or item.get("snippet") or "").strip()
        url = str(item.get("href") or item.get("url") or "").strip()
        if title or snippet or url:
            results.append({"title": title, "snippet": snippet[:600], "url": url})

    payload = {
        "ok": True,
        "query": query,
        "max_results": limit,
        "results": results,
        "evidence_policy": "Public web snippets are supplemental evidence. Local files and tool results remain authoritative.",
    }
    if save_path:
        if save_path in PRD_REVIEW_SEARCH_BUDGET:
            counts = _read_counts()
            counts[save_path] = counts.get(save_path, 0) + 1
            _write_counts(counts)
        try:
            _write_evidence(save_path, payload)
        except Exception as exc:
            return _json(
                {
                    "ok": False,
                    "query": query,
                    "save_path": save_path,
                    "error": f"failed to save evidence: {exc}",
                    "results": results,
                }
            )
        payload["saved_to"] = save_path

    return _json(payload)


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
