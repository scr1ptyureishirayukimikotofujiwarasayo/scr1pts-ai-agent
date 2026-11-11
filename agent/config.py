"""Configuration loading: providers, .env, config.json and CLI overrides."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Provider catalogue
#
# Every provider below speaks the OpenAI "chat/completions" wire format, which
# is supported by OpenRouter, OpenCode, OpenAI, Anthropic (compat layer),
# Google (compat layer), Groq, DeepSeek, Mistral, xAI, Ollama, LM Studio, ...
# Unknown providers can be added in config.json without touching the code.
#
#   base_url      default API root (must NOT include /chat/completions)
#   base_url_env  env var that overrides base_url
#   api_key_env   env var holding the key (list allowed, first match wins)
#   default_model model used when no model is chosen
#   headers       extra headers sent with every request
#   requires_key  whether a missing key is a hard error
# ---------------------------------------------------------------------------

DEFAULT_PROVIDERS: dict[str, dict[str, Any]] = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": ["OPENROUTER_API_KEY"],
        "default_model": "anthropic/claude-sonnet-4.5",
        "headers": {
            "HTTP-Referer": "https://github.com/local/python-ai-agent",
            "X-Title": "Python AI Agent",
        },
    },
    # OpenCode "Go" plan. Endpoint differs per plan; override with
    # OPENCODE_GO_BASE_URL if your dashboard shows another URL.
    "opencode-go": {
        "base_url": "https://opencode.ai/zen/v1",
        "base_url_env": "OPENCODE_GO_BASE_URL",
        "api_key_env": ["OPENCODE_GO_API_KEY", "OPENCODE_API_KEY"],
        "default_model": "claude-sonnet-4-5",
    },
    "opencode": {
        "base_url": "https://opencode.ai/zen/v1",
        "base_url_env": "OPENCODE_BASE_URL",
        "api_key_env": ["OPENCODE_API_KEY", "OPENCODE_ZEN_API_KEY"],
        "default_model": "kimi-k2",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key_env": ["OPENAI_API_KEY"],
        "default_model": "gpt-4.1-mini",
    },
    "venice": {
        "base_url": "https://api.venice.ai/api/v1",
        "base_url_env": "VENICE_BASE_URL",
        "api_key_env": ["VENICE_API_KEY"],
        "default_model": "venice-uncensored",
    },
    "perplexity": {
        "base_url": "https://api.perplexity.ai",
        "base_url_env": "PERPLEXITY_BASE_URL",
        "api_key_env": ["PERPLEXITY_API_KEY"],
        "default_model": "sonar-pro",
    },
    "moonshot": {
        "base_url": "https://api.moonshot.ai/v1",
        "base_url_env": "MOONSHOT_BASE_URL",
        "api_key_env": ["MOONSHOT_API_KEY", "KIMI_API_KEY"],
        "default_model": "kimi-k2-0905-preview",
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "base_url_env": "ZHIPU_BASE_URL",
        "api_key_env": ["ZHIPU_API_KEY", "GLM_API_KEY"],
        "default_model": "glm-4.6",
    },
    "dashscope": {
        "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "base_url_env": "DASHSCOPE_BASE_URL",
        "api_key_env": ["DASHSCOPE_API_KEY", "QWEN_API_KEY"],
        "default_model": "qwen-plus",
    },
    "novita": {
        "base_url": "https://api.novita.ai/v3/openai",
        "base_url_env": "NOVITA_BASE_URL",
        "api_key_env": ["NOVITA_API_KEY"],
        "default_model": "meta-llama/llama-3.3-70b-instruct",
    },
    "nebius": {
        "base_url": "https://api.studio.nebius.ai/v1",
        "base_url_env": "NEBIUS_BASE_URL",
        "api_key_env": ["NEBIUS_API_KEY"],
        "default_model": "meta-llama/Llama-3.3-70B-Instruct",
    },
    "hyperbolic": {
        "base_url": "https://api.hyperbolic.xyz/v1",
        "base_url_env": "HYPERBOLIC_BASE_URL",
        "api_key_env": ["HYPERBOLIC_API_KEY"],
        "default_model": "meta-llama/Llama-3.3-70B-Instruct",
    },
    "github": {
        "base_url": "https://models.github.ai/inference",
        "base_url_env": "GITHUB_MODELS_BASE_URL",
        "api_key_env": ["GITHUB_TOKEN", "GITHUB_MODELS_TOKEN"],
        "default_model": "openai/gpt-4.1-mini",
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "api_key_env": ["ANTHROPIC_API_KEY"],
        "default_model": "claude-sonnet-4-5",
    },
    "google": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "api_key_env": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "default_model": "gemini-2.5-flash",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": ["GROQ_API_KEY"],
        "default_model": "llama-3.3-70b-versatile",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": ["DEEPSEEK_API_KEY"],
        "default_model": "deepseek-chat",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "api_key_env": ["MISTRAL_API_KEY"],
        "default_model": "mistral-large-latest",
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "api_key_env": ["TOGETHER_API_KEY"],
        "default_model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    },
    "fireworks": {
        "base_url": "https://api.fireworks.ai/inference/v1",
        "api_key_env": ["FIREWORKS_API_KEY"],
        "default_model": "accounts/fireworks/models/llama-v3p3-70b-instruct",
    },
    "xai": {
        "base_url": "https://api.x.ai/v1",
        "api_key_env": ["XAI_API_KEY"],
        "default_model": "grok-4",
    },
    "cerebras": {
        "base_url": "https://api.cerebras.ai/v1",
        "api_key_env": ["CEREBRAS_API_KEY"],
        "default_model": "qwen-3-235b-a22b-instruct-2507",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "base_url_env": "OLLAMA_BASE_URL",
        "api_key_env": ["OLLAMA_API_KEY"],
        "default_model": "qwen2.5-coder:7b",
        "requires_key": False,
    },
    "lmstudio": {
        "base_url": "http://localhost:1234/v1",
        "base_url_env": "LMSTUDIO_BASE_URL",
        "api_key_env": ["LMSTUDIO_API_KEY"],
        "default_model": "local-model",
        "requires_key": False,
    },
    "custom": {
        "base_url": "http://localhost:8000/v1",
        "base_url_env": "CUSTOM_BASE_URL",
        "api_key_env": ["CUSTOM_API_KEY"],
        "default_model": "default",
        "requires_key": False,
    },
}


class ConfigError(RuntimeError):
    """Raised for invalid / incomplete configuration."""


def load_dotenv(path: Path) -> None:
    """Minimal .env parser. Existing environment variables win."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass
