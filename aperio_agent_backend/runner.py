from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from langchain.chat_models import init_chat_model

from .config import PROJECT_ROOT, WORKSPACE_ROOT, get_api_key, get_base_url, get_model_name


KNOWN_ARTIFACTS = (
    "code_health/code_health_report.md",
    "prd_review/prd_v2_final.md",
    "prd_review/review_matrix.md",
    "performance.json",
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
        if route == "prd":
            answer = _run_prd_review(message, run_root)
        elif route == "code_health":
            answer = _run_code_health(message, run_root)
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
    if re.search(r"\bprd\b|产品需求|需求文档|评审|review matrix|原型", text, re.IGNORECASE):
        return "prd"
    if re.search(r"代码|code|仓库|项目|体检|健康|质量|安全|依赖|扫描|review", text, re.IGNORECASE):
        return "code_health"
    return "general"


def _model():
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError(
            "未配置 DEEPSEEK_API_KEY。请在 aperio_agent_backend/.env 中配置，或在当前 shell 环境变量中配置。"
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
    prd_dir = run_root / "prd_review"
    prd_prompt = (
        "请基于用户输入编写一份中文 PRD v2，必须只使用用户已经给出的事实；"
        "缺失信息标为“待确认”。结构包括：背景、目标用户、核心场景、范围边界、"
        "功能需求、非功能需求、数据与权限、验收标准、风险与里程碑。"
    )
    prd = _invoke(prd_prompt, message)
    matrix_prompt = (
        "请对下面 PRD 做中文评审矩阵。用 Markdown 表格输出，列为：维度、发现、风险等级、建议、验收方式。"
    )
    matrix = _invoke(matrix_prompt, prd)

    _write_text(prd_dir / "prd_v2_final.md", prd)
    _write_text(prd_dir / "review_matrix.md", matrix)
    return "PRD 评审已完成，右侧产物面板中可查看 `prd_v2_final.md` 和 `review_matrix.md`。"


def _run_code_health(message: str, run_root: Path) -> str:
    target = _resolve_target_path(message)
    summary = _scan_project(target)
    prompt = (
        "你是资深代码健康审查员。请基于静态扫描摘要输出中文 Markdown 报告。"
        "包括：总体结论、目录/技术栈观察、主要风险、建议优先级、后续验证清单。"
        "只能引用摘要中出现的事实，不要声称已经运行测试或安全扫描工具。"
    )
    report = _invoke(prompt, json.dumps(summary, ensure_ascii=False, indent=2))
    _write_text(run_root / "code_health" / "code_health_report.md", report)
    return "代码健康报告已完成。该后端未使用 Docker，只做轻量静态扫描和 LLM 总结；完整报告在右侧产物面板。"


def _resolve_target_path(message: str) -> Path:
    candidates = re.findall(r"`([^`]+)`|['\"]([^'\"]+)['\"]", message)
    flat = [item for pair in candidates for item in pair if item]
    flat.extend(token for token in re.split(r"\s+", message) if "/" in token or "\\" in token)
    for raw in flat:
        cleaned = raw.strip().strip("，。；;:()[]{}")
        path = Path(cleaned)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        try:
            resolved = path.resolve()
            resolved.relative_to(PROJECT_ROOT.resolve())
        except ValueError:
            continue
        if resolved.exists():
            return resolved
    return PROJECT_ROOT


def _scan_project(target: Path) -> dict[str, Any]:
    root = target if target.is_dir() else target.parent
    ignored = {".git", "__pycache__", "node_modules", ".venv", "venv", "workspace"}
    files: list[Path] = []
    for path in root.rglob("*"):
        if len(files) >= 260:
            break
        if any(part in ignored or part.startswith("workspace_") for part in path.parts):
            continue
        if path.is_file() and path.stat().st_size <= 250_000:
            files.append(path)

    extensions: dict[str, int] = {}
    notable: list[str] = []
    for path in files:
        ext = path.suffix.lower() or "<none>"
        extensions[ext] = extensions.get(ext, 0) + 1
        if path.name in {"package.json", "pyproject.toml", "requirements.txt", "environment.yml", "Dockerfile"}:
            notable.append(_rel(path))

    return {
        "target": _rel(target),
        "scanned_root": _rel(root),
        "file_count_sampled": len(files),
        "extensions": dict(sorted(extensions.items(), key=lambda item: item[1], reverse=True)[:20]),
        "notable_files": notable[:40],
        "sample_files": [_rel(path) for path in files[:80]],
        "limitations": [
            "未启动 Docker 沙箱",
            "未运行测试、lint、SAST 或依赖漏洞扫描",
            "报告基于文件结构、清单文件和抽样文件列表生成",
        ],
    }


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def _read_artifacts(run_root: Path) -> list[AgentArtifact]:
    artifacts: list[AgentArtifact] = []
    for rel in KNOWN_ARTIFACTS:
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
        "model": get_model_name(),
    }
    if error:
        payload["error"] = error
    _write_text(run_root / "performance.json", json.dumps(payload, ensure_ascii=False, indent=2))
