# Aperio — 研发质量多智能体守护平台 设计文档

> 基于 DeepAgents 的全栈项目，面向《人工智能交叉学科项目应用实践》大作业，并以此为起点扩展为更完善的生产级应用。

---

## 目录

1. [产品概述](#1-产品概述)
2. [系统架构](#2-系统架构)
3. [核心工作流](#3-核心工作流)
4. [技能系统](#4-技能系统)
5. [上下文工程](#5-上下文工程)
6. [长期记忆](#6-长期记忆)
7. [安全机制](#7-安全机制)
8. [可观测性](#8-可观测性)
9. [数据模型](#9-数据模型)
10. [前端页面设计](#10-前端页面设计)
11. [模型选型](#11-模型选型)
12. [错误处理策略](#12-错误处理策略)
13. [开发路线图](#13-开发路线图)
14. [技术点覆盖自检](#14-技术点覆盖自检)

---

## 1. 产品概述

### 1.1 产品定位

**Aperio** 是一款面向软件开发团队的质量守护平台，覆盖研发全周期的两个关键质量关卡：

- **设计阶段**：AI 辅助 PRD 生成与多角色评审
- **开发阶段**：AI 驱动代码仓库多维度健康体检

### 1.2 用户画像

| 角色 | 使用场景 |
|------|----------|
| 个人开发者 / 小团队 | 项目提交前自检，发现架构、安全、依赖风险 |
| 产品经理（非技术背景） | 写好需求描述，系统自动产出标准化 PRD 并接受多角色评审 |
| 技术 Leader | 定期对团队仓库做健康扫描，追踪技术债趋势 |

### 1.3 产品形态

- Web 应用（浏览器访问），本地 Docker 部署
- 后端：FastAPI + DeepAgents（LangGraph）
- 前端：React 19 + TypeScript + shadcn/ui（基于 full-stack-fastapi-template 改造）
- 一键启动：`docker compose up`

### 1.4 用户核心路径

```
代码体检路径：
  登录 → 首页 → 代码体检页 → 输入仓库URL → 等待分析 → 查看健康度报告

PRD评审路径：
  登录 → 首页 → PRD评审页 → 输入需求描述 → 等待评审 → 查看PRD + 评审矩阵
```

---

## 2. 系统架构

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────┐
│                  前端 (React + TypeScript)                 │
│  基于 FastAPI 模板改造：Dashboard + 代码体检页 + PRD评审页    │
└──────────────────────────┬──────────────────────────────┘
                           │ REST API + WebSocket (实时流式)
┌──────────────────────────▼──────────────────────────────┐
│               FastAPI 后端 (Python 3.10+)                  │
│                                                          │
│  ┌────────────┐  ┌────────────┐  ┌─────────────────┐   │
│  │  用户模块   │  │ 代码体检模块 │  │   PRD评审模块    │   │
│  │ (模板自带)  │  │            │  │                 │   │
│  │ JWT认证    │  │ 任务CRUD   │  │  任务CRUD       │   │
│  │ 用户管理   │  │ 报告查询   │  │  报告查询       │   │
│  └─────┬──────┘  └─────┬──────┘  └───────┬─────────┘   │
│        │               │                  │              │
│        └───────────────┼──────────────────┘              │
│                        │                                 │
│  ┌─────────────────────▼───────────────────────────────┐ │
│  │              DeepAgents 引擎层                        │ │
│  │                                                      │ │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐ │ │
│  │  │  Subagent   │  │   Skill     │  │   Context   │ │ │
│  │  │ Orchestrator│  │  Registry   │  │   Manager   │ │ │
│  │  └─────────────┘  └─────────────┘  └─────────────┘ │ │
│  └────────────────────┬─────────────────────────────────┘ │
│                       │                                  │
│  ┌────────────────────▼─────────────────────────────────┐ │
│  │                 基础设施层                             │ │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │ │
│  │  │  Docker  │  │ LangSmith│  │ Storage Backends │   │ │
│  │  │  Sandbox │  │ Tracing  │  │ (FS/Store/State) │   │ │
│  │  └──────────┘  └──────────┘  └──────────────────┘   │ │
│  └──────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────┘
```

### 2.2 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| Agent 调用模式 | 异步任务（BackgroundTasks 起步，可升级 Celery） | 代码分析可能跑数分钟，不能阻塞 HTTP |
| 实时通信 | REST 查询状态 + WebSocket 推送 Agent 进度 | 用户可看到Agent实时输出 |
| 沙盒范围 | 仅代码体检模块使用 | PRD 评审不涉及不可信代码执行 |
| Agent 框架 | DeepAgents (LangGraph) | 课程核心技术栈，13个练习全覆盖 |
| 模型默认 | DeepSeek v4 (兼容 OpenAI API) | 练习中已验证，成本低效果好 |
| 部署 | `docker compose up` 一键启动 | 模板已有完整 compose 配置 |

### 2.3 FastAPI 模板复用清单

| 直接复用 | 改造 | 完全新增 |
|----------|------|---------|
| 用户注册/登录/JWT认证 | 前端 Dashboard → 双模块入口 | 代码体检路由 + 服务层 |
| SQLModel + Alembic 迁移框架 | 导航菜单增加两个入口 | PRD评审路由 + 服务层 |
| shadcn/ui 组件库 | `.env` 增加 Agent 配置项 | DeepAgents 引擎层 |
| Docker Compose（Traefik/PostgreSQL） | 数据库新增 Report 模型 | Skills 目录（12个SKILL.md） |
| pytest 测试框架 | — | /memories/ 存储层 |
| Email 模板系统 | — | WebSocket handler |
| ruff/mypy/pre-commit | — | PerformanceMiddleware + AuditMiddleware |

---

## 3. 核心工作流

### 3.1 模块一：代码体检

```
用户提交 Git 仓库 URL
        │
        ▼
┌──────────────────────────────────────┐
│  🎯 Orchestrator Agent (同步)         │
│  - write_todos 拆解任务               │
│  - git clone → Docker Sandbox         │
│  - 写入 /workspace/{task_id}/code/    │
└────────────┬─────────────────────────┘
             │ 并行派发 4 个子代理 (异步)
    ┌────────┼────────┬────────┐
    ▼        ▼        ▼        ▼
┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐
│架构   │ │安全   │ │依赖   │ │文档   │
│分析   │ │扫描   │ │检查   │ │评估   │
│Agent  │ │Agent  │ │Agent  │ │Agent  │
│       │ │       │ │       │ │       │
│目录结构│ │bandit │ │依赖版本│ │注释率  │
│模块耦合│ │semgrep│ │已知漏洞│ │API文档│
│循环依赖│ │硬编码  │ │兼容性 │ │README │
│分层是否│ │密钥扫描│ │License│ │质量   │
│合理   │ │        │ │      │ │       │
└───┬───┘ └──┬────┘ └──┬────┘ └───┬───┘
    │        │        │        │
    └────────┼────────┼────────┘
             │ 汇总（Compress: 结构化为 Report 对象）
             ▼
┌──────────────────────────────────────┐
│  📊 Summarizer Agent (同步)           │
│  - 去重合并 4 份子报告                 │
│  - 风险分级（Critical/High/Med/Low）    │
│  - 与 /memories/ 历史对比              │
│  - 输出：健康度评分 + 风险清单 + 建议    │
└──────────────────────────────────────┘
```

**沙盒安全在此流程的作用**：

| 环节 | 安全风险 | 沙盒措施 |
|------|---------|---------|
| 拉取代码 | 恶意仓库或超大仓库（>500MB） | 容器磁盘配额 + HITL 确认 |
| 安全扫描 Agent | `bandit`/`semgrep` 结果写入恶意路径 | 容器内只读挂载 `/code` |
| 依赖检查 Agent | `pip install` 或 `npm install` 拉恶意包 | `--network none` 禁用外网 |
| 自动修复（可选） | Agent 误删或误改代码 | HITL：所有写操作需人工审批 |

### 3.2 模块二：PRD 评审

```
用户输入需求描述（自然语言）
        │
        ▼
┌──────────────────────────────────────┐
│  ✍️ PRD Writer Agent (同步)           │
│  - 结构化生成 PRD 草稿                 │
│  - 章节：背景/用户故事/功能范围/       │
│          交互流程/验收标准/非功能需求   │
│  - 写入 /workspace/{task_id}/prd.md   │
└────────────┬─────────────────────────┘
             │ 并行派发 4 个评审子代理 (异步)
    ┌────────┼────────┬────────┐
    ▼        ▼        ▼        ▼
┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐
│技术   │ │UX    │ │测试   │ │运营   │
│评审   │ │评审   │ │评审   │ │评审   │
│Agent  │ │Agent  │ │Agent  │ │Agent  │
│       │ │       │ │       │ │       │
│技术可行性│交互流程│边界条件│商业价值│
│技术栈  │ │异常状态│可测试性│上线策略│
│API设计 │ │一致性 │验收标准│风险点  │
│安全性  │ │无障碍 │性能   │竞品对比│
└───┬───┘ └──┬────┘ └──┬────┘ └───┬───┘
    │        │        │        │
    └────────┼────────┼────────┘
             │ 汇总（Compress: 结构化为评审矩阵）
             ▼
┌──────────────────────────────────────┐
│  📋 PRD Editor Agent (同步)           │
│  - 综合 4 份评审意见                   │
│  - 输出：修订版 PRD + 评审矩阵          │
│  - 评审矩阵格式：维度/问题/严重度/建议   │
│  - 写入 /memories/ 记录偏好            │
└──────────────────────────────────────┘
```

### 3.3 异步与同步子代理协作总结

| 模块 | 同步子代理 (sync) | 异步子代理 (async) |
|------|------------------|-------------------|
| 代码体检 | Orchestrator, Summarizer | ArchAgent, SecurityAgent, DepAgent, DocAgent |
| PRD评审 | Writer, Editor | TechReview, UxReview, TestReview, OpsReview |

- **同步**：必须等待结果才能继续（派发任务的 Agent / 汇总 Agent）
- **异步**：并行执行互不依赖（4 个分析/评审 Agent 同时跑）

---

## 4. 技能系统

### 4.1 技能层级架构

```
skills/
├── general/                          ← 通用技能（所有Agent共享）
│   ├── skill-creator/SKILL.md        ← Anthropic 开源，自我扩展能力
│   ├── report-writing/SKILL.md       ← 统一报告格式规范
│   ├── review-matrix/SKILL.md        ← 评审矩阵标准
│   └── tool-usage/SKILL.md           ← 工具调用规范
│
├── code-health/                      ← 代码体检专用技能
│   ├── code-architect/SKILL.md       ← 架构分析 Agent
│   ├── code-security/SKILL.md        ← 安全扫描 Agent
│   ├── code-dependency/SKILL.md      ← 依赖检查 Agent
│   └── code-documentation/SKILL.md   ← 文档评估 Agent
│
└── prd-review/                       ← PRD评审专用技能
    ├── review-tech/SKILL.md          ← 技术可行性评审
    ├── review-ux/SKILL.md            ← 交互体验评审
    ├── review-test/SKILL.md          ← 可测试性评审
    └── review-ops/SKILL.md           ← 商业与运营评审
```

共 12 个 SKILL.md 文件。每个 SKILL.md 遵循 DeepAgents 规范：YAML frontmatter（name、description、triggers）+ Markdown 正文（角色定义、工作流程、检查清单、输出格式）。

### 4.2 SKILL.md 示例

#### 通用技能：`skills/general/skill-creator/SKILL.md`

引用 Anthropic 开源的 skill-creator，指导 Agent 在遇到未知任务时如何创建新技能或优化现有技能，赋予系统自我扩展能力。该技能在 Orchestrator 和各 Subagent 初始化时注入。

#### 专用技能示例：`skills/code-health/code-security/SKILL.md`

```markdown
---
name: code-security
description: 代码安全漏洞扫描专家，擅长 Python/JavaScript 安全审计
triggers:
  - 安全漏洞
  - 代码审计
  - 依赖安全
---

## 角色定义
你是资深应用安全工程师（Application Security Engineer），
擅长 Python 和 JavaScript/TypeScript 代码安全审计。

## 工作流程
1. 在 Docker Sandbox 中执行 `bandit -r /code/` 扫描 Python
2. 执行 `semgrep --config=auto /code/` 扫描通用规则
3. 结合代码上下文人工解读扫描结果，去除误报
4. 按严重度（Critical > High > Medium > Low）分级输出

## 检查清单
- [ ] SQL 注入风险
- [ ] XSS 跨站脚本
- [ ] 硬编码密钥/Token/密码
- [ ] 不安全的反序列化 (pickle/yaml.load)
- [ ] 路径遍历漏洞
- [ ] 不安全的权限配置
- [ ] 已知 CVE 依赖

## 输出格式
| 序号 | 严重度 | 文件:行号 | 问题描述 | 修复建议 |
|------|--------|----------|---------|---------|
```

#### 专用技能示例：`skills/prd-review/review-ux/SKILL.md`

```markdown
---
name: review-ux
description: 从交互体验和可用性角度评审产品需求文档
triggers:
  - PRD评审
  - 用户体验
  - 交互设计
---

## 角色定义
你是资深 UX 设计师，关注用户交互流程和体验一致性。

## 评审维度
1. **交互流程**：用户操作路径是否最短？是否有不必要的步骤？
2. **异常状态**：加载中、空数据、错误状态、超时是否覆盖？
3. **一致性**：与系统其他模块的交互模式是否一致？
4. **无障碍**：是否考虑了键盘操作、屏幕阅读器等场景？
5. **信息架构**：导航是否符合用户心智模型？

## 输出格式
| 序号 | 维度 | 严重度 | 问题描述 | 改进建议 |
|------|------|--------|---------|---------|
```

### 4.3 渐进式披露策略

| 注入时机 | 注入内容 | 目的 |
|---------|---------|------|
| Agent 初始化 | 专用 SKILL.md 全文 | 角色定义 + 工作标准 |
| 执行子任务时 | 引用通用技能（按需 `read_file`） | 保持上下文简洁 |
| 汇总阶段 | report-writing SKILL.md | 统一输出格式 |
| 跨任务 | 从 /memories/ 读取用户偏好 | 个性化调整 |

---

## 5. 上下文工程

实现全部四个支柱：**Write / Select / Compress / Isolate**。

### 5.1 Write — 文件系统作为外部记忆

| 场景 | 写入内容 | 文件位置 |
|------|---------|---------|
| 代码体检 - 仓库克隆 | 按模块拆分的源代码 | `/workspace/{task_id}/code/` |
| 代码体检 - 子Agent分析完 | 各自分析草稿 | `/workspace/{task_id}/drafts/arch.md` 等 |
| PRD评审 - Writer生成 | PRD 初稿 | `/workspace/{task_id}/prd_v1.md` |
| PRD评审 - 评审Agent输出 | 各自评审意见 | `/workspace/{task_id}/drafts/review_tech.md` 等 |

**实现**：通过 `CompositeBackend` 路由：
- `/workspace/{task_id}/code/` → Docker Sandbox
- `/workspace/{task_id}/drafts/` → 本地 FilesystemBackend
- `/memories/` → StoreBackend
- `/temp/` → StateBackend（会话级）

### 5.2 Select — 按需选择性读取

Agent 不一次性读入所有文件，而是：
```
1. ls /workspace/{task_id}/code/src/   → 查看目录结构
2. ls /workspace/{task_id}/code/src/app/ → 深入子目录
3. read_file main.py                    → 只读需要的文件
4. read_file models.py                  → 按需继续
```

### 5.3 Compress — 任务边界压缩

每个子 Agent 完成后，不传完整对话历史，而是输出结构化摘要：

```python
# SecurityAgent 输出压缩为：
SecurityReport = {
    "scanned_files": 47,
    "total_issues": 9,
    "by_severity": {"critical": 2, "high": 3, "medium": 3, "low": 1},
    "findings": [
        {
            "severity": "Critical",
            "file": "app/db.py",
            "line": 15,
            "issue": "SQL注入风险：使用字符串拼接构建查询",
            "fix": "改用参数化查询"
        },
        # ...
    ]
}
```

SummarizerAgent 收到的是 4 个 Report 对象，不是 4 个 Agent 几十 KB 的对话历史。

### 5.4 Isolate — 子代理上下文隔离

每个子代理拥有独立的上下文窗口：

```
ArchitectureAgent 上下文：          SecurityAgent 上下文：
├── skills/code-architect/SKILL.md  ├── skills/code-security/SKILL.md
├── 代码目录结构                      ├── bandit/semgrep 扫描结果
├── 模块依赖关系                      ├── CVE 数据库片段
└── (不包含安全扫描结果)               └── (不包含架构分析内容)

→ 上下文干净、聚焦，互不污染
```

### 5.5 四支柱覆盖矩阵

| 支柱 | 代码体检模块 | PRD评审模块 |
|------|------------|-----------|
| **Write** | 代码写入 Sandbox、中间稿写入 FS | PRD 稿写入 FS、评审意见写入 FS |
| **Select** | Agent 通过 ls/read_file 按需探索 | Editor 按需读取各评审意见 |
| **Compress** | 4 份子报告 → 结构化 Report 对象 | 4 份评审 → 结构化评审矩阵 |
| **Isolate** | 4 分析 Agent 独立上下文 | 4 评审 Agent 独立上下文 |

---

## 6. 长期记忆

### 6.1 存储架构

使用 `StoreBackend` + `/memories/` 路径：

```
/memories/
├── preferences/                          ← 用户偏好
│   ├── {user_id}_tech_stack.json         ← 技术栈偏好（语言/框架/工具）
│   ├── {user_id}_prd_template.json       ← PRD 模板结构偏好
│   └── {user_id}_risk_threshold.json     ← 风险容忍阈值
│
├── history/                              ← 历史记录
│   ├── {user_id}_scans.json              ← 历次代码体检摘要
│   └── {user_id}_reviews.json            ← 历次 PRD 评审摘要
│
└── context/                              ← 项目上下文
    ├── {project_id}_context.json         ← 项目描述（如"电商后台"）
    └── {project_id}_trend.json           ← 技术债趋势追踪
```

### 6.2 跨线程验证

代码体检流程跨多个线程访问同一 /memories/：
- 线程 A：Orchestrator 写任务上下文
- 线程 B-E：4 个并行子 Agent 读取历史偏好
- 线程 F：Summarizer 读取上次报告做趋势对比

报告中展示："上次扫描发现 12 个问题，已修复 5 个，新增 3 个"。

### 6.3 使用示例

```python
# StoreBackend 配置
store_backend = StoreBackend(
    store=InMemoryStore(),  # 开发期可换 Redis
    namespace="aperio",
)

# Agent 通过路径访问
# 写记忆: write_file("/memories/preferences/{user_id}_tech_stack.json", ...)
# 读记忆: read_file("/memories/history/{user_id}_scans.json")
```

---

## 7. 安全机制

### 7.1 Docker Sandbox 架构

```python
class DockerSandbox:
    """代码体检专用沙盒"""
    
    def __init__(self, task_id: str):
        self.container = docker_client.containers.run(
            image="aperio-sandbox:latest",
            command="sleep infinity",
            volumes={
                f"/tmp/{task_id}/code": {"bind": "/code", "mode": "ro"},  # 只读
                f"/tmp/{task_id}/output": {"bind": "/output", "mode": "rw"},
            },
            network_mode="none",  # 禁用外网
            mem_limit="512m",
            cpu_quota=50000,
            detach=True,
        )
    
    async def execute(self, command: str, timeout: int = 30) -> str:
        """在容器内执行命令，返回 stdout/stderr"""
        ...
```

### 7.2 CompositeBackend 路径路由

```python
composite = CompositeBackend(
    rules={
        r"/workspace/.*/code/": DockerSandboxBackend(),    # 代码在沙盒
        r"/workspace/.*/drafts/": FilesystemBackend(),      # 草稿在本地
        r"/memories/": StoreBackend(store, namespace="aperio"),
        r"/temp/": StateBackend(),
    }
)
```

### 7.3 HITL 审批触发点

| 触发时机 | 审批提示 | 实现方式 |
|---------|---------|---------|
| 安全扫描发现 Critical 漏洞后想自动修复 | "Agent 建议修改 `app/db.py:15` 以修复 SQL 注入，是否执行？" | LangGraph `Command` interrupt → 前端展示 diff → 用户批准/拒绝 |
| 依赖检查想自动升级过期包 | "建议将 `flask==2.0 → 3.1`，是否允许？" | 同上 |
| 仓库大小异常（>500MB） | "仓库大小异常（523MB），是否继续分析？" | 同上 |

### 7.4 FilesystemPermission 规则

| 路径 | 权限 | 说明 |
|------|------|------|
| `/workspace/{id}/code/` | 只读 | 用户代码不可被Agent修改 |
| `/workspace/{id}/code/.env` | 不可读 | 敏感文件过滤 |
| `/workspace/{id}/drafts/` | 读写 | 中间产物写入 |
| `/memories/` | 读写 | 长期记忆更新 |
| `/workspace/{id}/code/.git/` | 不可读 | Git 历史可能含敏感信息 |

---

## 8. 可观测性

### 8.1 三层观测架构

```
第一层：LangSmith Tracing（外部服务）
  - Project: "aperio"
  - 追踪：Agent 调用链、Tool 调用时序、完整上下文
  - 用途：调试 Agent 行为异常、回溯决策链路

第二层：PerformanceMiddleware（自定义）
  - 指标：每次模型调用耗时(ms)、工具调用耗时(ms)、Token 消耗
  - 维度：按子代理、按任务拆解
  - 输出：结构化日志 + 前端 Dashboard 数据源
  实现方式：
  - 在调用模型前后记录时间戳和 token 计数
  - 统计每个子代理的 token 消耗和耗时
  - 汇总到任务级别的性能报告

第三层：AuditMiddleware（自定义）
  - 记录：文件写操作、沙盒命令执行、HITL 审批结果
  - 用途：安全审计、合规演示
  实现方式：
  - 拦截所有 write_file/edit_file 操作
  - 拦截所有沙盒 execute 调用
  - 拦截所有 HITL 审批决策
  - 记录操作者、时间戳、操作内容摘要
```

### 8.2 前端 Dashboard 展示

改造模板自带的 Dashboard 页面：

| 面板 | 数据来源 | 展示内容 |
|------|---------|---------|
| Token 消耗 | PerformanceMiddleware | 柱状图：各子代理 Token 消耗对比 |
| 子代理耗时 | PerformanceMiddleware | 水平条形图：各子代理耗时 |
| 安全趋势 | /memories/ 历史数据 | 折线图：历次扫描的风险数量变化 |
| LangSmith | 外部链接 | 按钮跳转 LangSmith Trace 页面 |

---

## 9. 数据模型

在模板现有 `User` / `Item` 基础上新增以下 SQLModel 模型：

### 9.1 ScanTask（代码体检任务）

```python
class ScanTask(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    owner_id: uuid.UUID = Field(foreign_key="user.id", ondelete="CASCADE")
    repo_url: str = Field(max_length=2048)
    status: str = Field(default="pending")  # pending/running/completed/failed
    health_score: int | None = Field(default=None)  # 0-100
    summary: str | None = Field(default=None, sa_type=Text)  # 报告摘要
    report_path: str | None = Field(default=None)  # 完整报告文件路径
    error_message: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = Field(default=None)
    
    # 子报告元数据
    arch_issues: int = Field(default=0)
    security_issues: int = Field(default=0)
    dependency_issues: int = Field(default=0)
    documentation_issues: int = Field(default=0)
```

### 9.2 ReviewTask（PRD评审任务）

```python
class ReviewTask(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    owner_id: uuid.UUID = Field(foreign_key="user.id", ondelete="CASCADE")
    requirement: str = Field(sa_type=Text)  # 用户原始输入
    status: str = Field(default="pending")  # pending/running/completed/failed
    prd_path: str | None = Field(default=None)  # 生成的 PRD 文件路径
    matrix_path: str | None = Field(default=None)  # 评审矩阵文件路径
    total_issues: int = Field(default=0)
    error_message: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = Field(default=None)
```

---

## 10. 前端页面设计

### 10.1 页面结构

```
/                          → Dashboard (模板改造)
/login                     → 登录页 (模板自带，不改)
/signup                    → 注册页 (模板自带，不改)
/settings                  → 用户设置 (模板自带，不改)
/app/code-health           → 代码体检页 (新增)
/app/prd-review            → PRD 评审页 (新增)
/app/code-health/{task_id} → 体检报告详情页 (新增)
/app/prd-review/{task_id}  → 评审报告详情页 (新增)
```

### 10.2 代码体检页

- **输入区**：Git 仓库 URL 输入框 + 提交按钮 + "分析我的代码"按钮
- **进行中**：实时日志面板（WebSocket 推送 Agent 输出），显示 4 个子代理进度条
- **完成态**：健康度评分（大数字） + 四维雷达图 + 风险清单表格（按严重度排序）
- **操作**：下载完整报告（Markdown）、分享链接

### 10.3 PRD 评审页

- **输入区**：需求描述 Textarea + 提交按钮
- **进行中**：实时显示 Writer 生成过程 → 4 个评审 Agent 并行工作
- **完成态**：
  - 左侧：修订版 PRD（渲染后的 Markdown）
  - 右侧：评审矩阵表格（维度/问题/严重度/建议）
- **操作**：下载 PRD（Markdown）、下载评审矩阵（CSV）

### 10.4 Dashboard（改造）

原有 Dashboard 改为运维面板：
- Token 消耗柱状图（按任务）
- 子代理耗时对比图
- 安全趋势折线图（历次扫描统计）
- 最近任务列表

---

## 11. 模型选型

| 用途 | 模型 | 理由 |
|------|------|------|
| 默认 Agent（生产） | DeepSeek v4 (`deepseek-v4-flash`) | 练习已验证，性价比高，中文友好 |
| 备选 Agent | Qwen-Plus (`qwen-plus`) | DashScope API，练习已验证 |
| 轻量任务 | DeepSeek v3 (`deepseek-chat`) | 简单汇总、格式化任务 |
| 可切换 | OpenAI GPT-4o / Claude | 通过环境变量切换，兼容 OpenAI API 格式 |

所有模型通过 `init_chat_model` 统一加载，API Key 通过 `.env` 配置。

### 配置示例

```env
# .env 新增 Agent 配置
LLM_MODEL=deepseek-v4-flash
LLM_API_KEY=sk-xxxxx
LLM_BASE_URL=https://api.deepseek.com
LANGCHAIN_TRACING_V2=true
LANGCHAIN_PROJECT=aperio
LANGCHAIN_API_KEY=lsv2_xxxxx
SANDBOX_IMAGE=aperio-sandbox:latest
```

---

## 12. 错误处理策略

### 12.1 子代理失败处理

| 场景 | 策略 |
|------|------|
| 单个子代理失败 | 继续收集其他 3 份报告，汇总时标注"架构分析失败，已跳过" |
| 2+ 子代理失败 | 终止任务，返回部分结果 + 错误详情 |
| 超时（单子代理 >5min） | 强制终止该子代理，继续其他 |
| 全量失败 | 任务标记 failed，展示错误信息，允许重试 |
| 沙盒崩溃 | 重启容器，恢复任务状态 |
| API 调用失败 | 指数退避重试 3 次，仍失败则跳过该步骤 |

### 12.2 任务重试

- 失败的任务保留状态，用户可一键重试
- 重试时复用已成功的子代理结果（幂等设计）
- 部分成功时，仅重试失败的子代理

---

## 13. 开发路线图

### 开发原则

先在 `demo/` 目录像课程练习一样快速验证 DeepAgents 核心逻辑，调通后嵌入 FastAPI 模板。

### Phase 1: Demo 核心验证（demo/ 目录）

```
demo/
├── 01_basic_agent.py            → DeepAgent 基本连通，模型 + 工具就绪
├── 02_code_health_subagents.py  → 代码体检：Orchestrator + 4 并行子代理
├── 03_prd_review_subagents.py   → PRD 评审：Writer + 4 并行子代理
├── 04_skills/                   → Skill 体系搭建，SKILL.md 编写与验证
├── 05_context_engineering/      → Write/Select/Compress/Isolate 验证
├── 06_sandbox_hitl/             → Docker Sandbox 集成 + HITL 审批
├── 07_middleware_langsmith/     → PerformanceMiddleware + LangSmith
└── 08_longterm_memory/          → StoreBackend + /memories/ 跨线程验证
```

每个 demo 都是独立可运行的 `.py` 文件，像练习一样简洁。

### Phase 2: FastAPI 模板集成

```
任务：
├── 后端 - 新增数据模型（ScanTask, ReviewTask）+ Alembic 迁移
├── 后端 - 新增路由 /api/v1/code-health/ + /api/v1/prd-review/
├── 后端 - Agent Service 层（封装 DeepAgents 调用为异步任务）
├── 后端 - WebSocket handler（推送 Agent 实时日志）
├── 前端 - 新增代码体检页面
├── 前端 - 新增 PRD 评审页面
├── 前端 - Dashboard 改造
├── 联调 - 前后端对接
└── 部署 - docker compose 一键启动验证
```

### Phase 3: 打磨与交付

```
任务：
├── 集成测试（pytest）
├── 前端交互优化（loading / error / empty 状态）
├── 报告导出（Markdown / HTML 下载）
├── 安全加固（FilesystemPermission 规则完善）
├── 录屏 ≤5 分钟演示视频
├── 截图 ≥5 张关键页面
└── 撰写最终报告（Word/PDF）
```

---

## 14. 技术点覆盖自检

| 大作业要求 | 覆盖位置 | 状态 |
|-----------|---------|------|
| **多子代理协作** | §3：代码体检 1同步+4异步+1同步；PRD评审 1同步+4异步+1同步 | ✅ |
| **技能系统 (≥2)** | §4：4 通用技能 + 8 专用技能 = 12 SKILL.md | ✅ |
| **上下文工程 (≥2)** | §5：Write/Select/Compress/Isolate 四支柱全覆盖 | ✅ |
| **长期记忆** | §6：StoreBackend + /memories/ 三层记忆 + 跨线程趋势 | ✅ |
| **安全机制** | §7：DockerSandbox + CompositeBackend + HITL + FilesystemPermission | ✅ |
| **可观测性** | §8：LangSmith + PerformanceMiddleware + AuditMiddleware | ✅ |
| **成果输出** | §10：React Web 界面 + Dashboard + 报告下载 | ✅ |

---

> 📅 创建于 2025-06-25
> 📝 状态：设计阶段，待评审
