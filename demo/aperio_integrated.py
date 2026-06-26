"""
Aperio Integrated Demo — task router + two orchestrators with skill-equipped sub-agents.

Architecture:
  Main Router
    ├── code-health-orchestrator  (sync)
    │     ├── architect           (async, skill: code-architect)
    │     ├── security-analyst    (async, skill: code-security)
    │     ├── dependency-checker  (async, skill: code-dependency)
    │     ├── doc-reviewer        (async, skill: code-documentation)
    │     └── summarizer          (sync,  skill: report-writing)
    │
    └── prd-review-orchestrator   (sync)
          ├── product-strategist  (async, skill: review-ops)
          ├── technical-feasibility(async,skill: review-tech)
          ├── ux-researcher       (async, skill: review-ux)
          ├── risk-analyst        (async, skill: review-test)
          └── editor              (sync,  skill: report-writing + review-matrix)

Usage:
  conda activate llm-dev
  python demo/aperio_integrated.py
"""
from __future__ import annotations

import base64
import atexit
import io
import json
import os
import shlex
import sys
import tarfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_DEMO_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _DEMO_DIR.parent

from dotenv import load_dotenv
load_dotenv(_DEMO_DIR / ".env")

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend, CompositeBackend, StateBackend
from deepagents.backends.protocol import (
    EditResult,
    ExecuteResponse,
    FileData,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
)
from deepagents.backends.store import StoreBackend
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore
from langchain.chat_models import init_chat_model
from langchain.agents.middleware import AgentMiddleware
from langgraph.types import Command

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
WORKSPACE_ROOT = (_PROJECT_ROOT / "demo/workspace_integrated").resolve()
SKILLS_DIR = _DEMO_DIR / "04_skills"
TARGET_CODE = "full-stack-fastapi-template-master/backend/app/core"
SANDBOX_TARGET_CODE = "/workspace/code"


def _skill_dir(name: str) -> str:
    """Build absolute path to a skill directory (DeepAgents loads all SKILL.md within)."""
    return str((SKILLS_DIR / name).resolve())


# ---------------------------------------------------------------------------
# Docker Sandbox (Exercise 12 pattern)
# ---------------------------------------------------------------------------

