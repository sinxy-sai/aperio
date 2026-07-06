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
APERIO_ENGINE=deepagents
APERIO_MODEL=openai:deepseek-v4-flash
APERIO_BASE_URL=https://api.deepseek.com
APERIO_INSTALL_PROJECT_DEPS=0
APERIO_SCAN_SANDBOX=host
APERIO_ENABLE_MCP=0
AMAP_API_KEY=
```

## CLI

Start interactive chat:

```powershell
aperio
```

Run one prompt:

```powershell
aperio run "为智慧校园导航助手写一份 PRD 并评审"
```

Start Web UI:

```powershell
aperio serve --host 127.0.0.1 --port 8088
```

Check config:

```powershell
aperio doctor
```

## Notes

The default engine is `deepagents`, which runs a package-native DeepAgents router with PRD, code-health, and general-purpose subagents. Set `APERIO_ENGINE=lite` for the simpler fallback engine.

Code-health mode now packages the migrated demo skills and deterministic `code-health-toolkit`. By default it scans on the host and does not install project dependencies. Set `APERIO_SCAN_SANDBOX=docker` to run the scanner in the packaged Docker sandbox, or `auto` to try Docker and fall back to host scanning.

Optional MCP tools are disabled by default. Install `aperio-agent[mcp]`, set `APERIO_ENABLE_MCP=1`, and optionally set `AMAP_API_KEY` to enable public web search and Amap tools for agent workflows.