class Provider:
    name: str
    base_url: str
    model: str
    api_key: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    requires_key: bool = True

    @property
    def endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"


@dataclass
class Settings:
    provider: Provider
    model: str
    temperature: float = 0.7
    max_tokens: int = 4096
    max_steps: int = 32
    compact_keep_turns: int = 4
    stream: bool = True
    timeout: int = 180
    workspace: Path = ROOT
    skills_dir: Path = ROOT / "skills"
    plugins_dir: Path = ROOT / "plugins"
    sessions_dir: Path = ROOT / "sessions"
    system_prompt: str = ""
    confirm_shell: bool = True
    auto_save: bool = True
    show_tool_results: bool = True
    show_usage: bool = True
    show_reasoning: bool = False
    verbose: bool = False
    mode: str = "build"  # "plan" (read-only) or "build"
    shell: str = "auto"  # auto | bash | sh | cmd (run_command only)
    reasoning_effort: str = "auto"  # auto | minimal | low | medium | high
    web_search_backend: str = "auto"  # auto | duckduckgo | searxng
    searxng_url: str = ""
    web_search_results: int = 5
    env_file: Path = ROOT / ".env"
    mcp_config: Path = ROOT / "mcp.json"
    mcp_enabled: bool = True
    mcp_timeout: int = 30
    config_path: Path = ROOT / "config.json"
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)

    def with_provider(
        self, name: str, model: str | None = None, require_key: bool = False
    ) -> "Settings":
        provider = build_provider(name, self.providers, require_key=require_key)
        return replace(self, provider=provider, model=model or provider.model)


# ---------------------------------------------------------------------------
# Building providers and settings
# ---------------------------------------------------------------------------


def _env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def build_provider(
    name: str, providers: dict[str, dict[str, Any]], require_key: bool = True
) -> Provider:
    name = name.strip().lower()
    spec = providers.get(name)
    if spec is None:
        known = ", ".join(sorted(providers))
        raise ConfigError(f"Unknown provider {name!r}. Known providers: {known}")

    base_url = _env(spec.get("base_url_env") or "", "") or spec.get("base_url", "")
    if not base_url:
        raise ConfigError(f"Provider {name!r} has no base_url")

    key_envs = spec.get("api_key_env") or []
    if isinstance(key_envs, str):
        key_envs = [key_envs]
    api_key = _env(*key_envs) or spec.get("api_key", "")

    requires_key = spec.get("requires_key", True)
    if require_key and requires_key and not api_key:
        env_hint = " or ".join(key_envs) or "an api key"
        raise ConfigError(
            f"No API key for provider {name!r}. Set {env_hint} in your .env file "
            f"(see .env.example)."
        )

    return Provider(
        name=name,
        base_url=base_url,
        model=spec.get("default_model", ""),
        api_key=api_key,
        headers={str(k): str(v) for k, v in (spec.get("headers") or {}).items()},
        requires_key=requires_key,
    )


