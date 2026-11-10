"""Declarative catalogue of runtime settings (drives `/settings`)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings


@dataclass(frozen=True)
class Setting:
    key: str
    kind: str  # str | int | float | bool | path | text
    description: str
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()


SETTINGS: tuple[Setting, ...] = (
    Setting("mode", "enum", "execution mode", choices=("plan", "build")),
    Setting("shell", "enum", "shell for run_command (auto prefers bash)", choices=("auto", "bash", "sh", "cmd")),
    Setting("provider", "str", "model provider (see /providers)"),
    Setting("model", "str", "model id for the active provider"),
    Setting("temperature", "float", "sampling temperature, 0-2", 0.0, 2.0),
    Setting("max_tokens", "int", "max tokens per reply", 1, 200_000),
    Setting("max_steps", "int", "max model/tool round trips per turn", 1, 1000),
    Setting("compact_keep_turns", "int", "recent user turns kept when compacting", 1, 100),
    Setting("stream", "bool", "stream tokens to the terminal"),
    Setting("timeout", "int", "seconds per model request", 1, 3600),
    Setting("confirm_shell", "bool", "ask before dangerous tools run"),
    Setting("auto_save", "bool", "save the session on exit"),
    Setting("show_tool_results", "bool", "print a line per tool result"),
    Setting("show_usage", "bool", "print context/token/cost line after each turn"),
    Setting("verbose", "bool", "print latency and token usage"),
    Setting("workspace", "path", "root for relative tool paths"),
    Setting("skills_dir", "path", "folder scanned for skills"),
    Setting("plugins_dir", "path", "folder scanned for plugins"),
    Setting("sessions_dir", "path", "folder holding saved sessions"),
    Setting("system_prompt", "text", "custom system prompt (empty = built-in)"),
    Setting("env_file", "path", "where /keys reads and writes API keys"),
    Setting("mcp_config", "path", "MCP server config file (mcp.json)"),
    Setting("mcp_enabled", "bool", "connect MCP servers on startup"),
    Setting("mcp_timeout", "int", "seconds to wait for an MCP server", 1, 600),
)

SETTINGS_BY_KEY: dict[str, Setting] = {setting.key: setting for setting in SETTINGS}

# Shortcuts people actually type.
ALIASES = {
    "temp": "temperature",
    "tokens": "max_tokens",
    "maxtokens": "max_tokens",
    "steps": "max_steps",
    "maxsteps": "max_steps",
    "confirm": "confirm_shell",
    "confirm_shell_tools": "confirm_shell",
    "autosave": "auto_save",
    "tool_results": "show_tool_results",
    "results": "show_tool_results",
    "cwd": "workspace",
    "dir": "workspace",
    "root": "workspace",
    "skills": "skills_dir",
    "plugins": "plugins_dir",
    "sessions": "sessions_dir",
    "env": "env_file",
    "dotenv": "env_file",
    "mcp": "mcp_config",
    "prompt": "system_prompt",
    "system": "system_prompt",
}

# Used by `/settings reset <key>` when the key is absent from config.json.
DEFAULTS: dict[str, Any] = {
    "temperature": 0.7,
    "max_tokens": 4096,
    "max_steps": 32,
    "compact_keep_turns": 4,
    "stream": True,
    "timeout": 180,
    "confirm_shell": True,
    "auto_save": True,
    "show_tool_results": True,
    "show_usage": True,
    "verbose": False,
    "system_prompt": "",
    "mode": "build",
    "shell": "auto",
    "mcp_enabled": True,
    "mcp_timeout": 30,
}

BOOL_WORDS_TRUE = {"1", "true", "on", "yes", "y", "enable", "enabled"}
BOOL_WORDS_FALSE = {"0", "false", "off", "no", "n", "disable", "disabled"}


def resolve_key(name: str) -> str:
    key = name.strip().lower()
    return ALIASES.get(key, key)


def coerce(setting: Setting, raw: str) -> Any:
    """Turn user input into a typed value. Raises ValueError with a clear message."""
    text = raw.strip()
    if setting.kind == "bool":
        lowered = text.lower()
        if lowered in BOOL_WORDS_TRUE:
            return True
        if lowered in BOOL_WORDS_FALSE:
            return False
        raise ValueError(f"expected on/off, got {text!r}")
    if setting.kind == "int":
        try:
            value = int(text, 10)
        except ValueError:
            raise ValueError(f"expected a whole number, got {text!r}") from None
        return _check_range(setting, value)
    if setting.kind == "float":
        try:
            value = float(text)
        except ValueError:
            raise ValueError(f"expected a number, got {text!r}") from None
        return _check_range(setting, value)
    if setting.kind == "path":
        if not text:
            raise ValueError("path must not be empty")
        return Path(text).expanduser().resolve()
    if setting.kind == "enum":
        lowered = text.lower()
        if setting.choices and lowered not in setting.choices:
            raise ValueError(f"must be one of: {', '.join(setting.choices)}")
        return lowered
    if setting.kind == "text":
        if text in ('""', "''"):
            return ""
        return raw
    if setting.kind == "str" and setting.key in ("provider", "model"):
        if not text:
            raise ValueError("value must not be empty")
        return text
    return text


def _check_range(setting: Setting, value: float) -> float:
    if setting.minimum is not None and value < setting.minimum:
        raise ValueError(f"must be >= {_num(setting.minimum)}")
    if setting.maximum is not None and value > setting.maximum:
        raise ValueError(f"must be <= {_num(setting.maximum)}")
    return value


def _num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def get_value(settings: Settings, key: str) -> Any:
    if key == "provider":
        return settings.provider.name
    return getattr(settings, key)


def display_value(settings: Settings, key: str) -> str:
    value = get_value(settings, key)
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, Path):
        return str(value)
    if key == "system_prompt":
        if not str(value).strip():
            return "(built-in)"
        return f"(custom, {len(str(value))} chars)"
    text = str(value)
    return text if text else "(empty)"


def hint(setting: Setting) -> str:
    if setting.kind == "bool":
        return "on | off"
    if setting.kind in ("int", "float"):
        low = _num(setting.minimum) if setting.minimum is not None else "-inf"
        high = _num(setting.maximum) if setting.maximum is not None else "inf"
        kind = "whole number" if setting.kind == "int" else "number"
        return f"{kind} {low}..{high}"
    if setting.kind == "path":
        return "a file system path"
    if setting.kind == "enum" and setting.choices:
        return " | ".join(setting.choices)
    return "free text"
