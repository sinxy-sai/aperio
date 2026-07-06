from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from langchain.chat_models import init_chat_model

from .config import (
    PROJECT_ROOT,
    WORKSPACE_ROOT,
    get_api_key,
    get_base_url,
    get_engine_name,
    get_install_project_deps,
    get_model_name,
)
from .deepagents_engine import run_deep_agent
from .scanner import compact_scan_summary, run_code_health_scan


KNOWN_ARTIFACTS = (
    "outputs/code_health/code_health_report.md",
    "outputs/prd_review/prd_v2_final.md",
    "outputs/prd_review/review_matrix.md",
    "outputs/code_health/raw/tool_results.json",
    "performance.json",
    # Backward-compatible paths from the first backend prototype.
    "code_health/code_health_report.md",
    "prd_review/prd_v2_final.md",
    "prd_review/review_matrix.md",
)

PROJECT_ROOT_MARKERS = (
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "poetry.lock",
    "uv.lock",
    "package.json",
    ".git",
)


@dataclass
class AgentArtifact:
    path: str
    size: int
    preview: str


@dataclass
class AgentRunResult:
    ok: bool
    return_code: int
    duration_seconds: float
    answer: str
    run_id: str
    artifacts: list[AgentArtifact]
    stdout_tail: str = ""
    stderr_tail: str = ""
    route: str = "general"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["artifacts"] = [asdict(item) for item in self.artifacts]
        return data


