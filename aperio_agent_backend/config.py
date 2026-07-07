from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - minimal local checks may not install optional runtime deps
    def load_dotenv(*_: Any, **__: Any) -> bool:
        return False


BACKEND_DIR = Path(__file__).resolve().parent
PACKAGE_ROOT = BACKEND_DIR.parent

load_dotenv(BACKEND_DIR / ".env")
load_dotenv(PACKAGE_ROOT / ".env")


def _env_or(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


APERIO_HOME = Path(_env_or("APERIO_HOME", str(Path.home() / ".aperio"))).expanduser().resolve()
load_dotenv(APERIO_HOME / ".env")

CONFIG_PATH = Path(_env_or("APERIO_CONFIG_PATH", str(APERIO_HOME / "config.json"))).expanduser().resolve()
PROJECT_ROOT = Path(_env_or("APERIO_PROJECT_ROOT", str(Path.cwd()))).expanduser().resolve()
WORKSPACE_ROOT = Path(
    _env_or("APERIO_WORKSPACE_ROOT", str(APERIO_HOME / "workspace"))
).expanduser().resolve()
MEMORY_DB_PATH = Path(
    _env_or("APERIO_MEMORY_DB", str(APERIO_HOME / "memory.sqlite3"))
).expanduser().resolve()
KNOWLEDGE_DB_PATH = Path(
    _env_or("APERIO_KNOWLEDGE_DB", str(APERIO_HOME / "knowledge.sqlite3"))
).expanduser().resolve()


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    keywords: tuple[str, ...]
    env_key: str
    default_api_base: str = ""
    display_name: str = ""
    is_gateway: bool = False
    is_local: bool = False
    detect_by_key_prefix: str = ""
    detect_by_base_keyword: str = ""
    strip_model_prefix: bool = False

    @property
    def label(self) -> str:
        return self.display_name or self.name


PROVIDER_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec("custom", (), "", display_name="Custom"),
    ProviderSpec("openrouter", ("openrouter",), "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1", "OpenRouter", is_gateway=True, detect_by_key_prefix="sk-or-", detect_by_base_keyword="openrouter"),
    ProviderSpec("aihubmix", ("aihubmix",), "OPENAI_API_KEY", "https://aihubmix.com/v1", "AiHubMix", is_gateway=True, detect_by_base_keyword="aihubmix", strip_model_prefix=True),
    ProviderSpec("siliconflow", ("siliconflow",), "SILICONFLOW_API_KEY", "https://api.siliconflow.cn/v1", "SiliconFlow", is_gateway=True, detect_by_base_keyword="siliconflow"),
    ProviderSpec("volcengine", ("volcengine", "volces", "ark"), "ARK_API_KEY", "https://ark.cn-beijing.volces.com/api/v3", "VolcEngine", is_gateway=True, detect_by_base_keyword="volces"),
    ProviderSpec("deepseek", ("deepseek",), "DEEPSEEK_API_KEY", "https://api.deepseek.com", "DeepSeek"),
    ProviderSpec("openai", ("openai", "gpt"), "OPENAI_API_KEY", display_name="OpenAI"),
    ProviderSpec("gemini", ("gemini",), "GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai/", "Gemini"),
    ProviderSpec("dashscope", ("qwen", "dashscope"), "DASHSCOPE_API_KEY", "https://dashscope.aliyuncs.com/compatible-mode/v1", "DashScope"),
    ProviderSpec("moonshot", ("moonshot", "kimi"), "MOONSHOT_API_KEY", "https://api.moonshot.ai/v1", "Moonshot"),
    ProviderSpec("minimax", ("minimax",), "MINIMAX_API_KEY", "https://api.minimax.io/v1", "MiniMax"),
    ProviderSpec("mistral", ("mistral",), "MISTRAL_API_KEY", "https://api.mistral.ai/v1", "Mistral"),
    ProviderSpec("stepfun", ("stepfun", "step"), "STEPFUN_API_KEY", "https://api.stepfun.com/v1", "StepFun"),
    ProviderSpec("zhipu", ("zhipu", "glm", "zai"), "ZAI_API_KEY", "https://open.bigmodel.cn/api/paas/v4", "Zhipu AI"),
    ProviderSpec("groq", ("groq",), "GROQ_API_KEY", "https://api.groq.com/openai/v1", "Groq"),
    ProviderSpec("qianfan", ("qianfan", "ernie"), "QIANFAN_API_KEY", "https://qianfan.baidubce.com/v2", "Qianfan"),
    ProviderSpec("ollama", ("ollama",), "OLLAMA_API_KEY", "http://localhost:11434/v1", "Ollama", is_local=True, detect_by_base_keyword="11434"),
    ProviderSpec("lm_studio", ("lm-studio", "lmstudio", "lm_studio"), "LM_STUDIO_API_KEY", "http://localhost:1234/v1", "LM Studio", is_local=True, detect_by_base_keyword="1234"),
    ProviderSpec("vllm", ("vllm",), "HOSTED_VLLM_API_KEY", display_name="vLLM/Local", is_local=True),
)


