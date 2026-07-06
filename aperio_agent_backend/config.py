from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parent
PACKAGE_ROOT = BACKEND_DIR.parent
APERIO_HOME = Path(os.environ.get("APERIO_HOME", Path.home() / ".aperio")).expanduser().resolve()
PROJECT_ROOT = Path(os.environ.get("APERIO_PROJECT_ROOT", Path.cwd())).expanduser().resolve()
WORKSPACE_ROOT = Path(
    os.environ.get("APERIO_WORKSPACE_ROOT", APERIO_HOME / "workspace")
).expanduser().resolve()

load_dotenv(BACKEND_DIR / ".env")
load_dotenv(PACKAGE_ROOT / ".env")
load_dotenv(APERIO_HOME / ".env")


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


def get_engine_name() -> str:
    return os.environ.get("APERIO_ENGINE", "deepagents").strip().lower()


def get_install_project_deps() -> bool:
    return os.environ.get("APERIO_INSTALL_PROJECT_DEPS", "0").strip().lower() in {"1", "true", "yes", "on"}


def get_scan_sandbox_mode() -> str:
    value = os.environ.get("APERIO_SCAN_SANDBOX", "host").strip().lower()
    return value if value in {"host", "docker", "auto"} else "host"


def get_sandbox_image() -> str:
    return os.environ.get("APERIO_SANDBOX_IMAGE", "aperio-sandbox:py311-tools").strip()


def get_enable_mcp_tools() -> bool:
    return os.environ.get("APERIO_ENABLE_MCP", "0").strip().lower() in {"1", "true", "yes", "on"}


def get_amap_api_key() -> str:
    return os.environ.get("AMAP_API_KEY", "").strip()
