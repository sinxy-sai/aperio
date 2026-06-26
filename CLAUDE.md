# Aperio Project

## Python Environment

The `demo/` folder uses the **conda environment `llm-dev`**.

All Python commands for demo scripts must be run with:
```bash
conda activate llm-dev
python demo/xx.py
```

Key packages in llm-dev:
- `deepagents` 0.6.10
- `langchain` 1.3.9
- `langchain-openai` 1.3.2 (DeepSeek uses OpenAI-compatible API)
- `langgraph`

## Demo Scripts

All demo scripts are standalone `.py` files under `demo/`, runnable with:
```bash
conda activate llm-dev
python demo/<script>.py
```

API key is loaded from `demo/.env` via `load_dotenv`.

Model: `openai:deepseek-v4-flash` via `init_chat_model` with `base_url="https://api.deepseek.com"`.

Workspace outputs go under `demo/workspace_XX/` corresponding to each demo number.
