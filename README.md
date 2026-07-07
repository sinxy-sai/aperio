# Aperio Agent

Aperio Agent is a local Python agent package with:

- terminal chat via `aperio`
- one-shot execution via `aperio run ...`
- local Web UI via `aperio serve`
- backend APIs used by the Web UI

The default CLI behavior matches tools like Codex or Claude Code: run `aperio` and keep chatting in the terminal.

## Install From GitHub

```powershell
pip install git+https://github.com/sinxy-sai/aperio.git
```

For local development:

```powershell
pip install -e .
```

## Configure

```powershell
aperio init
```

Edit `~/.aperio/.env`:

```env
DEEPSEEK_API_KEY=your-key
OPENROUTER_API_KEY=
DASHSCOPE_API_KEY=
MOONSHOT_API_KEY=
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
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_ENCRYPT_KEY=
FEISHU_VERIFICATION_TOKEN=
```

`aperio init` also creates `~/.aperio/config.json`. This file follows the nanobot-style split between runtime defaults, model providers, and software channels:

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
    "dashscope": {
      "apiKey": "${DASHSCOPE_API_KEY}",
      "apiBase": "https://dashscope.aliyuncs.com/compatible-mode/v1"
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

Use `provider: "auto"` to let Aperio infer the provider from the model name and configured keys. Use `provider: "custom"` with `providers.custom.apiBase` for local OpenAI-compatible servers such as vLLM, Ollama, LM Studio, or other gateways. `APERIO_MODEL` and `APERIO_BASE_URL` still work as legacy overrides; leave them empty when you want `config.json` to control provider routing.

## CLI

Start interactive chat:

```powershell
aperio
```

The interactive CLI shows a short welcome panel with the active workspace, model, approval mode, and packaged skills. With `prompt-toolkit` installed, typing `/` opens command completion and typing `$` opens skill completion. Keyboard navigation with arrow keys, Tab, and Enter works everywhere. If you want clickable completion menus and your terminal supports mouse events, set `APERIO_CLI_MOUSE=1`; this may make terminal scrollback follow the prompt in some shells.

Useful interactive commands:

```text
/help       Show commands
/skills     List packaged skills
/doctor     Check environment and config
/runs       List recent runs
/artifacts  List artifacts for the last run
/channels   Show software channel config status
/exit       Quit
```

Run one prompt:

```powershell
aperio run "为智慧校园导航助手写一份 PRD 并评审"
```

Start Web UI:

```powershell
aperio serve --host 127.0.0.1 --port 8088
```

Start Feishu/Lark gateway:

```powershell
pip install "aperio-agent[integrations]"
aperio gateway feishu
```

Check config:

```powershell
aperio doctor
```

## Notes

The default engine is `deepagents`, which runs a package-native DeepAgents router with PRD, code-health, and general-purpose subagents. Set `APERIO_ENGINE=lite` for the simpler fallback engine.

Code-health mode now packages the migrated demo skills and deterministic `code-health-toolkit`. By default it tries the packaged Docker sandbox first and falls back to host scanning if Docker is unavailable. Set `APERIO_SCAN_SANDBOX=host` to force host-only scanning, or `APERIO_SCAN_SANDBOX=docker` to require Docker.

DeepAgents runs on a routed workspace backend. Inputs, outputs, local policy files, packaged skills, per-agent skill views, memory, and temp state are separated by virtual paths, and each subagent receives only its assigned read-only skill source.

Optional MCP tools are disabled by default. Install `aperio-agent[mcp]`, set `APERIO_ENABLE_MCP=1`, and optionally set `AMAP_API_KEY` to enable public web search and Amap tools for agent workflows.

Software channel configuration is now represented in `~/.aperio/config.json`. Feishu/Lark uses `channels.feishu` with App ID, App Secret, Encrypt Key, Verification Token, `allowFrom`, `groupPolicy`, `streaming`, and `domain`. Install optional dependencies with `pip install "aperio-agent[integrations]"`, then run `aperio gateway feishu`.

Feishu setup notes:

- Enable bot capability and event subscription for `im.message.receive_v1`.
- Add required app credentials to `.env` or directly to `config.json`.
- Set `channels.feishu.enabled` to `true`.
- Set `allowFrom` to specific sender open IDs, or `["*"]` for local testing.
- `groupPolicy: "mention"` responds only when the bot is mentioned in groups; `open` responds to all group messages.
- Images, files, audio, and video are downloaded from Feishu and passed to Aperio as run uploads. `maxMediaBytes` limits each downloaded file.
- `streaming: true` uses Feishu CardKit to show progress events while the agent runs. The app needs CardKit card write permission such as `cardkit:card:write`; if CardKit is unavailable, Aperio falls back to sending the final text answer.

DeepAgents runtime guards are enabled by default: model calls are capped, tool calls are capped, model calls retry before failing, and read/search tools retry transient failures. Use `APERIO_FALLBACK_MODEL` to enable model fallback.
