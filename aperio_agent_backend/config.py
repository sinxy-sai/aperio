from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
WORKSPACE_ROOT = BACKEND_DIR / "workspace"

load_dotenv(BACKEND_DIR / ".env")
load_dotenv(PROJECT_ROOT / ".env")


def get_api_key() -> str:
    return (
        os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    ).strip()


def get_model_name() -> str:
    return (
        os.environ.get("APERIO_MODEL")
        or os.environ.get("OPENAI_MODEL")
        or "openai:deepseek-v4-flash"
    ).strip()


def get_base_url() -> str:
    return (
        os.environ.get("APERIO_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or "https://api.deepseek.com"
    ).strip()
