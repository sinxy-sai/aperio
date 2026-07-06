# Aperio Agent Backend

这是 Web UI 使用的独立后端 agent 包，不依赖 `demo/`，也不会启动 `demo/aperio_integrated.py`。

## 配置

从模板创建 `aperio_agent_backend/.env`：

```powershell
Copy-Item aperio_agent_backend/.env.example aperio_agent_backend/.env
```

然后填入：

```env
DEEPSEEK_API_KEY=你的 key
APERIO_ENGINE=deepagents
APERIO_MODEL=openai:deepseek-v4-flash
APERIO_BASE_URL=https://api.deepseek.com
APERIO_INSTALL_PROJECT_DEPS=0
APERIO_SCAN_SANDBOX=host
APERIO_ENABLE_MCP=0
AMAP_API_KEY=
```

也可以直接在当前 shell 中设置这些环境变量。

## 工作目录

运行产物会写入：

```text
aperio_agent_backend/workspace/<run_id>/
```

当前支持：

- 通用问答
- PRD 生成与评审矩阵
- 代码健康扫描与报告

默认使用 `APERIO_ENGINE=deepagents`，会运行包内 DeepAgents router 和子 agent。也可以设置 `APERIO_ENGINE=lite` 使用轻量 fallback。

代码健康报告默认在 host 环境运行包内迁移的 `code-health-toolkit`，把确定性扫描结果写入 `outputs/code_health/raw/tool_results.json`，再交给 DeepAgents 子 agent 生成报告。默认不安装项目依赖；如果当前环境安装了 `ruff`、`mypy`、`bandit`、`radon`、`detect-secrets` 等工具，会自动纳入扫描证据。

可选能力：

- `APERIO_SCAN_SANDBOX=docker`：使用包内 Dockerfile 构建 `aperio-sandbox:py311-tools`，并在 Docker 沙箱里运行扫描器。项目目录以只读方式挂载。
- `APERIO_SCAN_SANDBOX=auto`：优先 Docker，失败后回退 host 扫描。
- `APERIO_ENABLE_MCP=1`：启用包内 web search MCP；安装 `aperio-agent[mcp]` 后可用。
- `AMAP_API_KEY=...`：在 MCP 开启时额外启用高德地图 MCP 工具。
- CLI 交互模式 `aperio` 默认使用 prompt 审批；Web 端不能使用 prompt，只能选择 approve/reject。

## CLI

安装包后，直接运行：

```powershell
aperio
```

即可进入持续聊天模式。常用命令：

```powershell
aperio init
aperio doctor
aperio run "帮我写一份 PRD"
aperio serve --host 127.0.0.1 --port 8088
```
