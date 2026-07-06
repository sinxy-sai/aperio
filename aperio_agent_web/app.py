from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
DEMO_SCRIPT = PROJECT_ROOT / "demo" / "aperio_integrated.py"
WORKSPACE_ROOT = PROJECT_ROOT / "demo" / "workspace_integrated"
STATIC_DIR = APP_DIR / "static"

KNOWN_ARTIFACTS = (
    "code_health/code_health_report.md",
    "prd_review/prd_v2_final.md",
    "prd_review/review_matrix.md",
    "performance.json",
)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    approval_mode: str = Field(default="approve")
    timeout_seconds: int = Field(default=900, ge=30, le=3600)


def _run_dirs() -> set[Path]:
    if not WORKSPACE_ROOT.exists():
        return set()
    return {item.resolve() for item in WORKSPACE_ROOT.iterdir() if item.is_dir()}


def _newest_run(before: set[Path]) -> Path | None:
    after = _run_dirs()
    created = [item for item in after if item not in before]
    candidates = created or list(after)
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime)


def _extract_final_answer(stdout: str) -> str:
    marker = "Agent answer:"
    if marker in stdout:
        tail = stdout.split(marker, 1)[1]
        tail = re.split(r"\n=+\n|\n.*Performance:", tail, maxsplit=1)[0]
        return tail.strip()

    lines = [line.rstrip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        return ""
    return "\n".join(lines[-40:]).strip()


def _read_artifacts(run_root: Path | None) -> list[dict[str, Any]]:
    if run_root is None:
        return []

    artifacts: list[dict[str, Any]] = []
    for rel in KNOWN_ARTIFACTS:
        path = (run_root / rel).resolve()
        if not path.exists() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        artifacts.append(
            {
                "path": rel,
                "size": path.stat().st_size,
                "preview": text[:12000],
            }
        )
    return artifacts


def _safe_run_path(run_id: str, rel_path: str) -> Path:
    run_root = (WORKSPACE_ROOT / run_id).resolve()
    target = (run_root / rel_path).resolve()
    if not run_root.exists() or not run_root.is_dir():
        raise HTTPException(status_code=404, detail="Run not found")
    try:
        target.relative_to(run_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid artifact path") from exc
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")
    return target


app = FastAPI(title="Aperio Agent Web")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": DEMO_SCRIPT.exists(),
        "demo_script": str(DEMO_SCRIPT),
        "workspace": str(WORKSPACE_ROOT),
    }


@app.post("/api/chat")
def chat(request: ChatRequest) -> dict[str, Any]:
    approval_mode = request.approval_mode.strip().lower()
    if approval_mode not in {"prompt", "approve", "reject"}:
        raise HTTPException(status_code=400, detail="approval_mode must be prompt, approve, or reject")
    if approval_mode == "prompt":
        raise HTTPException(status_code=400, detail="Web requests cannot use prompt approval mode")
    if not DEMO_SCRIPT.exists():
        raise HTTPException(status_code=500, detail="demo/aperio_integrated.py was not found")

    before = _run_dirs()
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["APERIO_HITL_MODE"] = approval_mode

    started = time.time()
    try:
        completed = subprocess.run(
            [sys.executable, str(DEMO_SCRIPT)],
            input=request.message.strip() + "\n",
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=request.timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        run_root = _newest_run(before)
        raise HTTPException(
            status_code=504,
            detail={
                "message": f"Agent run timed out after {request.timeout_seconds} seconds",
                "run_id": run_root.name if run_root else None,
                "stdout": (exc.stdout or "")[-12000:],
                "stderr": (exc.stderr or "")[-4000:],
            },
        ) from exc

    run_root = _newest_run(before)
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    return {
        "ok": completed.returncode == 0,
        "return_code": completed.returncode,
        "duration_seconds": round(time.time() - started, 1),
        "answer": _extract_final_answer(stdout),
        "run_id": run_root.name if run_root else None,
        "artifacts": _read_artifacts(run_root),
        "stdout_tail": stdout[-20000:],
        "stderr_tail": stderr[-8000:],
    }


@app.get("/api/runs/{run_id}/artifact")
def artifact(run_id: str, path: str) -> FileResponse:
    target = _safe_run_path(run_id, path)
    return FileResponse(target, media_type="text/markdown", filename=target.name)