class DockerSandbox:
    """DeepAgents-compatible Docker backend based on Exercise 12."""

    def __init__(
        self,
        image: str = "python:3.11-slim",
        working_dir: str = "/workspace",
        remove_on_close: bool = True,
    ):
        try:
            import docker
        except ImportError as exc:
            raise RuntimeError("docker Python package is required for sandbox mode") from exc

        self.image = image
        self.working_dir = working_dir
        self.remove_on_close = remove_on_close
        self.client = docker.from_env()
        self.container = self.client.containers.run(
            self.image,
            command="tail -f /dev/null",
            detach=True,
            working_dir=self.working_dir,
            remove=self.remove_on_close,
        )
        print(f"Docker sandbox: started {self.container.id[:12]} ({self.image})")

    def seed_directory(self, source: Path, destination: str) -> None:
        """Copy host directory into the container as demo input."""
        if not source.exists():
            raise FileNotFoundError(f"target code path not found: {source}")

        self.execute(f"rm -rf {shlex.quote(destination)} && mkdir -p {shlex.quote(destination)}")
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as tar:
            for item in source.rglob("*"):
                arcname = str(Path(destination.lstrip("/")) / item.relative_to(source))
                tar.add(item, arcname=arcname, recursive=False)
        stream.seek(0)
        ok = self.container.put_archive("/", stream.getvalue())
        if not ok:
            raise RuntimeError(f"failed to seed sandbox directory: {destination}")

    def execute(self, command: str) -> tuple[str, int]:
        if not self.container:
            raise RuntimeError("sandbox container is not running")
        exit_code, output = self.container.exec_run(
            ["sh", "-c", command],
            workdir=self.working_dir,
            demux=False,
        )
        output_str = output.decode("utf-8", errors="replace") if output else ""
        return output_str, exit_code

    def ls(self, path: str = ".") -> LsResult:
        try:
            stdout, exit_code = self.execute(f"ls -1 {shlex.quote(path)}")
            if exit_code != 0:
                return LsResult(error=f"cannot list directory: {path}", entries=[])
            entries = [{"path": f} for f in stdout.strip().split("\n") if f]
            return LsResult(entries=entries, error=None)
        except Exception as exc:
            return LsResult(error=str(exc), entries=[])

    def read(self, path: str, offset: int = 0, limit: Optional[int] = None) -> ReadResult:
        try:
            stdout, exit_code = self.execute(f"cat {shlex.quote(path)}")
            if exit_code != 0:
                return ReadResult(error=f"cannot read file: {path}")
            lines = stdout.splitlines(keepends=True)
            if offset > 0:
                lines = lines[offset:]
            if limit is not None:
                lines = lines[:limit]
            return ReadResult(file_data=FileData(content="".join(lines), encoding="utf-8"), error=None)
        except Exception as exc:
            return ReadResult(error=str(exc))

    def write(self, path: str, content: bytes | str) -> WriteResult:
        if isinstance(content, str):
            content = content.encode("utf-8")
        try:
            dir_path = os.path.dirname(path)
            if dir_path and dir_path != ".":
                self.execute(f"mkdir -p {shlex.quote(dir_path)}")
            content_b64 = base64.b64encode(content).decode("utf-8")
            stdout, exit_code = self.execute(
                f"printf %s {shlex.quote(content_b64)} | base64 -d > {shlex.quote(path)}"
            )
            if exit_code != 0:
                return WriteResult(error=f"write failed: {stdout}", path=None)
            return WriteResult(path=path, error=None)
        except Exception as exc:
            return WriteResult(error=str(exc), path=None)

    def edit(
        self,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
        instructions: Optional[str] = None,
    ) -> EditResult:
        try:
            read_result = self.read(path)
            if read_result.error:
                return EditResult(path=None, error=read_result.error)
            current = read_result.file_data["content"]
            updated = current.replace(old_string, new_string) if replace_all else current.replace(old_string, new_string, 1)
            if updated == current:
                return EditResult(path=path, error=None)
            write_result = self.write(path, updated)
            if write_result.error:
                return EditResult(path=None, error=write_result.error)
            return EditResult(path=path, error=None)
        except Exception as exc:
            return EditResult(path=None, error=str(exc))

    def glob(self, pattern: str, path: str = ".") -> GlobResult:
        try:
            stdout, exit_code = self.execute(
                f"find {shlex.quote(path)} -name {shlex.quote(pattern)} -type f"
            )
            if exit_code != 0:
                return GlobResult(error=f"glob failed: {stdout}", matches=[])
            return GlobResult(matches=[f for f in stdout.strip().split("\n") if f], error=None)
        except Exception as exc:
            return GlobResult(error=str(exc), matches=[])

    def grep(
        self,
        pattern: str,
        path: str = ".",
        recursive: bool = True,
        glob: Optional[str] = None,
    ) -> GrepResult:
        try:
            if glob:
                cmd = (
                    f"find {shlex.quote(path)} -name {shlex.quote(glob)} -type f "
                    f"-exec grep -n {shlex.quote(pattern)} {{}} \\; 2>/dev/null || true"
                )
            else:
                flag = "-rn" if recursive else "-n"
                cmd = f"grep {flag} {shlex.quote(pattern)} {shlex.quote(path)} 2>/dev/null || true"
            stdout, _ = self.execute(cmd)
            matches = []
            for line in stdout.strip().split("\n"):
                if not line or ":" not in line:
                    continue
                parts = line.split(":", 2)
                if len(parts) == 3:
                    file_path, line_num, content = parts
                    try:
                        matches.append({"path": file_path, "line": int(line_num), "text": content})
                    except ValueError:
                        continue
            return GrepResult(matches=matches, error=None)
        except Exception as exc:
            return GrepResult(error=str(exc), matches=[])

    def close(self) -> None:
        if self.container:
            container_id = self.container.id[:12]
            self.container.stop()
            if not self.remove_on_close:
                self.container.remove()
            self.container = None
            print(f"Docker sandbox: cleaned {container_id}")