def default_config_data() -> dict[str, Any]:
    return {
        "agents": {
            "defaults": {
                "model": "deepseek-v4-flash",
                "provider": "deepseek",
                "fallbackModel": "",
            },
        },
        "providers": {
            "deepseek": {
                "apiKey": "${DEEPSEEK_API_KEY}",
                "apiBase": "https://api.deepseek.com",
            },
            "openrouter": {
                "apiKey": "${OPENROUTER_API_KEY}",
                "apiBase": "https://openrouter.ai/api/v1",
            },
            "dashscope": {
                "apiKey": "${DASHSCOPE_API_KEY}",
                "apiBase": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            },
            "custom": {
                "apiKey": "",
                "apiBase": "",
            },
        },
        "channels": {
            "feishu": {
                "enabled": False,
                "appId": "${FEISHU_APP_ID}",
                "appSecret": "${FEISHU_APP_SECRET}",
                "encryptKey": "${FEISHU_ENCRYPT_KEY}",
                "verificationToken": "${FEISHU_VERIFICATION_TOKEN}",
                "allowFrom": [],
                "groupPolicy": "mention",
                "streaming": True,
                "domain": "feishu",
                "maxMediaBytes": 26214400,
            }
        },
    }


def load_app_config() -> dict[str, Any]:
    data = default_config_data()
    if CONFIG_PATH.exists():
        try:
            loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = _deep_merge(data, loaded)
        except (OSError, json.JSONDecodeError):
            pass
    return _resolve_env_refs(data)


def save_default_config(path: Path | None = None, *, force: bool = False) -> Path:
    target = path or CONFIG_PATH
    if target.exists() and not force:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(default_config_data(), indent=2, ensure_ascii=False), encoding="utf-8")
    return target


def get_config_path() -> Path:
    return CONFIG_PATH


def get_provider_specs() -> tuple[ProviderSpec, ...]:
    return PROVIDER_SPECS


def get_active_provider_name(model: str | None = None) -> str:
    _, name, _ = _match_provider(model or get_raw_model_name())
    return name


def get_provider_config(name: str) -> dict[str, Any]:
    providers = _dict(load_app_config().get("providers"))
    return _dict(providers.get(_normalize_provider_name(name)))


def get_channel_config(name: str) -> dict[str, Any]:
    channels = _dict(load_app_config().get("channels"))
    return _dict(channels.get(name))


def get_enabled_channels() -> list[str]:
    channels = _dict(load_app_config().get("channels"))
    return [name for name, config in channels.items() if isinstance(config, dict) and bool(config.get("enabled"))]


