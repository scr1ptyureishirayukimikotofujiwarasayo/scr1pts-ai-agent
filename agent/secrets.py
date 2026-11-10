"""API key storage helpers: `.env` parsing/rewriting and masking."""

from __future__ import annotations

import os
import re
from pathlib import Path


def parse_env(path: Path) -> dict[str, str]:
    """Read a .env file into a dict (no expansion, no interpolation)."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def set_env_var(path: Path, key: str, value: str) -> Path:
    """Set `key` in `path` (keeping comments/order) and in os.environ."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else _template()
    pattern = _assignment(key)
    output: list[str] = []
    replaced = False
    for line in lines:
        if pattern.match(line):
            if not replaced:
                output.append(f"{key}={_quote(value)}")
                replaced = True
            continue  # drop duplicate assignments
        output.append(line)
    if not replaced:
        while output and not output[-1].strip():
            output.pop()
        output.append(f"{key}={_quote(value)}")
    _write(path, output)
    os.environ[key] = value
    return path


def remove_env_var(path: Path, key: str) -> bool:
    """Delete `key` from `path` and from os.environ. Returns True if anything changed."""
    removed = False
    if path.is_file():
        pattern = _assignment(key)
        lines = path.read_text(encoding="utf-8").splitlines()
        kept = []
        for line in lines:
            if pattern.match(line):
                removed = True
                continue
            kept.append(line)
        if removed:
            _write(path, kept)
    if os.environ.pop(key, None) is not None:
        removed = True
    return removed


def mask(value: str) -> str:
    if not value:
        return "(unset)"
    if len(value) <= 8:
        return "…" + value[-2:]
    return f"{value[:6]}…{value[-4:]}"


def _assignment(key: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*(?:export\s+)?{re.escape(key)}\s*=")


def _quote(value: str) -> str:
    if not value or re.search(r"[\s#'\"]", value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def _write(path: Path, lines: list[str]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _template() -> list[str]:
    return [
        "# API keys for the python ai agent. Managed by /keys.",
        "# This file is git-ignored; keep it out of version control.",
        "",
    ]