class AgentDockerSandbox(SandboxBackendProtocol):
    """Adapter that exposes DockerSandbox through SandboxBackendProtocol."""

    def __init__(self, target_code: Path) -> None:
        self._sandbox = DockerSandbox()
        self._sandbox.seed_directory(target_code, SANDBOX_TARGET_CODE)

    @property
    def id(self) -> str:
        return self._sandbox.container.id if self._sandbox.container else "closed"

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        output, exit_code = self._sandbox.execute(command)
        return ExecuteResponse(output=output, exit_code=exit_code)

    def ls(self, path: str = ".") -> LsResult:
        return self._sandbox.ls(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return self._sandbox.read(file_path, offset=offset, limit=limit)

    def write(self, file_path: str, content: str) -> WriteResult:
        return self._sandbox.write(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return self._sandbox.edit(file_path, old_string, new_string, replace_all=replace_all)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        return self._sandbox.glob(pattern, path or ".")

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        return self._sandbox.grep(pattern, path or ".", glob=glob)

    def close(self) -> None:
        self._sandbox.close()


# ---------------------------------------------------------------------------
# Middleware (M6: Observability)
# ---------------------------------------------------------------------------

@dataclass
class Metrics:
    model_calls: int = 0
    model_time_ms: float = 0.0
    tool_calls: int = 0
    tool_time_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    events: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "model_calls": self.model_calls,
            "model_avg_ms": round(self.model_time_ms / max(1, self.model_calls), 1),
            "tool_calls": self.tool_calls,
            "tool_avg_ms": round(self.tool_time_ms / max(1, self.tool_calls), 1),
            "total_tokens": self.tokens_in + self.tokens_out,
            "events": self.events,
        }


class PerfMiddleware(AgentMiddleware):
    """Custom performance middleware — tracks model calls, tool calls, tokens, timing."""

    def __init__(self, m: Metrics):
        super().__init__()
        self.m = m

    def wrap_model_call(self, request, handler):
        self.m.model_calls += 1
        t0 = time.perf_counter()
        try:
            response = handler(request)
            dt = (time.perf_counter() - t0) * 1000
            self.m.model_time_ms += dt

            # Extract token usage from response
            actual = None
            if hasattr(response, 'result') and isinstance(response.result, list) and len(response.result) > 0:
                actual = response.result[0]
            elif hasattr(response, 'result'):
                actual = response.result
            if actual and hasattr(actual, 'usage_metadata'):
                usage = actual.usage_metadata or {}
                self.m.tokens_in += usage.get("input_tokens", 0)
                self.m.tokens_out += usage.get("output_tokens", 0)

            self.m.events.append({"type": "model", "ms": round(dt, 1)})
            return response
        except Exception:
            raise

    def wrap_tool_call(self, request, handler):
        self.m.tool_calls += 1
        t0 = time.perf_counter()
        try:
            result = handler(request)
            dt = (time.perf_counter() - t0) * 1000
            self.m.tool_time_ms += dt
            tool_name = getattr(request, 'tool', 'unknown') if hasattr(request, 'tool') else 'unknown'
            self.m.events.append({"type": "tool", "name": str(tool_name), "ms": round(dt, 1)})
            return result
        except Exception:
            raise


# ---------------------------------------------------------------------------
# Sub-agent definitions
# ---------------------------------------------------------------------------

def _code_health_subagents(ws: str, target: str) -> list:
    """Return the 5 sub-agents for code health orchestration."""
    return [
        {
            "name": "architect",
            "description": "Analyze code architecture: directory structure, module coupling, layering",
            "system_prompt": f"""你是代码架构师。分析 `{target}` 的架构：
- 目录结构是否合理、模块粒度是否合适
- 是否存在循环依赖、God Class
- 分层是否清晰（API → 业务 → 数据）
将分析结果写入 {ws}/drafts/architect.md，最后输出中文摘要。""",
            "skills": [_skill_dir("code-health/code-architect")],
        },
        {
            "name": "security-analyst",
            "description": "Audit code for vulnerabilities: SQL injection, XSS, hardcoded secrets, OWASP Top-10",
            "system_prompt": f"""你是应用安全工程师。审计 `{target}` 的安全：
- SQL 注入、XSS、硬编码密钥
- 不安全反序列化、路径遍历
- 敏感端点认证缺失
将分析结果写入 {ws}/drafts/security.md，最后输出中文摘要。""",
            "skills": [_skill_dir("code-health/code-security")],
        },
        {
            "name": "dependency-checker",
            "description": "Check dependencies: outdated versions, CVEs, license compatibility",
            "system_prompt": f"""你是依赖管理专家。检查 `{target}` 的依赖：
- 主版本落后的包、已知 CVE
- 许可证兼容性、未声明的传递依赖
将分析结果写入 {ws}/drafts/dependencies.md，最后输出中文摘要。""",
            "skills": [_skill_dir("code-health/code-dependency")],
        },
        {
            "name": "doc-reviewer",
            "description": "Review documentation: README, docstrings, API docs coverage",
            "system_prompt": f"""你是技术文档专家。评估 `{target}` 的文档：
- README 完整性、公开 API docstring 覆盖率
- 注释质量、配置说明
将分析结果写入 {ws}/drafts/documentation.md，最后输出中文摘要。""",
            "skills": [_skill_dir("code-health/code-documentation")],
        },
        {
            "name": "summarizer",
            "description": "Merge 4 analysis reports into a consolidated code health report",
            "system_prompt": f"""你是报告汇总专家。任务：
1. ls {ws}/drafts/ 发现所有分析报告
2. read_file 逐个读取
3. 去重合并 → 按严重度分级（Critical/High/Medium/Low）
4. 计算健康度评分（0-100）
5. 将最终报告写入 {ws}/code_health_report.md
报告使用中文，包含执行摘要和趋势对比（如有历史数据）。""",
            "skills": [_skill_dir("general/report-writing")],
        },
    ]


def _prd_review_subagents(ws: str) -> list:
    """Return the 5 sub-agents for PRD review orchestration (writer is the orchestrator itself)."""
    return [
        {
            "name": "product-strategist",
            "description": "Review PRD from strategy perspective: market, business value, differentiation",
            "system_prompt": f"""你是产品策略分析师。评审 PRD：
- 市场定位是否清晰、是否有明确竞品
- 商业价值和差异化在哪里
- MVP 功能优先级是否合理
读取 {ws}/prd_v1.md，将评审写入 {ws}/drafts/review_strategy.md，输出中文摘要。""",
            "skills": [_skill_dir("prd-review/review-ops")],
        },
        {
            "name": "technical-feasibility",
            "description": "Review PRD from tech perspective: stack, architecture, integration risks",
            "system_prompt": f"""你是技术架构师。评审 PRD：
- 技术栈是否可行、是否需要大规模重构
- API 设计是否清晰、性能预期是否合理
- 是否有安全隐患
读取 {ws}/prd_v1.md，将评审写入 {ws}/drafts/review_tech.md，输出中文摘要。""",
            "skills": [_skill_dir("prd-review/review-tech")],
        },
        {
            "name": "ux-researcher",
            "description": "Review PRD from UX perspective: user journey, edge cases, accessibility",
            "system_prompt": f"""你是 UX 研究员。评审 PRD：
- 用户操作路径是否最短、异常状态是否覆盖
- 交互一致性、无障碍、信息架构
读取 {ws}/prd_v1.md，将评审写入 {ws}/drafts/review_ux.md，输出中文摘要。""",
            "skills": [_skill_dir("prd-review/review-ux")],
        },
        {
            "name": "risk-analyst",
            "description": "Review PRD from risk perspective: timeline, resources, privacy, adoption barriers",
            "system_prompt": f"""你是项目风险分析师。评审 PRD：
- 时间线和资源风险、团队能力匹配
- 数据隐私合规、用户采纳障碍
读取 {ws}/prd_v1.md，将评审写入 {ws}/drafts/review_risk.md，输出中文摘要。""",
            "skills": [_skill_dir("prd-review/review-test")],
        },
        {
            "name": "editor",
            "description": "Merge all reviews into a final polished PRD v2 with review matrix",
            "system_prompt": f"""你是 PRD 编辑。任务：
1. ls {ws}/drafts/ 发现所有评审报告
2. read_file 逐个读取 + 读取 {ws}/prd_v1.md
3. 综合反馈，生成修订版 PRD v2 → {ws}/prd_v2_final.md
4. 生成评审矩阵 → {ws}/review_matrix.md
   （格式：序号 | 维度 | 严重度 | 问题 | 建议 | 状态）
报告使用中文。""",
            "skills": [_skill_dir("general/report-writing"), _skill_dir("general/review-matrix")],
        },
    ]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    # ---- LangSmith ----
    # ls_key = os.environ.get("LANGSMITH_API_KEY", "")
    # if ls_key:
    #     os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    #     os.environ.setdefault("LANGCHAIN_PROJECT", "aperio-integrated")
    #     print("LangSmith: ✅ enabled")
    # else:
    #     print("LangSmith: ⚠️  not configured")

    print("=" * 60)
    print("Aperio Integrated Demo")
    print("=" * 60)

    # ---- Validate ----
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("ERROR: DEEPSEEK_API_KEY not found")
        return
    target_path = (_PROJECT_ROOT / TARGET_CODE).resolve()
    if not target_path.exists():
        print(f"ERROR: target '{TARGET_CODE}' not found")
        return

    run_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    run_root = WORKSPACE_ROOT / run_id
    code_ws = "/outputs/code_health"
    prd_ws = "/outputs/prd_review"
    (run_root / "code_health" / "drafts").mkdir(parents=True, exist_ok=True)
    (run_root / "prd_review" / "drafts").mkdir(parents=True, exist_ok=True)

    # ---- Backend (M4 + M5) ----
    store = InMemoryStore()
    sandbox: AgentDockerSandbox | None = None
    try:
        sandbox = AgentDockerSandbox(target_path)
    except Exception as exc:
        print(f"ERROR: Docker sandbox unavailable: {exc}")
        print("Run Docker Desktop, then rerun this demo in the llm-dev environment.")
        return
    atexit.register(sandbox.close)

    backend = CompositeBackend(
        default=sandbox,
        routes={
            "/outputs/": FilesystemBackend(root_dir=str(run_root), virtual_mode=True),
            "/memories/": StoreBackend(store=store, namespace=lambda rt: ("aperio",)),
            "/temp/": StateBackend(),
        },
    )
    checkpointer = MemorySaver()

    # ---- Model ----
    model = init_chat_model(
        model="openai:deepseek-v4-flash",
        api_key=api_key,
        base_url="https://api.deepseek.com",
    )

    # ---- Middleware ----
    metrics = Metrics()
    perf = PerfMiddleware(metrics)

    # ---- Main Router Agent ----
    agent = create_deep_agent(
        model=model,
        backend=backend,
        checkpointer=checkpointer,
        middleware=[perf],
        interrupt_on={
            "execute": {"allowed_decisions": ["approve", "reject"]},
            "write_file": {"allowed_decisions": ["approve", "reject"]},
        },
        system_prompt="""你是 Aperio 研发质量平台的**任务路由器**。根据用户输入判断任务类型：

- 如果用户要求**分析代码、代码体检、代码健康检查**→ 委托给 code-health-orchestrator
- 如果用户要求**写 PRD、评审需求、产品需求文档**→ 委托给 prd-review-orchestrator

你的职责只是识别任务类型并路由到正确的 Orchestrator，不要自己做分析。""",
        subagents=[
            {
                "name": "code-health-orchestrator",
                "description": "Orchestrate a code health scan: spawn 4 parallel analysis sub-agents then merge results",
                "system_prompt": f"""你是代码健康检查编排器（Code Health Orchestrator）。工作流程：

1. 使用 write_todos 规划任务
2. 并行派发 4 个子代理分析 {SANDBOX_TARGET_CODE}：
   - architect：架构分析
   - security-analyst：安全审计
   - dependency-checker：依赖检查
   - doc-reviewer：文档评估
   每个子代理将结果写入 {code_ws}/drafts/
3. 等待全部 4 个完成后，派发 summarizer 合并生成最终报告
4. 如有 /memories/history/ 中的历史数据，进行趋势对比""",
                "subagents": _code_health_subagents(code_ws, SANDBOX_TARGET_CODE),
            },
            {
                "name": "prd-review-orchestrator",
                "description": "Orchestrate PRD review: write a PRD, spawn 4 parallel reviewers, then editor merges feedback",
                "system_prompt": f"""你是 PRD 评审编排器（PRD Review Orchestrator）。工作流程：

1. 根据用户需求，自己作为 Writer 编写 PRD 初稿 → {prd_ws}/prd_v1.md
   包含：产品概述、用户画像、核心功能（P0/P1/P2）、用户故事、成功指标、非功能需求
2. 并行派发 4 个评审子代理：
   - product-strategist：产品策略
   - technical-feasibility：技术可行性
   - ux-researcher：用户体验
   - risk-analyst：风险评估
   每个子代理读取 {prd_ws}/prd_v1.md，将评审写入 {prd_ws}/drafts/
3. 等待全部 4 个完成后，派发 editor 合并生成 PRD v2 + 评审矩阵

PRD 使用中文撰写。""",
                "subagents": _prd_review_subagents(prd_ws),
            },
        ],
    )

    # ---- Run: Code Health ----
    print(f"\n{'─' * 60}")
    print("Task 1: Code Health Scan")
    print(f"Target: {TARGET_CODE}")
    print(f"{'─' * 60}")

    t0 = time.time()
    code_config = {"configurable": {"thread_id": f"{run_id}:code-health"}}
    resp = agent.invoke(
        {
            "messages": [{
                "role": "user",
                "content": (
                    f"请对我的代码库 `{TARGET_CODE}` 进行完整的代码健康检查。"
                    f"使用 code-health-orchestrator 执行全流程。"
                ),
            }],
        },
        config=code_config,
        version="v2",
    )
    # HITL: wait for human approval
    while hasattr(resp, '__iter__') and hasattr(resp, 'interrupts') and resp.interrupts:
        decisions = []
        for interrupt in resp.interrupts:
            for action in interrupt.value.get("action_requests", []):
                print(f"\n  ⏸️  HITL: Agent 请求执行 [{action['name']}]")
                if action['name'] == 'execute':
                    print(f"     命令: {action['args'].get('command', 'N/A')}")
                elif action['name'] == 'write_file':
                    print(f"     文件: {action['args'].get('path', 'N/A')}")
                    content = action['args'].get('content', '')
                    print(f"     内容: {str(content)[:200]}...")
                choice = input("  [a]pprove / [r]eject: ").strip().lower()
                decisions.append({
                    "action_id": action.get("id"),
                    "tool_name": action["name"],
                    "type": "approve" if choice == 'a' else "reject",
                    "updated_args": action["args"] if choice == 'a' else None,
                })
        resp = agent.invoke(Command(resume={"decisions": decisions}), config=code_config, version="v2")
    t_code = time.time() - t0

    # ---- Run: PRD Review ----
    print(f"\n{'─' * 60}")
    print("Task 2: PRD Review")
    print(f"{'─' * 60}")

    t1 = time.time()
    prd_config = {"configurable": {"thread_id": f"{run_id}:prd-review"}}
    resp = agent.invoke(
        {
            "messages": [{
                "role": "user",
                "content": (
                    "我需要为「智慧校园导航助手」写一份 PRD 并评审。"
                    "这是一款基于 AR/语音的校内导航 App，帮助新生和访客快速找到教室和设施，"
                    "同时提供校园活动推荐和实时拥挤度信息。"
                    "请使用 prd-review-orchestrator 执行全流程。"
                ),
            }],
        },
        config=prd_config,
        version="v2",
    )
    while hasattr(resp, '__iter__') and hasattr(resp, 'interrupts') and resp.interrupts:
        decisions = []
        for interrupt in resp.interrupts:
            for action in interrupt.value.get("action_requests", []):
                print(f"\n  ⏸️  HITL: Agent 请求执行 [{action['name']}]")
                if action['name'] == 'execute':
                    print(f"     命令: {action['args'].get('command', 'N/A')}")
                elif action['name'] == 'write_file':
                    print(f"     文件: {action['args'].get('path', 'N/A')}")
                    content = action['args'].get('content', '')
                    print(f"     内容: {str(content)[:200]}...")
                choice = input("  [a]pprove / [r]eject: ").strip().lower()
                decisions.append({
                    "action_id": action.get("id"),
                    "tool_name": action["name"],
                    "type": "approve" if choice == 'a' else "reject",
                    "updated_args": action["args"] if choice == 'a' else None,
                })
        resp = agent.invoke(Command(resume={"decisions": decisions}), config=prd_config, version="v2")
    t_prd = time.time() - t1

    # ---- Output ----
    print(f"\n{'=' * 60}")

    # Performance
    m = metrics.to_dict()
    print(f"📊 Performance:")
    print(f"   Code Health:   {t_code:.1f}s")
    print(f"   PRD Review:    {t_prd:.1f}s")
    print(f"   Total wall:    {t_code + t_prd:.1f}s")
    print(f"   Model calls:   {m['model_calls']} (avg {m['model_avg_ms']}ms)")
    print(f"   Tool calls:    {m['tool_calls']} (avg {m['tool_avg_ms']}ms)")
    print(f"   Total tokens:  {m['total_tokens']}")

    # Save perf
    (run_root / "performance.json").write_text(
        json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")

    # Output files
    print(f"\n📁 Workspace: {run_root}/")
    for f in sorted(run_root.rglob("*")):
        if f.is_file():
            print(f"   {f.relative_to(run_root)} ({f.stat().st_size} bytes)")

    # Coverage
    print(f"\n📋 Module Coverage:")
    print(f"   M1 ✅ Multi-subagent: Router → 2 orchestrators × (4 async + 1 sync)")
    print(f"   M2 ✅ Skills: 11 custom SKILL.md loaded via 'skills' field")
    print(f"   M3 ✅ Context: Write/Select in orchestrator prompts")
    print(f"   M4 ✅ Memory: StoreBackend /memories/")
    print(f"   M5 ✅ Security: DockerSandbox default backend + HITL execute/write_file")
    print(f"   M6 ✅ Observability: PerfMiddleware {'+ LangSmith' if ls_key else '(LangSmith latent)'}")
    print(f"   M7 ✅ Output: code_health_report.md + prd_v2_final.md + review_matrix.md")

    sandbox.close()
    print(f"\n✅ Aperio Integrated Demo complete!")


if __name__ == "__main__":
    main()
