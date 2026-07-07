from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from deepagents.backends.store import StoreBackend
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ModelFallbackMiddleware,
    ModelRetryMiddleware,
    ToolCallLimitMiddleware,
    ToolRetryMiddleware,
)
from langchain.chat_models import init_chat_model
from langgraph.store.memory import InMemoryStore
from langgraph.checkpoint.memory import MemorySaver

from .config import (
    get_api_key,
    get_base_url,
    get_fallback_model_name,
    get_model_call_limit,
    get_model_max_retries,
    get_model_name,
    get_tool_call_limit,
    get_tool_max_retries,
)
from .hitl import build_interrupt_policy, resolve_human_interrupts
from .event_protocol import normalize_event
from .middleware import (
    FinalOutputGuardMiddleware,
    RouterToolGuardMiddleware,
    ToolAllowlistMiddleware,
    UploadedBinaryReadGuardMiddleware,
)
from .mcp_tools import load_mcp_tools
from .observability import RunTelemetry, TelemetryMiddleware
from .policy import write_local_policy
from .resources import copy_packaged_skills
from .skill_backend import AgentSkillBackend, AgentSkillSources


def run_deep_agent(
    message: str,
    run_root: Path,
    *,
    input_bundle: dict[str, Any],
    code_scan_summary: dict[str, Any] | None = None,
    approval_mode: str = "approve",
    cancel_event: Any | None = None,
    event_callback: Any | None = None,
) -> str:
    """Run the package-native DeepAgents implementation.

    The production backend never imports demo/aperio_integrated.py. It reuses
    the demo's useful contracts: normalized inputs, packaged skills, code-health
    evidence, PRD review outputs, and a pure routing agent.
    """
    run_root.mkdir(parents=True, exist_ok=True)
    _emit_event(event_callback, {"type": "phase", "phase": "deepagents_start", "message": "Start DeepAgents router"})
    skills_root = copy_packaged_skills(run_root)
    _write_input_files(run_root, message, input_bundle, code_scan_summary)
    write_local_policy(run_root, input_bundle)
    if _is_cancelled(cancel_event):
        return "本次运行已停止。"

    skill_sources = AgentSkillSources(skills_root)
    store = InMemoryStore()
    backend = CompositeBackend(
        default=FilesystemBackend(root_dir=str(run_root), virtual_mode=True),
        routes={
            "/inputs/": FilesystemBackend(root_dir=str(run_root / "inputs"), virtual_mode=True),
            "/outputs/": FilesystemBackend(root_dir=str(run_root / "outputs"), virtual_mode=True),
            "/local-resources/": FilesystemBackend(root_dir=str(run_root / "local-resources"), virtual_mode=True),
            "/skills/": FilesystemBackend(root_dir=str(skills_root), virtual_mode=True),
            "/agent-skills/": AgentSkillBackend(skill_sources),
            "/memories/": StoreBackend(store=store, namespace=lambda _runtime: ("aperio",)),
            "/temp/": StateBackend(),
        },
    )
    model = _model()
    fallback_model = _fallback_model()
    checkpointer = MemorySaver()
    telemetry = RunTelemetry()
    mcp_toolset = load_mcp_tools(run_root)
    shared_tools = mcp_toolset.shared
    general_tools = [*mcp_toolset.shared, *mcp_toolset.general_purpose]
    interrupt_on = _interrupt_policy_for_mode(approval_mode)
    router_prompt = _router_prompt()
    code_health_final_paths: set[str] = set()
    prd_review_final_paths: set[str] = set()
    permissions = [
        FilesystemPermission(operations=["write"], paths=["/inputs/**"], mode="deny"),
        FilesystemPermission(operations=["write"], paths=["/local-resources/**"], mode="deny"),
        FilesystemPermission(operations=["write"], paths=["/skills/**"], mode="deny"),
        FilesystemPermission(operations=["write"], paths=["/agent-skills/**"], mode="deny"),
    ]

    code_health_agent = _compiled_code_health_agent(
        model,
        backend,
        checkpointer,
        permissions,
        interrupt_on,
        telemetry,
        skill_sources,
        fallback_model,
        code_health_final_paths,
        event_callback,
    )
    prd_agent = _compiled_prd_agent(
        model,
        backend,
        checkpointer,
        permissions,
        interrupt_on,
        shared_tools,
        telemetry,
        skill_sources,
        fallback_model,
        prd_review_final_paths,
        event_callback,
    )
    general_agent = _compiled_general_agent(
        model,
        backend,
        checkpointer,
        permissions,
        interrupt_on,
        general_tools,
        telemetry,
        skill_sources,
        fallback_model,
        event_callback,
    )

    router = create_deep_agent(
        model=model,
        tools=[],
        backend=backend,
        checkpointer=checkpointer,
        middleware=[
            *_runtime_middleware(fallback_model, retry_tools=set()),
            RouterToolGuardMiddleware(router_prompt),
            TelemetryMiddleware(telemetry, "aperio-router", event_callback=event_callback),
        ],
        permissions=permissions,
        interrupt_on=interrupt_on,
        system_prompt=router_prompt,
        subagents=[code_health_agent, prd_agent, general_agent],
        name="aperio-router",
    )

    runtime_notes = ""
    if shared_tools or general_tools:
        runtime_notes += "- MCP tools are enabled. Use internet_search only when public evidence is useful and save evidence under /outputs when instructed.\n"
    if mcp_toolset.errors:
        runtime_notes += "- MCP load errors: " + " | ".join(mcp_toolset.errors) + "\n"
    if input_bundle.get("attachments"):
        runtime_notes += (
            "- User uploaded files are listed in /inputs/input_bundle.json and stored under /inputs/uploads. "
            "Images, PDFs, Office files, archives, and other binary files cannot be visually or binary-inspected by the current text-only model endpoint; "
            "do not call read_file on those binary paths unless a text extraction tool is available.\n"
        )
    if (input_bundle.get("persistent_memory") or {}).get("enabled"):
        runtime_notes += "- Persistent memory is available at /inputs/persistent_memory.md. Use it as background context, but do not treat old memories as current facts without checking dates.\n"

    config = {"configurable": {"thread_id": run_root.name}}
    response = router.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"{message}\n\n"
                        "运行时输入：\n"
                        "- /inputs/input_bundle.json 是标准化输入包。\n"
                        "- /inputs/user_request.md 是用户原始请求。\n"
                        "- /local-resources/aperio_policy.yaml 是本次运行的安全、存储、输出策略。\n"
                        "- 如果是代码健康任务，/outputs/code_health/raw/tool_results.json 是后端扫描器生成的确定性证据。\n\n"
                        f"{runtime_notes}"
                        "请只通过 task 委托给合适的子 agent，完成后用中文简要返回结果和产物路径。"
                    ),
                }
            ]
        },
        config=config,
    )
    response = resolve_human_interrupts(router, response, config, approval_mode=approval_mode)
    if _is_cancelled(cancel_event):
        return "本次运行已停止。"
    _write_observability(run_root, telemetry, mcp_toolset.errors)
    _emit_event(event_callback, {"type": "phase", "phase": "observability_written", "message": "Observability data written"})
    return _extract_final_answer(response) or "Agent run completed."


