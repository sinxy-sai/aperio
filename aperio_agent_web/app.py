from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Empty, Queue
from typing import Any

from fastapi import FastAPI, HTTPException, Request
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
from aperio_agent_backend.runner import UploadedInput, read_artifacts, run_agent, safe_artifact_path

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
REACT_INDEX = STATIC_DIR / "react" / "index.html"
MAX_UPLOAD_FILES = 80
MAX_UPLOAD_BYTES = 80 * 1024 * 1024
MAX_SINGLE_UPLOAD_BYTES = 25 * 1024 * 1024
RUN_EXECUTOR = ThreadPoolExecutor(max_workers=4)
RUN_CANCEL_EVENTS: dict[str, threading.Event] = {}


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
async def chat(request: Request) -> dict[str, Any]:
    chat_request, uploaded_inputs = await _parse_chat_request(request)
    approval_mode = _validate_approval_mode(chat_request.approval_mode)
    result = run_agent(
        message=chat_request.message,
        approval_mode=approval_mode,
        timeout_seconds=chat_request.timeout_seconds,
        uploaded_inputs=uploaded_inputs,
    )
    return result.to_dict()


@app.post("/api/chat/stream")
async def chat_stream(request: Request) -> StreamingResponse:
    chat_request, uploaded_inputs = await _parse_chat_request(request)
    approval_mode = _validate_approval_mode(chat_request.approval_mode)
    run_id = _new_run_id()
    cancel_event = threading.Event()
    event_queue: Queue[dict[str, Any]] = Queue()
    RUN_CANCEL_EVENTS[run_id] = cancel_event

    def emit_trace(event: dict[str, Any]) -> None:
        event_queue.put({"type": "trace", "run_id": run_id, "event": event})

    def stream_events():
        started = time.time()
        yield _json_line({"type": "status", "message": "已接收任务，正在启动 agent", "elapsed": 0})
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                run_agent,
                chat_request.message,
                approval_mode,
                chat_request.timeout_seconds,
                uploaded_inputs,
            )
            tick = 0
            while not future.done():
                elapsed = round(time.time() - started, 1)
                if tick == 0:
                    upload_note = f"，已附加 {len(uploaded_inputs)} 个文件" if uploaded_inputs else ""
                    message = f"agent 正在处理，请保持聊天页打开{upload_note}"
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

    def stream_events():
        started = time.time()
        yield _json_line({"type": "status", "message": "已接收任务，正在启动 agent", "elapsed": 0, "run_id": run_id})
        future = RUN_EXECUTOR.submit(
            run_agent,
            chat_request.message,
            approval_mode=approval_mode,
            timeout_seconds=chat_request.timeout_seconds,
            uploaded_inputs=uploaded_inputs,
            run_id=run_id,
            cancel_event=cancel_event,
            event_callback=emit_trace,
        )
        future.add_done_callback(lambda completed: _finish_run(run_id, cancel_event, completed))
        tick = 0
        while not future.done():
            yield from _drain_trace_events(event_queue)
            elapsed = round(time.time() - started, 1)
            if cancel_event.is_set():
                yield _json_line({"type": "cancelled", "message": "本次运行已停止。", "elapsed": elapsed, "run_id": run_id})
                return
            if tick == 0:
                upload_note = f"，已附加 {len(uploaded_inputs)} 个文件" if uploaded_inputs else ""
                message = f"agent 正在处理，请保持聊天页打开{upload_note}"
            else:
                message = f"agent 仍在运行，已耗时 {elapsed:.1f}s"
            yield _json_line({"type": "status", "message": message, "elapsed": elapsed, "run_id": run_id})
            tick += 1
            time.sleep(1)
        yield from _drain_trace_events(event_queue)
        try:
            result = future.result()
        except Exception as exc:
            yield _json_line({"type": "error", "message": str(exc), "run_id": run_id})
            return
        yield _json_line(
            {
                "type": "trace",
                "run_id": run_id,
                "event": {
                    "type": "phase",
                    "phase": "result_ready",
                    "message": f"返回结果，{len(result.artifacts)} 个产物",
                    "artifact_count": len(result.artifacts),
                },
            }
        )
        yield _json_line({"type": "result", "data": result.to_dict(), "run_id": run_id})

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


