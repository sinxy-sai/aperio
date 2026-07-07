from __future__ import annotations

import time
import uuid
from typing import Any


EVENT_SCHEMA = "aperio.event.v1"


def normalize_event(event: dict[str, Any] | None, *, run_id: str = "") -> dict[str, Any]:
    """Return a backward-compatible event with the stable Aperio event contract.

    Existing clients still read legacy fields such as ``type`` and ``phase``.
    New clients should prefer ``eventType``/``event_type`` plus ``status``.
    """
    source = dict(event or {})
    legacy_type = str(source.get("type") or "phase")
    event_type = str(source.get("eventType") or source.get("event_type") or _infer_event_type(source))
    status = str(source.get("status") or _infer_status(source, event_type))
    name = str(source.get("name") or source.get("phase") or source.get("tool") or legacy_type)
    event_id = str(source.get("eventId") or source.get("event_id") or _new_event_id())

    normalized = {
        **source,
        "schema": source.get("schema") or EVENT_SCHEMA,
        "eventId": event_id,
        "event_id": event_id,
        "eventType": event_type,
        "event_type": event_type,
        "legacyType": source.get("legacyType") or legacy_type,
        "legacy_type": source.get("legacy_type") or legacy_type,
        "name": name,
        "status": status,
        "createdAt": source.get("createdAt") or source.get("created_at") or time.time(),
    }
    if run_id and not normalized.get("runId"):
        normalized["runId"] = run_id
        normalized["run_id"] = run_id
    return normalized


def _new_event_id() -> str:
    return f"evt_{uuid.uuid4().hex[:12]}"


def _infer_event_type(event: dict[str, Any]) -> str:
    legacy_type = str(event.get("type") or "")
    phase = str(event.get("phase") or "")
    if phase == "run_started":
        return "thread.started"
    if phase in {"agent_started", "deepagents_start"}:
        return "turn.started"
    if phase in {"run_completed", "result_ready"}:
        return "turn.completed"
    if phase in {"run_failed", "cancelled"} or event.get("error"):
        return "turn.failed"
    if legacy_type in {"model", "tool", "artifact"}:
        return "item.completed"
    if phase.endswith("_started"):
        return "item.started"
    if phase.endswith("_completed") or phase.endswith("_written"):
        return "item.completed"
    return "item.updated"


def _infer_status(event: dict[str, Any], event_type: str) -> str:
    if event.get("error"):
        return "failed"
    if event_type.endswith(".started"):
        return "running"
    if event_type.endswith(".failed"):
        return "failed"
    if event_type.endswith(".completed"):
        return "completed"
    return "updated"