def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a JSON object")
    return data


def save_config_file(path: Path, data: dict[str, Any]) -> Path:
    """Atomically write a config JSON document."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_settings(
    config_path: Path | None = None,
    provider: str | None = None,
    model: str | None = None,
    workspace: Path | None = None,
    allow_missing_key: bool = False,
    **overrides: Any,
) -> Settings:
    config_file = Path(config_path or ROOT / "config.json").resolve()

    file_cfg = load_config_file(config_file)
    env_file = Path(file_cfg.get("env_file") or ROOT / ".env").expanduser().resolve()
    load_dotenv(env_file)
    providers = _deep_merge(DEFAULT_PROVIDERS, file_cfg.get("providers") or {})

    provider_name = (
        provider
        or os.environ.get("AGENT_PROVIDER")
        or file_cfg.get("provider")
        or "openrouter"
    )
    built = build_provider(
        str(provider_name), providers, require_key=not allow_missing_key
    )
    chosen_model = model or os.environ.get("AGENT_MODEL") or file_cfg.get("model") or built.model

    def path(key: str, default: Path) -> Path:
        raw = file_cfg.get(key)
        return Path(raw).expanduser().resolve() if raw else default

    workspace_dir = workspace or path("workspace", ROOT)

    settings = Settings(
        provider=built,
        model=chosen_model,
        temperature=float(file_cfg.get("temperature", 0.7)),
        max_tokens=int(file_cfg.get("max_tokens", 4096)),
        max_steps=int(file_cfg.get("max_steps", 32)),
        compact_keep_turns=max(1, int(file_cfg.get("compact_keep_turns", 4))),
        stream=bool(file_cfg.get("stream", True)),
        timeout=int(file_cfg.get("timeout", 180)),
        workspace=Path(workspace_dir).resolve(),
        skills_dir=path("skills_dir", ROOT / "skills"),
        plugins_dir=path("plugins_dir", ROOT / "plugins"),
        sessions_dir=path("sessions_dir", ROOT / "sessions"),
        system_prompt=str(file_cfg.get("system_prompt", "")),
        confirm_shell=bool(file_cfg.get("confirm_shell", True)),
        auto_save=bool(file_cfg.get("auto_save", True)),
        show_tool_results=bool(file_cfg.get("show_tool_results", True)),
        show_usage=bool(file_cfg.get("show_usage", True)),
        show_reasoning=bool(file_cfg.get("show_reasoning", False)),
        verbose=bool(file_cfg.get("verbose", False)),
        mode=str(file_cfg.get("mode", "build")).strip().lower(),
        shell=str(file_cfg.get("shell", "auto")).strip().lower(),
        reasoning_effort=str(file_cfg.get("reasoning_effort", "auto")).strip().lower(),
        web_search_backend=str(file_cfg.get("web_search_backend", "auto")).strip().lower(),
        searxng_url=str(file_cfg.get("searxng_url", "") or "").strip(),
        web_search_results=int(file_cfg.get("web_search_results", 5)),
        env_file=env_file,
        mcp_config=path("mcp_config", ROOT / "mcp.json"),
        mcp_enabled=bool(file_cfg.get("mcp_enabled", True)),
        mcp_timeout=int(file_cfg.get("mcp_timeout", 30)),
        config_path=config_file,
        providers=providers,
    )
    if settings.mode not in ("plan", "build"):
        raise ConfigError(f"mode must be 'plan' or 'build', got {settings.mode!r}")
    if settings.shell not in ("auto", "bash", "sh", "cmd"):
        raise ConfigError(
            f"shell must be one of auto, bash, sh, cmd, got {settings.shell!r}"
        )
    if settings.reasoning_effort not in ("auto", "minimal", "low", "medium", "high"):
        raise ConfigError(
            "reasoning_effort must be one of auto, minimal, low, medium, high, "
            f"got {settings.reasoning_effort!r}"
        )
    if settings.web_search_backend not in ("auto", "duckduckgo", "searxng"):
        raise ConfigError(
            "web_search_backend must be auto, duckduckgo or searxng, "
            f"got {settings.web_search_backend!r}"
        )
    settings.web_search_results = max(1, min(10, int(settings.web_search_results)))
    for key, value in overrides.items():
        if value is not None and hasattr(settings, key):
            setattr(settings, key, value)
    return settings