def get_api_key() -> str:
    model = get_raw_model_name()
    provider, _, spec = _match_provider(model)
    key = _str(provider.get("apiKey") or provider.get("api_key"))
    if key:
        return key
    if spec and spec.env_key:
        key = os.environ.get(spec.env_key, "")
    return (key or os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY") or "").strip()


def get_model_name() -> str:
    return _langchain_openai_model_name(get_raw_model_name())


def get_raw_model_name() -> str:
    defaults = _dict(_dict(load_app_config().get("agents")).get("defaults"))
    return (
        _env_or("APERIO_MODEL")
        or _env_or("OPENAI_MODEL")
        or _str(defaults.get("model"))
        or "deepseek-v4-flash"
    ).strip()


def get_fallback_model_name() -> str:
    defaults = _dict(_dict(load_app_config().get("agents")).get("defaults"))
    value = _env_or("APERIO_FALLBACK_MODEL") or _str(defaults.get("fallbackModel") or defaults.get("fallback_model"))
    return _langchain_openai_model_name(value).strip() if value else ""


def get_base_url() -> str:
    legacy_override = (_env_or("APERIO_BASE_URL") or _env_or("OPENAI_BASE_URL") or "").strip()
    if legacy_override:
        return legacy_override
    model = get_raw_model_name()
    provider, _, spec = _match_provider(model)
    base = _str(provider.get("apiBase") or provider.get("api_base"))
    if base:
        return base
    if spec and spec.default_api_base:
        return spec.default_api_base
    return "https://api.deepseek.com"


def get_engine_name() -> str:
    return _env_or("APERIO_ENGINE", "deepagents").strip().lower()


def get_install_project_deps() -> bool:
    return os.environ.get("APERIO_INSTALL_PROJECT_DEPS", "0").strip().lower() in {"1", "true", "yes", "on"}


def get_scan_sandbox_mode() -> str:
    value = os.environ.get("APERIO_SCAN_SANDBOX", "auto").strip().lower()
    return value if value in {"host", "docker", "auto"} else "host"


def get_sandbox_image() -> str:
    return os.environ.get("APERIO_SANDBOX_IMAGE", "aperio-sandbox:py311-tools").strip()


def get_enable_mcp_tools() -> bool:
    return os.environ.get("APERIO_ENABLE_MCP", "0").strip().lower() in {"1", "true", "yes", "on"}


def get_memory_enabled() -> bool:
    return os.environ.get("APERIO_MEMORY_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


def get_memory_db_path() -> Path:
    return MEMORY_DB_PATH


def get_knowledge_enabled() -> bool:
    return os.environ.get("APERIO_KNOWLEDGE_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


def get_knowledge_db_path() -> Path:
    return KNOWLEDGE_DB_PATH


def get_safe_execution_enabled() -> bool:
    return os.environ.get("APERIO_SAFE_EXECUTION_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


def get_extensions_enabled() -> bool:
    return os.environ.get("APERIO_EXTENSIONS_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


def get_amap_api_key() -> str:
    return os.environ.get("AMAP_API_KEY", "").strip()


def get_model_call_limit() -> int:
    return _int_env("APERIO_MODEL_CALL_LIMIT", 100)


def get_tool_call_limit() -> int:
    return _int_env("APERIO_TOOL_CALL_LIMIT", 160)


def get_model_max_retries() -> int:
    return _int_env("APERIO_MODEL_MAX_RETRIES", 3)


def get_tool_max_retries() -> int:
    return _int_env("APERIO_TOOL_MAX_RETRIES", 2)


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(_env_or(name, str(default)).strip()))
    except ValueError:
        return default


def _match_provider(model: str) -> tuple[dict[str, Any], str, ProviderSpec | None]:
    config = load_app_config()
    defaults = _dict(_dict(config.get("agents")).get("defaults"))
    providers = _dict(config.get("providers"))
    forced = _normalize_provider_name(_env_or("APERIO_PROVIDER") or _str(defaults.get("provider")) or "auto")
    model_lower = (model or "").lower()
    model_prefix = _normalize_provider_name(model_lower.split("/", 1)[0]) if "/" in model_lower else ""

    if forced and forced != "auto":
        spec = _find_provider_spec(forced)
        return _provider_with_env(providers, forced, spec), forced, spec

    for spec in PROVIDER_SPECS:
        provider = _provider_with_env(providers, spec.name, spec)
        if model_prefix and model_prefix == spec.name and _provider_is_configured(provider, spec):
            return provider, spec.name, spec

    for spec in PROVIDER_SPECS:
        provider = _provider_with_env(providers, spec.name, spec)
        if any(keyword in model_lower for keyword in spec.keywords) and _provider_is_configured(provider, spec):
            return provider, spec.name, spec

    for spec in PROVIDER_SPECS:
        provider = _provider_with_env(providers, spec.name, spec)
        api_base = _str(provider.get("apiBase") or provider.get("api_base"))
        if spec.is_local and api_base and (not spec.detect_by_base_keyword or spec.detect_by_base_keyword in api_base):
            return provider, spec.name, spec

    for spec in PROVIDER_SPECS:
        provider = _provider_with_env(providers, spec.name, spec)
        if _provider_is_configured(provider, spec):
            return provider, spec.name, spec

    spec = _find_provider_spec("deepseek")
    return _provider_with_env(providers, "deepseek", spec), "deepseek", spec


def _provider_with_env(providers: dict[str, Any], name: str, spec: ProviderSpec | None) -> dict[str, Any]:
    provider = _dict(providers.get(name))
    if not _str(provider.get("apiKey") or provider.get("api_key")) and spec and spec.env_key:
        env_key = os.environ.get(spec.env_key, "")
        if env_key:
            provider["apiKey"] = env_key
    return provider


def _provider_is_configured(provider: dict[str, Any], spec: ProviderSpec | None) -> bool:
    key = _str(provider.get("apiKey") or provider.get("api_key"))
    base = _str(provider.get("apiBase") or provider.get("api_base"))
    if key:
        return True
    return bool(spec and spec.is_local and (base or spec.default_api_base))


def _find_provider_spec(name: str) -> ProviderSpec | None:
    normalized = _normalize_provider_name(name)
    for spec in PROVIDER_SPECS:
        if spec.name == normalized:
            return spec
    return None


def _langchain_openai_model_name(model: str) -> str:
    model = _str(model)
    if not model:
        return ""
    if ":" in model:
        return model
    _, provider_name, spec = _match_provider(model)
    if spec and spec.strip_model_prefix and "/" in model:
        model = model.split("/", 1)[1]
    if provider_name and provider_name != "openai" and model.startswith(f"{provider_name}/"):
        model = model.split("/", 1)[1]
    return f"openai:{model}"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _resolve_env_refs(value: Any) -> Any:
    if isinstance(value, str):
        return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", lambda match: os.environ.get(match.group(1), ""), value)
    if isinstance(value, dict):
        return {key: _resolve_env_refs(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_env_refs(item) for item in value]
    return value


def _normalize_provider_name(name: str) -> str:
    return name.strip().lower().replace("-", "_")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
