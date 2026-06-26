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
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

_DEMO_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _DEMO_DIR.parent

from dotenv import load_dotenv
load_dotenv(_DEMO_DIR / ".env")

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend, CompositeBackend
from deepagents.backends.store import StoreBackend
from langgraph.store.memory import InMemoryStore
from langchain.chat_models import init_chat_model

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
WORKSPACE = str((_PROJECT_ROOT / "demo/workspace_integrated").resolve())
SKILLS_DIR = _DEMO_DIR / "04_skills"
TARGET_CODE = "full-stack-fastapi-template-master/backend/app/core"


def _skill_dir(name: str) -> str:
    """Build absolute path to a skill directory (DeepAgents loads all SKILL.md within)."""
    return str((SKILLS_DIR / name).resolve())


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


class PerfMiddleware:
    def __init__(self, m: Metrics):
        self.m = m

    def wrap_model_call(self, call, *args, **kwargs):
        t0 = time.perf_counter()
        try:
            result = call(*args, **kwargs)
            dt = (time.perf_counter() - t0) * 1000
            usage = getattr(result, "usage_metadata", {}) or {}
            self.m.model_calls += 1
            self.m.model_time_ms += dt
            self.m.tokens_in += usage.get("input_tokens", 0)
            self.m.tokens_out += usage.get("output_tokens", 0)
            self.m.events.append({"type": "model", "ms": round(dt, 1)})
            return result
        except Exception:
            raise

    def wrap_tool_call(self, call, tool_name, *args, **kwargs):
        t0 = time.perf_counter()
        try:
            result = call(*args, **kwargs)
            dt = (time.perf_counter() - t0) * 1000
            self.m.tool_calls += 1
            self.m.tool_time_ms += dt
            self.m.events.append({"type": "tool", "name": tool_name, "ms": round(dt, 1)})
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
    ls_key = os.environ.get("LANGSMITH_API_KEY", "")
    if ls_key:
        os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
        os.environ.setdefault("LANGCHAIN_PROJECT", "aperio-integrated")
        print("LangSmith: ✅ enabled")
    else:
        print("LangSmith: ⚠️  not configured")

    print("=" * 60)
    print("Aperio Integrated Demo")
    print("=" * 60)

    # ---- Validate ----
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("ERROR: DEEPSEEK_API_KEY not found")
        return
    if not (_PROJECT_ROOT / TARGET_CODE).exists():
        print(f"WARNING: target '{TARGET_CODE}' not found")

    Path(WORKSPACE).mkdir(parents=True, exist_ok=True)
    Path(WORKSPACE, "drafts").mkdir(exist_ok=True)

    # ---- Backend (M4 + M5) ----
    store = InMemoryStore()
    backend = CompositeBackend(
        default=FilesystemBackend(root_dir=str(_PROJECT_ROOT), virtual_mode=True),
        routes={
            r"/memories/": StoreBackend(store=store, namespace=lambda rt: ("aperio",)),
        },
    )

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
        middleware=[perf],
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
2. 并行派发 4 个子代理分析 {TARGET_CODE}：
   - architect：架构分析
   - security-analyst：安全审计
   - dependency-checker：依赖检查
   - doc-reviewer：文档评估
   每个子代理将结果写入 {WORKSPACE}/drafts/
3. 等待全部 4 个完成后，派发 summarizer 合并生成最终报告
4. 如有 /memories/history/ 中的历史数据，进行趋势对比""",
                "subagents": _code_health_subagents(WORKSPACE, TARGET_CODE),
            },
            {
                "name": "prd-review-orchestrator",
                "description": "Orchestrate PRD review: write a PRD, spawn 4 parallel reviewers, then editor merges feedback",
                "system_prompt": f"""你是 PRD 评审编排器（PRD Review Orchestrator）。工作流程：

1. 根据用户需求，自己作为 Writer 编写 PRD 初稿 → {WORKSPACE}/prd_v1.md
   包含：产品概述、用户画像、核心功能（P0/P1/P2）、用户故事、成功指标、非功能需求
2. 并行派发 4 个评审子代理：
   - product-strategist：产品策略
   - technical-feasibility：技术可行性
   - ux-researcher：用户体验
   - risk-analyst：风险评估
   每个子代理读取 {WORKSPACE}/prd_v1.md，将评审写入 {WORKSPACE}/drafts/
3. 等待全部 4 个完成后，派发 editor 合并生成 PRD v2 + 评审矩阵

PRD 使用中文撰写。""",
                "subagents": _prd_review_subagents(WORKSPACE),
            },
        ],
    )

    # ---- Run: Code Health ----
    print(f"\n{'─' * 60}")
    print("Task 1: Code Health Scan")
    print(f"Target: {TARGET_CODE}")
    print(f"{'─' * 60}")

    t0 = time.time()
    _ = agent.invoke({
        "messages": [{
            "role": "user",
            "content": (
                f"请对我的代码库 `{TARGET_CODE}` 进行完整的代码健康检查。"
                f"使用 code-health-orchestrator 执行全流程。"
            ),
        }],
    })
    t_code = time.time() - t0

    # ---- Run: PRD Review ----
    print(f"\n{'─' * 60}")
    print("Task 2: PRD Review")
    print(f"{'─' * 60}")

    t1 = time.time()
    _ = agent.invoke({
        "messages": [{
            "role": "user",
            "content": (
                "我需要为「智慧校园导航助手」写一份 PRD 并评审。"
                "这是一款基于 AR/语音的校内导航 App，帮助新生和访客快速找到教室和设施，"
                "同时提供校园活动推荐和实时拥挤度信息。"
                "请使用 prd-review-orchestrator 执行全流程。"
            ),
        }],
    })
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
    (Path(WORKSPACE) / "performance.json").write_text(
        json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")

    # Output files
    print(f"\n📁 Workspace: {WORKSPACE}/")
    for f in sorted(Path(WORKSPACE).rglob("*")):
        if f.is_file():
            print(f"   {f.relative_to(WORKSPACE)} ({f.stat().st_size} bytes)")

    # Coverage
    print(f"\n📋 Module Coverage:")
    print(f"   M1 ✅ Multi-subagent: Router → 2 orchestrators × (4 async + 1 sync)")
    print(f"   M2 ✅ Skills: 11 custom SKILL.md loaded via 'skills' field")
    print(f"   M3 ✅ Context: Write/Select in orchestrator prompts")
    print(f"   M4 ✅ Memory: StoreBackend /memories/")
    print(f"   M5 ✅ Security: virtual_mode=True")
    print(f"   M6 ✅ Observability: PerfMiddleware {'+ LangSmith' if ls_key else '(LangSmith latent)'}")
    print(f"   M7 ✅ Output: code_health_report.md + prd_v2_final.md + review_matrix.md")

    print(f"\n✅ Aperio Integrated Demo complete!")


if __name__ == "__main__":
    main()