def run_agent(message: str, approval_mode: str = "approve", timeout_seconds: int = 900) -> AgentRunResult:
    started = time.time()
    run_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    run_root = WORKSPACE_ROOT / run_id
    run_root.mkdir(parents=True, exist_ok=True)

    try:
        route = _route_task(message)
        code_project_path, code_target_rel, code_target_path, code_context_found = resolve_code_context(message)
        input_bundle = build_input_bundle(
            run_id=run_id,
            task_text=message,
            code_project_path=code_project_path,
            code_target_rel=code_target_rel,
            code_target_path=code_target_path,
            code_context_found=code_context_found,
        )

        scan_summary: dict[str, Any] | None = None
        if route == "code_health":
            scan_result = run_code_health_scan(
                code_project_path,
                code_target_rel,
                run_root / "outputs" / "code_health" / "raw" / "tool_results.json",
                timeout_seconds=min(max(timeout_seconds // 3, 60), 360),
                install_project_deps=get_install_project_deps(),
            )
            scan_summary = compact_scan_summary(scan_result)

        if get_engine_name() == "deepagents":
            answer = run_deep_agent(
                message,
                run_root,
                input_bundle=input_bundle,
                code_scan_summary=scan_summary,
            )
        else:
            if route == "prd":
                answer = _run_prd_review(message, run_root)
            elif route == "code_health":
                answer = _run_code_health_lite(message, run_root, scan_summary)
            else:
                answer = _run_general(message)

        _write_performance(run_root, started, route, ok=True)
        return AgentRunResult(
            ok=True,
            return_code=0,
            duration_seconds=round(time.time() - started, 1),
            answer=answer,
            run_id=run_id,
            artifacts=_read_artifacts(run_root),
            route=route,
        )
    except Exception as exc:
        message_text = f"后端 agent 运行失败：{exc}"
        _write_text(run_root / "error.txt", message_text)
        _write_performance(run_root, started, "error", ok=False, error=str(exc))
        return AgentRunResult(
            ok=False,
            return_code=1,
            duration_seconds=round(time.time() - started, 1),
            answer=message_text,
            run_id=run_id,
            artifacts=_read_artifacts(run_root),
            stderr_tail=str(exc),
            route="error",
        )


def read_artifacts(run_root: Path | None) -> list[AgentArtifact]:
    if run_root is None:
        return []
    return _read_artifacts(run_root)


def safe_artifact_path(run_id: str, rel_path: str) -> Path:
    run_root = (WORKSPACE_ROOT / run_id).resolve()
    target = (run_root / rel_path).resolve()
    if not run_root.exists() or not run_root.is_dir():
        raise FileNotFoundError("Run not found")
    try:
        target.relative_to(run_root)
    except ValueError as exc:
        raise ValueError("Invalid artifact path") from exc
    if not target.exists() or not target.is_file():
        raise FileNotFoundError("Artifact not found")
    return target


def _route_task(message: str) -> str:
    text = message.lower()
    if re.search(r"\bprd\b|产品需求|需求文档|需求评审|评审矩阵|产品评审|原型|验收标准", text, re.IGNORECASE):
        return "prd"
    if re.search(
        r"代码|code|仓库|项目|体检|健康|质量|安全|依赖|扫描|审查|review|ruff|mypy|bandit|pytest|coverage",
        text,
        re.IGNORECASE,
    ):
        return "code_health"
    return "general"


def resolve_code_context(task_text: str) -> tuple[Path, str, Path, bool]:
    """Resolve optional code context without deciding the route."""
    for candidate in extract_path_candidates(task_text):
        candidate_path = _resolve_host_path(candidate)
        if not candidate_path.exists():
            continue
        try:
            candidate_path.relative_to(PROJECT_ROOT.resolve())
        except ValueError:
            continue
        project_root = infer_project_root(candidate_path)
        target_rel = _relative_path(candidate_path, project_root)
        return project_root, target_rel, candidate_path, True

    project_root = PROJECT_ROOT.resolve()
    return project_root, ".", project_root, False


def build_input_bundle(
    *,
    run_id: str,
    task_text: str,
    code_project_path: Path,
    code_target_rel: str,
    code_target_path: Path,
    code_context_found: bool,
) -> dict[str, Any]:
    attachments: list[dict[str, Any]] = []
    if code_context_found:
        attachments.append(
            {
                "id": "code_context_1",
                "type": "directory" if code_target_path.is_dir() else "file",
                "path": str(code_target_path),
                "project_root": str(code_project_path),
                "target_relative_path": code_target_rel,
                "purpose_hint": "candidate input for code-health if the router selects that workflow",
            }
        )

    return {
        "schema_version": "aperio-input-bundle-v1",
        "run_id": run_id,
        "user_text": task_text,
        "attachments": attachments,
        "resolved_paths": [
            {
                "kind": "code_context",
                "found": code_context_found,
                "host_project_root": str(code_project_path),
                "host_target_path": str(code_target_path),
                "target_relative_path": code_target_rel,
            }
        ],
        "runtime_context": {
            "inputs": "/inputs",
            "outputs": "/outputs",
            "skills": "/skills",
            "code_health_raw_results": "/outputs/code_health/raw/tool_results.json",
        },
        "routing_policy": {
            "router_decides_task": True,
            "fallback_agent": "general-purpose",
            "demo_runtime_dependency": False,
        },
    }


def extract_path_candidates(task_text: str) -> list[str]:
    candidates: list[str] = []
    for match in re.finditer(r"[`'\"“「『](.+?)[`'\"”」』]", task_text):
        candidates.append(_clean_path_candidate(match.group(1)))

    for pattern in (
        r"(?:对|给|扫描|检查|分析|体检)\s*(.+?)(?:做|进行|执行|开展|生成|输出|写入|$)",
        r"(?:代码库|项目|目录|路径)\s*[:：]?\s*(.+?)(?:\s|，|。|；|;|$)",
    ):
        for match in re.finditer(pattern, task_text):
            candidates.append(_clean_path_candidate(match.group(1)))

    for token in re.split(r"\s+", task_text):
        cleaned = _clean_path_candidate(token)
        if "/" in cleaned or "\\" in cleaned or re.match(r"^[A-Za-z]:", cleaned):
            candidates.append(cleaned)

    ordered: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in ordered:
            ordered.append(candidate)
    return ordered


def infer_project_root(target_path: Path) -> Path:
    current = target_path if target_path.is_dir() else target_path.parent
    while True:
        if any((current / marker).exists() for marker in PROJECT_ROOT_MARKERS):
            return current
        if current.parent == current:
            return target_path if target_path.is_dir() else target_path.parent
        current = current.parent


def _clean_path_candidate(text: str) -> str:
    candidate = text.strip().strip("`'\"“”‘’「」『』（）()[]【】")
    prefixes = ("我的代码库", "这个代码库", "代码库", "项目", "目录", "路径", "当前")
    for prefix in prefixes:
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix):].strip(" ：:，,")
    candidate = re.split(
        r"(?:做|进行|执行|开展|生成|输出|写入|完整|全面|代码健康|代码体检|代码检查|检查|体检|分析)",
        candidate,
        maxsplit=1,
    )[0].strip(" ：:，,。.;；")
    return candidate