def _is_cancelled(cancel_event: Any | None) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def _interrupt_policy_for_mode(approval_mode: str) -> dict[str, Any]:
    mode = (approval_mode or "approve").strip().lower()
    if mode == "approve":
        return {}
    return build_interrupt_policy()


def _emit_event(event_callback: Any | None, event: dict[str, Any]) -> None:
    if event_callback is None:
        return
    try:
        event_callback(normalize_event(event))
    except Exception:
        return


def _model():
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY。请运行 `aperio init` 并填写 ~/.aperio/.env。")
    return init_chat_model(
        model=get_model_name(),
        api_key=api_key,
        base_url=get_base_url(),
    )


def _fallback_model():
    fallback_name = get_fallback_model_name()
    if not fallback_name or fallback_name == get_model_name():
        return None
    api_key = get_api_key()
    if not api_key:
        return None
    return init_chat_model(
        model=fallback_name,
        api_key=api_key,
        base_url=get_base_url(),
    )


def _runtime_middleware(fallback_model: Any | None, retry_tools: set[str] | None = None) -> list[Any]:
    middleware: list[Any] = [
        ModelCallLimitMiddleware(
            run_limit=get_model_call_limit(),
            exit_behavior="end",
        ),
        ModelRetryMiddleware(
            max_retries=get_model_max_retries(),
            initial_delay=1.0,
            max_delay=8.0,
            on_failure="error",
        ),
    ]
    if fallback_model is not None:
        middleware.append(ModelFallbackMiddleware(fallback_model))
    middleware.append(UploadedBinaryReadGuardMiddleware())
    if retry_tools is None:
        retry_tools = {"read_file", "internet_search"}
    if retry_tools:
        middleware.append(
            ToolRetryMiddleware(
                tools=sorted(retry_tools),
                max_retries=get_tool_max_retries(),
                initial_delay=0.5,
                max_delay=2.0,
                on_failure="continue",
            )
        )
    middleware.append(
        ToolCallLimitMiddleware(
            run_limit=get_tool_call_limit(),
            exit_behavior="continue",
        )
    )
    return middleware


