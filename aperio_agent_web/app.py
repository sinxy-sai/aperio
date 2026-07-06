from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from aperio_agent_backend.config import (
    WORKSPACE_ROOT,
    get_amap_api_key,
    get_api_key,
    get_enable_mcp_tools,
    get_engine_name,
    get_install_project_deps,
    get_model_name,
    get_scan_sandbox_mode,
)
from aperio_agent_backend.runner import read_artifacts, run_agent, safe_artifact_path

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
REACT_INDEX = STATIC_DIR / "react" / "index.html"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    approval_mode: str = Field(default="approve")
    timeout_seconds: int = Field(default=900, ge=30, le=3600)


app = FastAPI(title="Aperio Agent Web")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    if REACT_INDEX.exists():
        return REACT_INDEX.read_text(encoding="utf-8")
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/observability", response_class=HTMLResponse)
def observability() -> str:
    if REACT_INDEX.exists():
        return REACT_INDEX.read_text(encoding="utf-8")
    return (STATIC_DIR / "observability.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": bool(get_api_key()),
        "backend": "aperio_agent_backend",
        "engine": get_engine_name(),
        "model": get_model_name(),
        "scanSandbox": get_scan_sandbox_mode(),
        "installProjectDeps": get_install_project_deps(),
        "mcpTools": get_enable_mcp_tools(),
        "amapConfigured": bool(get_amap_api_key()),
        "workspace": str(WORKSPACE_ROOT),
        "configured": bool(get_api_key()),
    }


@app.post("/api/chat")
def chat(request: ChatRequest) -> dict[str, Any]:
    approval_mode = request.approval_mode.strip().lower()
    if approval_mode not in {"prompt", "approve", "reject"}:
        raise HTTPException(status_code=400, detail="approval_mode must be prompt, approve, or reject")
    if approval_mode == "prompt":
        raise HTTPException(status_code=400, detail="Web requests cannot use prompt approval mode")
    result = run_agent(
        message=request.message,
        approval_mode=approval_mode,
        timeout_seconds=request.timeout_seconds,
    )
    return result.to_dict()


@app.post("/api/chat/stream")
def chat_stream(request: ChatRequest) -> StreamingResponse:
    approval_mode = request.approval_mode.strip().lower()
    if approval_mode not in {"prompt", "approve", "reject"}:
        raise HTTPException(status_code=400, detail="approval_mode must be prompt, approve, or reject")
    if approval_mode == "prompt":
        raise HTTPException(status_code=400, detail="Web requests cannot use prompt approval mode")

    def stream_events():
        started = time.time()
        yield _json_line({"type": "status", "message": "已接收任务，正在启动 agent", "elapsed": 0})
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                run_agent,
                request.message,
                approval_mode,
                request.timeout_seconds,
            )
            tick = 0
            while not future.done():
                elapsed = round(time.time() - started, 1)
                if tick == 0:
                    message = "agent 正在处理，请保持聊天页打开；观测平台会在新标签页显示"
                else:
                    message = f"agent 仍在运行，已耗时 {elapsed:.1f}s"
                yield _json_line({"type": "status", "message": message, "elapsed": elapsed})
                tick += 1
                time.sleep(1)
            try:
                result = future.result()
            except Exception as exc:
                yield _json_line({"type": "error", "message": str(exc)})
                return
        yield _json_line({"type": "result", "data": result.to_dict()})

    return StreamingResponse(
        stream_events(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/runs")
def runs(limit: int = 30) -> dict[str, Any]:
    limit = max(1, min(limit, 100))
    if not WORKSPACE_ROOT.exists():
        return {"runs": []}

    items = []
    for run_root in sorted((item for item in WORKSPACE_ROOT.iterdir() if item.is_dir()), reverse=True):
        performance = _read_json(run_root / "performance.json")
        artifacts = read_artifacts(run_root)
        items.append(
            {
                "runId": run_root.name,
                "ok": performance.get("ok"),
                "route": performance.get("route", "unknown"),
                "durationSeconds": performance.get("duration_seconds"),
                "engine": performance.get("engine"),
                "model": performance.get("model"),
                "modelCalls": performance.get("model_calls", 0),
                "toolCalls": performance.get("tool_calls", 0),
                "totalTokens": performance.get("total_tokens", 0),
                "artifactCount": len(artifacts),
                "createdAt": run_root.name[:15],
            }
        )
        if len(items) >= limit:
            break
    return {"runs": items}


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str) -> dict[str, Any]:
    run_root = _safe_run_root(run_id)
    performance = _read_json(run_root / "performance.json")
    observability = _read_json(run_root / "observability.json")
    return {
        "runId": run_id,
        "performance": performance,
        "observability": observability,
        "artifacts": [item.__dict__ for item in read_artifacts(run_root)],
        "files": _list_run_files(run_root),
    }


@app.get("/api/runs/{run_id}/artifact")
def artifact(run_id: str, path: str) -> FileResponse:
    try:
        target = safe_artifact_path(run_id, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(target, media_type="text/markdown", filename=target.name)


def _safe_run_root(run_id: str) -> Path:
    run_root = (WORKSPACE_ROOT / run_id).resolve()
    try:
        run_root.relative_to(WORKSPACE_ROOT.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid run id") from exc
    if not run_root.exists() or not run_root.is_dir():
        raise HTTPException(status_code=404, detail="Run not found")
    return run_root


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"error": "invalid JSON"}


def _json_line(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


def _list_run_files(run_root: Path) -> list[dict[str, Any]]:
    files = []
    for path in sorted(run_root.rglob("*")):
        if path.is_file():
            files.append(
                {
                    "path": path.relative_to(run_root).as_posix(),
                    "size": path.stat().st_size,
                }
            )
    return files[:300]
