from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from aperio_agent_backend.config import WORKSPACE_ROOT, get_api_key, get_model_name
from aperio_agent_backend.runner import run_agent, safe_artifact_path

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    approval_mode: str = Field(default="approve")
    timeout_seconds: int = Field(default=900, ge=30, le=3600)


app = FastAPI(title="Aperio Agent Web")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": bool(get_api_key()),
        "backend": "aperio_agent_backend",
        "model": get_model_name(),
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


@app.get("/api/runs/{run_id}/artifact")
def artifact(run_id: str, path: str) -> FileResponse:
    try:
        target = safe_artifact_path(run_id, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(target, media_type="text/markdown", filename=target.name)