def _resolve_host_path(path: str) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate.resolve()


def _relative_path(path: Path, root: Path) -> str:
    try:
        rel = path.resolve().relative_to(root.resolve())
        value = str(rel).replace("\\", "/").strip("/")
        return "." if value in {"", "."} else value
    except ValueError:
        return "."


def _model():
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError(
            "未配置 DEEPSEEK_API_KEY。请在 ~/.aperio/.env 中配置，或在当前 shell 环境变量中配置。"
        )
    return init_chat_model(
        model=get_model_name(),
        api_key=api_key,
        base_url=get_base_url(),
    )


def _invoke(system_prompt: str, user_prompt: str) -> str:
    response = _model().invoke(
        [
            ("system", system_prompt),
            ("user", user_prompt),
        ]
    )
    return _content_text(getattr(response, "content", response)).strip()


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                value = item.get("text") or item.get("content")
                if value:
                    parts.append(str(value))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


def _run_general(message: str) -> str:
    return _invoke(
        "你是 Aperio 的通用智能助手。回答要简洁、准确，优先使用中文。"
        "如果问题需要实时外部数据但你没有工具可验证，请明确说明限制，不要编造。",
        message,
    )


def _run_prd_review(message: str, run_root: Path) -> str:
    prd_dir = run_root / "outputs" / "prd_review"
    prd_prompt = (
        "请基于用户输入编写一份中文 PRD v2。只能使用用户已经给出的事实；缺失信息标为“待确认”。"
        "结构包括：背景、目标用户、核心场景、范围边界、功能需求、非功能需求、数据与权限、验收标准、风险与里程碑。"
    )
    prd = _invoke(prd_prompt, message)
    matrix_prompt = "请对下面 PRD 做中文评审矩阵。用 Markdown 表格输出，列为：维度、发现、风险等级、建议、验收方式。"
    matrix = _invoke(matrix_prompt, prd)

    _write_text(prd_dir / "prd_v2_final.md", prd)
    _write_text(prd_dir / "review_matrix.md", matrix)
    return "PRD 评审已完成，产物为 `outputs/prd_review/prd_v2_final.md` 和 `outputs/prd_review/review_matrix.md`。"


def _run_code_health_lite(message: str, run_root: Path, scan_summary: dict[str, Any] | None) -> str:
    prompt = (
        "你是资深代码健康审查员。请基于后端扫描摘要输出中文 Markdown 报告。"
        "包括：总体结论、扫描范围与限制、工具覆盖情况、主要风险、优先级建议、后续验证清单。"
        "只能引用摘要中出现的事实，不要声称运行了摘要未显示的工具。"
    )
    report = _invoke(prompt, json.dumps(scan_summary or {}, ensure_ascii=False, indent=2))
    _write_text(run_root / "outputs" / "code_health" / "code_health_report.md", report)
    return "代码健康报告已完成，产物为 `outputs/code_health/code_health_report.md`。"


def _read_artifacts(run_root: Path) -> list[AgentArtifact]:
    artifacts: list[AgentArtifact] = []
    seen: set[str] = set()
    for rel in KNOWN_ARTIFACTS:
        if rel in seen:
            continue
        seen.add(rel)
        path = (run_root / rel).resolve()
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        artifacts.append(AgentArtifact(path=rel, size=path.stat().st_size, preview=text[:12000]))
    return artifacts


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")


def _write_performance(run_root: Path, started: float, route: str, ok: bool, error: str = "") -> None:
    payload = {
        "ok": ok,
        "route": route,
        "duration_seconds": round(time.time() - started, 1),
        "backend": "aperio_agent_backend",
        "engine": get_engine_name(),
        "model": get_model_name(),
    }
    if error:
        payload["error"] = error
    _write_text(run_root / "performance.json", json.dumps(payload, ensure_ascii=False, indent=2))
