from __future__ import annotations

import re
from typing import Any, Callable

from langgraph.types import Command


FINAL_WRITE_PATHS = {
    "/outputs/code_health/code_health_report.md",
    "/outputs/prd_review/prd_v2_final.md",
    "/outputs/prd_review/review_matrix.md",
}
AUTO_WRITE_PATH_RE = re.compile(
    r"^/outputs/(?:(?:code_health/drafts/(?:architect|security|dependencies|documentation)\.md)"
    r"|(?:prd_review/prd_v1\.md)"
    r"|(?:prd_review/drafts/review_(?:strategy|tech|ux|risk)\.md)"
    r"|(?:code_health/raw/tool_results\.json))$"
)

SAFE_MKDIR_RE = re.compile(r"^\s*mkdir\s+-p\s+(?:['\"]?(?:/outputs|/temp|/tmp)(?:/[^\s;&|<>]*)?['\"]?\s*)+$")
HIGH_RISK_EXECUTE_RE = re.compile(
    r"""
    (^|[\s;&|])(rm|rmdir|unlink|shred|mkdir|mv|cp|chmod|chown|chgrp|truncate|dd|mkfs|mount|umount|sudo|docker|podman)\b
    |\b(pip|pip3|uv|poetry|pipenv|conda|npm|pnpm|yarn|bun|apt|apt-get|apk|yum|dnf|brew|cargo|go)\s+(install|add|remove|uninstall|update|upgrade|sync|get|mod)\b
    |\bgit\s+(reset|clean|checkout|restore|switch|pull|merge|rebase|push|commit|tag)\b
    |(^|[\s;&|])tee\s+
    |(?:>>?|<)
    """,
    re.IGNORECASE | re.VERBOSE,
)
INLINE_CODE_EXECUTE_RE = re.compile(r"\b(python|python3|node|ruby|perl|bash|sh)\s+(-c|-m\s+pip|<<|-)\b", re.IGNORECASE)
SCRIPT_OR_MODULE_EXECUTE_RE = re.compile(
    r"(^|[\s;&|])(?:python(?:3)?|node|ruby|perl|bash|sh)\s+"
    r"(?:-m\s+[A-Za-z0-9_.-]+|[^\s;&|<>]+\.(?:py|js|mjs|cjs|rb|pl|sh|bash))\b",
    re.IGNORECASE,
)


def build_interrupt_policy() -> dict[str, Any]:
    return {
        "write_file": {
            "allowed_decisions": ["approve", "reject"],
            "when": _write_requires_approval,
        },
        "execute": {
            "allowed_decisions": ["approve", "reject"],
            "when": _execute_requires_approval,
        },
    }


def resolve_human_interrupts(
    agent: Any,
    response: Any,
    config: dict[str, Any],
    *,
    approval_mode: str,
    input_fn: Callable[[str], str] = input,
) -> Any:
    mode = (approval_mode or "approve").strip().lower()
    if mode not in {"prompt", "approve", "reject"}:
        mode = "approve"

    while hasattr(response, "interrupts") and response.interrupts:
        resume_map: dict[str, Any] = {}
        single_payload: dict[str, Any] | None = None
        for interrupt in response.interrupts:
            decisions = []
            value = getattr(interrupt, "value", {}) or {}
            for action in value.get("action_requests", []):
                tool_name = action.get("name", "")
                args = action.get("args", {}) or {}
                approved = _approval_decision(mode, tool_name, args, input_fn)
                decisions.append(
                    {
                        "action_id": action.get("id"),
                        "tool_name": tool_name,
                        "type": "approve" if approved else "reject",
                        "updated_args": args if approved else None,
                    }
                )
            payload = {"decisions": decisions}
            single_payload = payload
            resume_map[interrupt.id] = payload
        response = agent.invoke(
            Command(resume=single_payload if len(response.interrupts) == 1 else resume_map),
            config=config,
        )
    return response


def _approval_decision(mode: str, tool_name: str, args: dict[str, Any], input_fn: Callable[[str], str]) -> bool:
    if mode == "approve":
        return True
    if mode == "reject":
        return False

    preview = _action_preview(tool_name, args)
    choice = input_fn(f"\nAperio 请求执行 {tool_name}: {preview}\n[a]pprove / [r]eject: ").strip().lower()
    normalized = re.sub(r"[^a-z]", "", choice)
    return normalized.startswith("a") or normalized in {"y", "yes"}


def _write_requires_approval(request: Any) -> bool:
    args = request.tool_call.get("args", {})
    path = _normalize_virtual_path(_write_file_path(args))
    if path in FINAL_WRITE_PATHS:
        return True
    if AUTO_WRITE_PATH_RE.fullmatch(path):
        return False
    return True


def _execute_requires_approval(request: Any) -> bool:
    args = request.tool_call.get("args", {})
    command = str(args.get("command") or "").strip()
    if not command:
        return True
    if SAFE_MKDIR_RE.fullmatch(command):
        return False
    if HIGH_RISK_EXECUTE_RE.search(command):
        return True
    if INLINE_CODE_EXECUTE_RE.search(command):
        return True
    if SCRIPT_OR_MODULE_EXECUTE_RE.search(command):
        return True
    return False


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


def _action_preview(tool_name: str, args: dict[str, Any]) -> str:
    if tool_name == "execute":
        return _redact(str(args.get("command") or ""))
    if tool_name == "write_file":
        path = _write_file_path(args)
        content = str(args.get("content") or "")
        return f"{path} ({len(content)} chars)"
    return _redact(str(args)[:240])


def _redact(value: str) -> str:
    redacted = re.sub(
        r"(?i)\b(api[_-]?key|token|password|passwd|secret|authorization)\s*=\s*([^\s]+)",
        r"\1=<redacted>",
        value,
    )
    return redacted.replace("\n", "\\n")[:240]