def _write_input_files(
    run_root: Path,
    message: str,
    input_bundle: dict[str, Any],
    code_scan_summary: dict[str, Any] | None,
) -> None:
    inputs_dir = run_root / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    (inputs_dir / "user_request.md").write_text(message.strip() + "\n", encoding="utf-8")
    (inputs_dir / "input_bundle.json").write_text(
        json.dumps(input_bundle, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (inputs_dir / "persistent_memory.md").write_text(
        str((input_bundle.get("persistent_memory") or {}).get("markdown") or "No persistent memory has been recorded yet.") + "\n",
        encoding="utf-8",
    )
    (inputs_dir / "code_scan_summary.json").write_text(
        json.dumps(code_scan_summary or {}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _compiled_code_health_agent(
    model,
    backend,
    checkpointer,
    permissions,
    interrupt_on,
    telemetry: RunTelemetry,
    skill_sources: AgentSkillSources,
    fallback_model: Any | None,
    completed_paths: set[str],
    event_callback: Any | None,
) -> dict[str, Any]:
    agent = create_deep_agent(
        model=model,
        tools=[],
        backend=backend,
        checkpointer=checkpointer,
        middleware=[
            *_runtime_middleware(fallback_model, retry_tools={"read_file"}),
            ToolAllowlistMiddleware({"read_file", "write_file", "task", "write_todos"}, "code-health orchestrator"),
            FinalOutputGuardMiddleware(
                {"/outputs/code_health/code_health_report.md"},
                "code-health orchestrator",
                completed_paths,
            ),
            TelemetryMiddleware(telemetry, "code-health-orchestrator", event_callback=event_callback),
        ],
        permissions=permissions,
        interrupt_on=interrupt_on,
        skills=skill_sources.source("code-health-orchestrator", "code-health/code-health-toolkit"),
        system_prompt=_code_health_prompt(),
        subagents=_code_health_reviewers(telemetry, skill_sources, fallback_model, completed_paths, event_callback),
        name="code-health-orchestrator",
    )
    return {
        "name": "code-health-orchestrator",
        "description": "代码健康编排器：基于后端扫描证据，组织架构、安全、依赖、文档和汇总审查，生成中文代码健康报告。",
        "runnable": agent,
    }


def _compiled_prd_agent(
    model,
    backend,
    checkpointer,
    permissions,
    interrupt_on,
    web_tools: list[Any],
    telemetry: RunTelemetry,
    skill_sources: AgentSkillSources,
    fallback_model: Any | None,
    completed_paths: set[str],
    event_callback: Any | None,
) -> dict[str, Any]:
    web_tool_names = {str(getattr(tool, "name", "")) for tool in web_tools}
    agent = create_deep_agent(
        model=model,
        tools=web_tools,
        backend=backend,
        checkpointer=checkpointer,
        middleware=[
            *_runtime_middleware(fallback_model, retry_tools={"read_file", "internet_search", *web_tool_names}),
            ToolAllowlistMiddleware(
                {"read_file", "write_file", "internet_search", "task", "write_todos", *web_tool_names},
                "PRD review orchestrator",
            ),
            FinalOutputGuardMiddleware(
                {"/outputs/prd_review/prd_v2_final.md", "/outputs/prd_review/review_matrix.md"},
                "PRD review orchestrator",
                completed_paths,
            ),
            TelemetryMiddleware(telemetry, "prd-review-orchestrator", event_callback=event_callback),
        ],
        permissions=permissions,
        interrupt_on=interrupt_on,
        skills=skill_sources.source(
            "prd-review-orchestrator",
            "prd-review/prd-writing",
            "shared/web-search",
        ),
        system_prompt=_prd_prompt(),
        subagents=_prd_reviewers(web_tools, telemetry, skill_sources, fallback_model, completed_paths, event_callback),
        name="prd-review-orchestrator",
    )
    return {
        "name": "prd-review-orchestrator",
        "description": "PRD 编排器：从用户需求生成 PRD v1，组织产品、技术、UX、风险评审，输出 PRD v2 和评审矩阵。",
        "runnable": agent,
    }


def _compiled_general_agent(
    model,
    backend,
    checkpointer,
    permissions,
    interrupt_on,
    tools: list[Any],
    telemetry: RunTelemetry,
    skill_sources: AgentSkillSources,
    fallback_model: Any | None,
    event_callback: Any | None,
) -> dict[str, Any]:
    tool_names = {str(getattr(tool, "name", "")) for tool in tools}
    agent = create_deep_agent(
        model=model,
        tools=tools,
        backend=backend,
        checkpointer=checkpointer,
        middleware=[
            *_runtime_middleware(fallback_model, retry_tools={"read_file", *tool_names}),
            ToolAllowlistMiddleware({"read_file", *tool_names}, "general-purpose"),
            TelemetryMiddleware(telemetry, "general-purpose", event_callback=event_callback),
        ],
        permissions=permissions,
        interrupt_on=interrupt_on,
        skills=skill_sources.source("general-purpose", "shared/web-search"),
        system_prompt=_general_prompt(),
        name="general-purpose",
    )
    return {
        "name": "general-purpose",
        "description": "通用问答、解释、写作、翻译和不需要产物的普通任务。",
        "runnable": agent,
    }


def _router_prompt() -> str:
    return """你是 Aperio 的 Main Router。你的唯一职责是判断用户请求类型，然后使用 task 工具委托给合适的 agent。

可用 agent：
- code-health-orchestrator：用户要求代码体检、代码质量审查、仓库扫描、架构/安全/依赖/文档审查时使用。
- prd-review-orchestrator：用户要求写 PRD、需求文档、产品评审、评审矩阵、完善产品需求时使用。
- general-purpose：普通问答、解释、翻译、写作、头脑风暴，以及不明确属于前两类的请求。

路由规则：
- 你只能调用 task，不要自己读文件、写文件、搜索或分析代码。
- 如果用户同时明确要求 PRD 和代码健康，分别委托两个专用 agent。
- 委托时原样传递用户请求，并说明 /inputs/input_bundle.json 可用。
- 不要输出 demo 调试信息，不要提及 demo/aperio_integrated.py。
- 子 agent 完成后，用中文简要告诉用户结果和产物路径。"""


def _code_health_prompt() -> str:
    return """你是 Aperio 的代码健康编排器。

输入：
- /inputs/user_request.md：用户原始请求。
- /inputs/input_bundle.json：标准化输入包，包含项目路径、目标路径和运行上下文。
- /local-resources/aperio_policy.yaml：安全、工具、存储和最终产物策略。
- /outputs/code_health/raw/tool_results.json：后端已执行的确定性扫描结果。

工作流：
1. 先读取 /local-resources/aperio_policy.yaml、/outputs/code_health/raw/tool_results.json 和 /inputs/input_bundle.json。
2. 使用 task 分别委托四个子 agent，必须生成这四个草稿：
   - /outputs/code_health/drafts/architect.md
   - /outputs/code_health/drafts/security.md
   - /outputs/code_health/drafts/dependencies.md
   - /outputs/code_health/drafts/documentation.md
3. 四个草稿完成后，委托 summarizer 合并最终报告。
4. 最终产物只能写入 /outputs/code_health/code_health_report.md。必须实际调用 write_file 写入该路径；只在聊天里说明不算完成。

硬性约束：
- 全文使用中文；工具名、文件路径、命令、错误码可以保留英文。
- 优先引用 tool_results.json 中的事实。工具跳过、不可用、超时都必须作为覆盖限制，不要写成“无问题”。
- 不要声称已经运行 Docker、SAST、测试、依赖审计或联网搜索，除非 tool_results.json 明确包含对应成功结果。
- 不要写 HTML、JSON 可视化或别名报告。"""


def _code_health_reviewers(
    telemetry: RunTelemetry,
    skill_sources: AgentSkillSources,
    fallback_model: Any | None,
    completed_paths: set[str],
    event_callback: Any | None,
) -> list[dict[str, Any]]:
    return [
        {
            "name": "architect",
            "description": "分析目录结构、模块边界、耦合、可维护性和代码坏味道。",
            "skills": skill_sources.source("architect", "code-health/code-architect"),
            "middleware": [
                *_runtime_middleware(fallback_model, retry_tools={"read_file"}),
                ToolAllowlistMiddleware({"read_file", "write_file"}, "code-health architect"),
                TelemetryMiddleware(telemetry, "code-health.architect", event_callback=event_callback),
            ],
            "system_prompt": """你是代码架构师。读取 /outputs/code_health/raw/tool_results.json，必要时读取 /inputs/code_scan_summary.json。
输出中文 Markdown 草稿到 /outputs/code_health/drafts/architect.md。只基于已有扫描事实和输入摘要，不要泛读源码或虚构发现。""",
        },
        {
            "name": "security-analyst",
            "description": "分析安全风险、疑似密钥、Bandit/detect-secrets 结果和人工安全判断边界。",
            "skills": skill_sources.source("security-analyst", "code-health/code-security"),
            "middleware": [
                *_runtime_middleware(fallback_model, retry_tools={"read_file"}),
                ToolAllowlistMiddleware({"read_file", "write_file"}, "code-health security analyst"),
                TelemetryMiddleware(telemetry, "code-health.security-analyst", event_callback=event_callback),
            ],
            "system_prompt": """你是应用安全工程师。读取 /outputs/code_health/raw/tool_results.json。
输出中文 Markdown 草稿到 /outputs/code_health/drafts/security.md。没有确定证据时标为待复核，不要编造漏洞或 CVE。""",
        },
        {
            "name": "dependency-checker",
            "description": "分析依赖清单、pip-audit/deptry 覆盖、依赖安装限制和升级建议。",
            "skills": skill_sources.source("dependency-checker", "code-health/code-dependency"),
            "middleware": [
                *_runtime_middleware(fallback_model, retry_tools={"read_file"}),
                ToolAllowlistMiddleware({"read_file", "write_file"}, "code-health dependency checker"),
                TelemetryMiddleware(telemetry, "code-health.dependency-checker", event_callback=event_callback),
            ],
            "system_prompt": """你是依赖管理专家。读取 /outputs/code_health/raw/tool_results.json。
输出中文 Markdown 草稿到 /outputs/code_health/drafts/dependencies.md。pip-audit 跳过或超时时必须明确说明不能证明依赖安全。""",
        },
        {
            "name": "doc-reviewer",
            "description": "分析 README、docstring、测试与文档覆盖限制。",
            "skills": skill_sources.source("doc-reviewer", "code-health/code-documentation"),
            "middleware": [
                *_runtime_middleware(fallback_model, retry_tools={"read_file"}),
                ToolAllowlistMiddleware({"read_file", "write_file"}, "code-health doc reviewer"),
                TelemetryMiddleware(telemetry, "code-health.doc-reviewer", event_callback=event_callback),
            ],
            "system_prompt": """你是技术文档专家。读取 /outputs/code_health/raw/tool_results.json。
输出中文 Markdown 草稿到 /outputs/code_health/drafts/documentation.md。重点说明文档、测试和覆盖率证据是否充分。""",
        },
        {
            "name": "summarizer",
            "description": "合并四个代码健康草稿和工具结果，生成最终中文代码健康报告。",
            "skills": skill_sources.source("code-health-summarizer", "code-health/report-writing-code-health"),
            "middleware": [
                *_runtime_middleware(fallback_model, retry_tools={"read_file"}),
                ToolAllowlistMiddleware({"read_file", "write_file"}, "code-health summarizer"),
                FinalOutputGuardMiddleware(
                    {"/outputs/code_health/code_health_report.md"},
                    "code-health summarizer",
                    completed_paths,
                ),
                TelemetryMiddleware(telemetry, "code-health.summarizer", event_callback=event_callback),
            ],
            "system_prompt": """你是代码健康报告编辑。读取：
- /outputs/code_health/raw/tool_results.json
- /outputs/code_health/drafts/architect.md
- /outputs/code_health/drafts/security.md
- /outputs/code_health/drafts/dependencies.md
- /outputs/code_health/drafts/documentation.md

按 report-writing-code-health skill 的结构生成最终报告。必须调用 write_file 写入 /outputs/code_health/code_health_report.md；只回复报告正文但不写文件视为失败。
完成写入后停止，不要创建其他最终报告。""",
        },
    ]


def _prd_prompt() -> str:
    return """你是 Aperio 的 PRD 评审编排器。

输入：
- /inputs/user_request.md：用户原始需求。
- /inputs/input_bundle.json：标准化输入包。
- /local-resources/aperio_policy.yaml：安全、工具、存储和最终产物策略。

工作流：
1. 先读取 /local-resources/aperio_policy.yaml，再基于用户输入写 PRD 初稿 /outputs/prd_review/prd_v1.md。缺失信息标为“待确认”，不要用示例补全事实。
2. 使用 task 分别委托四个评审子 agent，必须生成：
   - /outputs/prd_review/drafts/review_strategy.md
   - /outputs/prd_review/drafts/review_tech.md
   - /outputs/prd_review/drafts/review_ux.md
   - /outputs/prd_review/drafts/review_risk.md
3. 委托 editor 合并为：
   - /outputs/prd_review/prd_v2_final.md
   - /outputs/prd_review/review_matrix.md

硬性约束：
- 全文使用中文。
- 用户输入是最高优先级事实来源。
- 如果 internet_search 工具可用，Writer 最多调用 1 次并保存到 /outputs/prd_review/raw/web_search/writer-research.json；如果工具不可用，不要假装已搜索。
- 不要创建 final_report.md、merged-report.md 或根目录别名文件。"""


def _prd_reviewers(
    web_tools: list[Any],
    telemetry: RunTelemetry,
    skill_sources: AgentSkillSources,
    fallback_model: Any | None,
    completed_paths: set[str],
    event_callback: Any | None,
) -> list[dict[str, Any]]:
    web_tool_names = {str(getattr(tool, "name", "")) for tool in web_tools}
    return [
        {
            "name": "product-strategist",
            "description": "从产品策略、价值、范围和运营视角评审 PRD。",
            "skills": skill_sources.source("product-strategist", "prd-review/review-ops", "shared/web-search"),
            "tools": web_tools,
            "middleware": [
                *_runtime_middleware(fallback_model, retry_tools={"read_file", "internet_search", *web_tool_names}),
                ToolAllowlistMiddleware({"read_file", "write_file", "internet_search", *web_tool_names}, "PRD product strategist"),
                TelemetryMiddleware(telemetry, "prd-review.product-strategist", event_callback=event_callback),
            ],
            "system_prompt": """你是产品策略分析师。读取 /outputs/prd_review/prd_v1.md。
如果 internet_search 工具可用，最多调用 1 次并保存到 /outputs/prd_review/raw/web_search/product-strategy.json；如果工具不可用，不要假装已经联网搜索。
输出中文 Markdown 草稿到 /outputs/prd_review/drafts/review_strategy.md。""",
        },
        {
            "name": "technical-feasibility",
            "description": "从技术可行性、架构、集成、数据和实施复杂度视角评审 PRD。",
            "skills": skill_sources.source("technical-feasibility", "prd-review/review-tech"),
            "middleware": [
                *_runtime_middleware(fallback_model, retry_tools={"read_file"}),
                ToolAllowlistMiddleware({"read_file", "write_file"}, "PRD technical feasibility reviewer"),
                TelemetryMiddleware(telemetry, "prd-review.technical-feasibility", event_callback=event_callback),
            ],
            "system_prompt": """你是技术架构师。读取 /outputs/prd_review/prd_v1.md。
输出中文 Markdown 草稿到 /outputs/prd_review/drafts/review_tech.md。重点指出技术风险、依赖和验收可测性。""",
        },
        {
            "name": "ux-researcher",
            "description": "从用户体验、用户流程、边界情况和可访问性视角评审 PRD。",
            "skills": skill_sources.source("ux-researcher", "prd-review/review-ux"),
            "middleware": [
                *_runtime_middleware(fallback_model, retry_tools={"read_file"}),
                ToolAllowlistMiddleware({"read_file", "write_file"}, "PRD UX researcher"),
                TelemetryMiddleware(telemetry, "prd-review.ux-researcher", event_callback=event_callback),
            ],
            "system_prompt": """你是 UX 研究员。读取 /outputs/prd_review/prd_v1.md。
输出中文 Markdown 草稿到 /outputs/prd_review/drafts/review_ux.md。重点检查场景、流程、异常状态和用户反馈。""",
        },
        {
            "name": "risk-analyst",
            "description": "从项目风险、隐私、安全、合规、资源和里程碑视角评审 PRD。",
            "skills": skill_sources.source("risk-analyst", "prd-review/review-risk"),
            "middleware": [
                *_runtime_middleware(fallback_model, retry_tools={"read_file"}),
                ToolAllowlistMiddleware({"read_file", "write_file"}, "PRD risk analyst"),
                TelemetryMiddleware(telemetry, "prd-review.risk-analyst", event_callback=event_callback),
            ],
            "system_prompt": """你是项目风险分析师。读取 /outputs/prd_review/prd_v1.md。
输出中文 Markdown 草稿到 /outputs/prd_review/drafts/review_risk.md。风险必须可追溯到 PRD 或用户输入。""",
        },
        {
            "name": "editor",
            "description": "合并 PRD 初稿和四个评审草稿，输出 PRD v2 与评审矩阵。",
            "skills": skill_sources.source("prd-editor", "prd-review/report-writing-prd", "prd-review/review-matrix"),
            "middleware": [
                *_runtime_middleware(fallback_model, retry_tools={"read_file"}),
                ToolAllowlistMiddleware({"read_file", "write_file"}, "PRD editor"),
                FinalOutputGuardMiddleware(
                    {"/outputs/prd_review/prd_v2_final.md", "/outputs/prd_review/review_matrix.md"},
                    "PRD editor",
                    completed_paths,
                ),
                TelemetryMiddleware(telemetry, "prd-review.editor", event_callback=event_callback),
            ],
            "system_prompt": """你是 PRD 编辑。读取：
- /outputs/prd_review/prd_v1.md
- /outputs/prd_review/drafts/review_strategy.md
- /outputs/prd_review/drafts/review_tech.md
- /outputs/prd_review/drafts/review_ux.md
- /outputs/prd_review/drafts/review_risk.md

按 report-writing-prd 和 review-matrix skills 合并结果。
必须调用 write_file 分别写入 /outputs/prd_review/prd_v2_final.md 和 /outputs/prd_review/review_matrix.md；只在聊天里返回正文但不写文件视为失败。完成后停止。""",
        },
    ]


def _general_prompt() -> str:
    return """你是 Aperio 的通用智能体。用中文简洁回答。

边界：
- 不要创建 PRD 或代码健康产物，除非用户明确要求。
- 如果问题需要实时数据或联网搜索，优先使用可用 MCP 工具；没有可用工具时说明限制，不要编造。
- 对天气、城市、地址、周边、路线类问题，如果 maps_ 开头的高德 MCP 工具可用，优先调用；不可用时说明需要配置 AMAP_API_KEY 和 APERIO_ENABLE_MCP。
- 如果用户提到“今天、明天、昨天”等相对日期，请结合当前运行日期，但没有外部数据时不要假装已验证。"""


def _extract_final_answer(response: Any) -> str:
    value = response.value if hasattr(response, "value") else response
    messages = value.get("messages", []) if isinstance(value, dict) else getattr(value, "messages", [])
    for message in reversed(messages or []):
        msg_type = getattr(message, "type", None) or getattr(message, "role", None)
        if msg_type in {"ai", "assistant"} or message.__class__.__name__.lower().startswith("ai"):
            text = _content_text(getattr(message, "content", None))
            if text:
                return text
    return ""


def _write_observability(run_root: Path, telemetry: RunTelemetry, mcp_errors: list[str]) -> None:
    payload = telemetry.to_dict()
    if mcp_errors:
        payload["mcp_errors"] = mcp_errors
    (run_root / "observability.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
            else:
                parts.append(str(item))
        return "\n".join(parts).strip()
    return str(content).strip() if content is not None else ""
