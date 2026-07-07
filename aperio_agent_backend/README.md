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
APERIO_PROVIDER=
APERIO_MODEL=
APERIO_FALLBACK_MODEL=
APERIO_BASE_URL=
APERIO_MODEL_CALL_LIMIT=100
APERIO_TOOL_CALL_LIMIT=160
APERIO_MODEL_MAX_RETRIES=3
APERIO_TOOL_MAX_RETRIES=2
APERIO_INSTALL_PROJECT_DEPS=0
APERIO_SCAN_SANDBOX=auto
APERIO_ENABLE_MCP=0
AMAP_API_KEY=
```

也可以直接在当前 shell 中设置这些环境变量。

`aperio init` 会额外创建 `~/.aperio/config.json`。模型和软件渠道建议放在这个 JSON 里，密钥仍可放 `.env`，并通过 `${VAR_NAME}` 引用：

```json
{
  "agents": {
    "defaults": {
      "model": "deepseek-v4-flash",
      "provider": "deepseek",
      "fallbackModel": ""
    }
  },
  "providers": {
    "deepseek": {
      "apiKey": "${DEEPSEEK_API_KEY}",
      "apiBase": "https://api.deepseek.com"
    },
    "openrouter": {
      "apiKey": "${OPENROUTER_API_KEY}",
      "apiBase": "https://openrouter.ai/api/v1"
    },
    "custom": {
      "apiKey": "",
      "apiBase": ""
    }
  },
  "channels": {
    "feishu": {
      "enabled": false,
      "appId": "${FEISHU_APP_ID}",
      "appSecret": "${FEISHU_APP_SECRET}",
      "encryptKey": "${FEISHU_ENCRYPT_KEY}",
      "verificationToken": "${FEISHU_VERIFICATION_TOKEN}",
      "allowFrom": [],
      "groupPolicy": "mention",
      "streaming": true,
      "domain": "feishu",
      "maxMediaBytes": 26214400
    }
  }
}
```

`provider` 可设为 `auto` 自动匹配，也可固定为 `deepseek`、`openrouter`、`dashscope`、`moonshot`、`siliconflow`、`custom` 等。`custom` 用于 vLLM、Ollama、LM Studio 或其它 OpenAI-compatible 网关。`APERIO_MODEL` 和 `APERIO_BASE_URL` 仍可作为旧式环境变量覆盖项；如果希望由 `config.json` 控制模型路由，就保持为空。

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

代码健康报告默认优先在 Docker 沙盒里运行包内迁移的 `code-health-toolkit`，Docker 不可用时回退到 host 环境，把确定性扫描结果写入 `outputs/code_health/raw/tool_results.json`，再交给 DeepAgents 子 agent 生成报告。默认不安装项目依赖；如果扫描环境安装了 `ruff`、`mypy`、`bandit`、`radon`、`detect-secrets` 等工具，会自动纳入扫描证据。

DeepAgents 运行时使用 `CompositeBackend` 隔离虚拟路径：`/inputs`、`/outputs`、`/local-resources`、`/skills`、`/agent-skills`、`/memories` 和 `/temp` 分别路由到不同 backend。`/agent-skills` 是只读的 per-agent skill 视图，每个子 agent 只能自动加载分配给自己的 skill。

可选能力：

- `APERIO_SCAN_SANDBOX=docker`：使用包内 Dockerfile 构建 `aperio-sandbox:py311-tools`，并在 Docker 沙箱里运行扫描器。项目目录以只读方式挂载。
- `APERIO_SCAN_SANDBOX=auto`：优先 Docker，失败后回退 host 扫描。
- `APERIO_ENABLE_MCP=1`：启用包内 web search MCP；安装 `aperio-agent[mcp]` 后可用。
- `AMAP_API_KEY=...`：在 MCP 开启时额外启用高德地图 MCP 工具。
- `channels.feishu`：迁移自 nanobot 的飞书/Lark 配置结构。接入飞书 runtime gateway 前需要安装 `aperio-agent[integrations]`，并填写 App ID、App Secret、Encrypt Key、Verification Token、`allowFrom`、`groupPolicy`、`domain` 等字段。运行 `aperio gateway feishu` 后会通过飞书 WebSocket 长连接接收文本/富文本/卡片消息，下载图片、文件、音频、视频作为本次运行上传文件，调用当前 Aperio agent，并把最终回答发回飞书。`maxMediaBytes` 控制单个飞书文件下载上限。`streaming: true` 会用 CardKit 展示 agent 运行进度，需要飞书应用具备 `cardkit:card:write` 等卡片写权限；不可用时会自动退回普通最终文本回复。
- `APERIO_FALLBACK_MODEL`、`APERIO_MODEL_CALL_LIMIT`、`APERIO_TOOL_CALL_LIMIT`、`APERIO_MODEL_MAX_RETRIES`、`APERIO_TOOL_MAX_RETRIES`：控制 DeepAgents 的模型降级、调用上限和重试策略。
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
aperio gateway feishu
```

交互模式支持常用 slash 命令：

```text
/help, /?                 查看命令
/exit, /quit, /q          退出
/doctor                   检查环境配置
/init [--force]           创建或覆盖 ~/.aperio/.env
/config, /status          查看当前 CLI、模型和运行配置
/workspace, /pwd          查看运行产物目录
/approval [mode]          查看或设置 prompt|approve|reject
/timeout [seconds]        查看或设置超时时间
/runs [n], /ls [n]        查看最近运行
/artifacts [run_id|last]  查看运行产物和 trace 文件
/channels                 查看飞书等软件渠道配置状态
/last, /answer            重印上一条回复
/history [n]              查看当前 CLI 会话历史
/clear                    清空当前 CLI 会话历史
/retry                    重跑上一条用户请求
/serve [port], /web [p]   启动本地 Web UI
```
