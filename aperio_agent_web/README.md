# Aperio Agent Web

独立的本地 Web 工作台，用来调用 `aperio_agent_backend` 里的后端 agent。

## 后端配置

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

## 启动

安装为包后，推荐使用：

```powershell
aperio serve --host 127.0.0.1 --port 8088
```

本仓库源码运行也可以用：

```powershell
conda activate llm-dev
python -m uvicorn aperio_agent_web.app:app --reload --host 127.0.0.1 --port 8088
```

打开：

```text
http://127.0.0.1:8088
```

如果提示 `WinError 10013` 或端口无法绑定，通常是 `8088` 已经被旧服务占用。可以先查并停止旧进程：

```powershell
Get-Process -Id (Get-NetTCPConnection -LocalPort 8088).OwningProcess
Stop-Process -Id (Get-NetTCPConnection -LocalPort 8088).OwningProcess -Force
```

也可以直接换一个端口：

```powershell
python -m uvicorn aperio_agent_web.app:app --reload --host 127.0.0.1 --port 8089
```

## 说明

- Web UI 不再启动 `demo/aperio_integrated.py`。
- 后端 agent 位于 `aperio_agent_backend/`。
- 运行产物写入 `aperio_agent_backend/workspace/<run_id>/`，页面右侧会预览常见 Markdown 产物。
- 当前代码体检默认在 host 环境运行包内 `code-health-toolkit` 生成 `outputs/code_health/raw/tool_results.json`，再由 agent 基于扫描证据生成报告；默认不安装项目依赖，环境中已安装的静态检查工具会被自动使用。
- 设置 `APERIO_SCAN_SANDBOX=docker` 可切换到 Docker 沙箱扫描；设置 `auto` 可优先 Docker、失败后回退 host。
- 设置 `APERIO_ENABLE_MCP=1` 可启用包内 web search MCP；配置 `AMAP_API_KEY` 后可额外启用高德地图 MCP。
