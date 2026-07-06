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
APERIO_MODEL=openai:deepseek-v4-flash
APERIO_BASE_URL=https://api.deepseek.com
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

Current code-health mode is a lightweight static scan plus LLM summary. It does not require Docker and does not run tests, SAST, or dependency vulnerability scanners yet.