@app.get("/api/observability/summary")
def observability_summary(days: int = 7) -> dict[str, Any]:
    days = max(0, min(days, 3650))
    summaries = _run_summaries()
    if days:
        cutoff = datetime.now() - timedelta(days=days)
        summaries = [item for item in summaries if not item["createdAtDate"] or item["createdAtDate"] >= cutoff]

    latencies = sorted(
        float(item["durationSeconds"])
        for item in summaries
        if isinstance(item.get("durationSeconds"), (int, float))
    )
    failed = [item for item in summaries if _is_failed_run(item)]
    route_counts: dict[str, int] = {}
    for item in summaries:
        route = str(item.get("route") or "unknown")
        route_counts[route] = route_counts.get(route, 0) + 1

    total_runs = len(summaries)
    latest = summaries[0] if summaries else {}
    return {
        "windowDays": days,
        "totalRuns": total_runs,
        "successfulRuns": total_runs - len(failed),
        "failedRuns": len(failed),
        "errorRate": (len(failed) / total_runs) if total_runs else 0,
        "p50LatencySeconds": _percentile(latencies, 50),
        "p99LatencySeconds": _percentile(latencies, 99),
        "avgLatencySeconds": (sum(latencies) / len(latencies)) if latencies else 0,
        "totalTokens": sum(int(item.get("totalTokens") or 0) for item in summaries),
        "totalModelCalls": sum(int(item.get("modelCalls") or 0) for item in summaries),
        "totalToolCalls": sum(int(item.get("toolCalls") or 0) for item in summaries),
        "routeCounts": route_counts,
        "latestRunId": latest.get("runId", ""),
        "latestRunRoute": latest.get("route", ""),
        "latestRunDurationSeconds": latest.get("durationSeconds"),
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
    }


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


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str) -> dict[str, Any]:
    cancel_event = RUN_CANCEL_EVENTS.get(run_id)
    if cancel_event is None:
        return {"ok": False, "runId": run_id, "running": False}
    cancel_event.set()
    return {"ok": True, "runId": run_id, "running": True}


@app.delete("/api/runs/{run_id}")
def delete_run(run_id: str) -> dict[str, Any]:
    cancel_event = RUN_CANCEL_EVENTS.get(run_id)
    if cancel_event is not None:
        cancel_event.set()
    run_root = _safe_run_root(run_id)
    shutil.rmtree(run_root)
    return {"ok": True, "runId": run_id}


@app.get("/api/runs/{run_id}/artifact")
def artifact(run_id: str, path: str) -> FileResponse:
    try:
        target = safe_artifact_path(run_id, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(target, media_type="text/markdown", filename=target.name)


def _new_run_id() -> str:
    return time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]


def _finish_run(run_id: str, cancel_event: threading.Event, completed: Any) -> None:
    RUN_CANCEL_EVENTS.pop(run_id, None)
    if cancel_event.is_set():
        run_root = (WORKSPACE_ROOT / run_id).resolve()
        try:
            run_root.relative_to(WORKSPACE_ROOT.resolve())
        except ValueError:
            return
        shutil.rmtree(run_root, ignore_errors=True)


def _drain_trace_events(event_queue: Queue[dict[str, Any]]):
    while True:
        try:
            yield _json_line(event_queue.get_nowait())
        except Empty:
            return


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


async def _parse_chat_request(request: Request) -> tuple[ChatRequest, list[UploadedInput]]:
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        chat_request = ChatRequest(
            message=str(form.get("message") or ""),
            approval_mode=str(form.get("approval_mode") or "approve"),
            timeout_seconds=int(form.get("timeout_seconds") or 900),
        )
        files = form.getlist("files")
        paths = [str(item) for item in form.getlist("paths")]
        uploaded_inputs: list[UploadedInput] = []
        total_bytes = 0

        if len(files) > MAX_UPLOAD_FILES:
            raise HTTPException(status_code=413, detail=f"最多支持上传 {MAX_UPLOAD_FILES} 个文件")

        for index, upload in enumerate(files):
            if not hasattr(upload, "read"):
                continue
            content = await upload.read()
            if len(content) > MAX_SINGLE_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail=f"单个文件不能超过 {MAX_SINGLE_UPLOAD_BYTES // 1024 // 1024}MB")
            total_bytes += len(content)
            if total_bytes > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail=f"总上传大小不能超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB")

            filename = str(getattr(upload, "filename", "") or f"upload-{index + 1}")
            uploaded_inputs.append(
                UploadedInput(
                    filename=filename,
                    relative_path=paths[index] if index < len(paths) else filename,
                    content_type=str(getattr(upload, "content_type", "") or "application/octet-stream"),
                    content=content,
                )
            )
        return chat_request, uploaded_inputs

    return ChatRequest.model_validate(await request.json()), []


def _validate_approval_mode(value: str) -> str:
    approval_mode = value.strip().lower()
    if approval_mode not in {"prompt", "approve", "reject"}:
        raise HTTPException(status_code=400, detail="approval_mode must be prompt, approve, or reject")
    if approval_mode == "prompt":
        raise HTTPException(status_code=400, detail="Web requests cannot use prompt approval mode")
    return approval_mode


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


def _run_summaries() -> list[dict[str, Any]]:
    if not WORKSPACE_ROOT.exists():
        return []
    items = []
    for run_root in sorted((item for item in WORKSPACE_ROOT.iterdir() if item.is_dir()), reverse=True):
        performance = _read_json(run_root / "performance.json")
        artifacts = read_artifacts(run_root)
        created_at_date = _parse_run_datetime(run_root.name)
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
                "createdAtDate": created_at_date,
                "error": performance.get("error", ""),
            }
        )
    return items


def _parse_run_datetime(run_id: str) -> datetime | None:
    try:
        return datetime.strptime(run_id[:15], "%Y%m%d_%H%M%S")
    except ValueError:
        return None


def _is_failed_run(item: dict[str, Any]) -> bool:
    return item.get("ok") is False or item.get("route") == "error" or bool(item.get("error"))


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        return 0
    if len(values) == 1:
        return values[0]
    rank = (percentile / 100) * (len(values) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(values) - 1)
    weight = rank - lower
    return values[lower] * (1 - weight) + values[upper] * weight
