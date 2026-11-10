"""Minimal MCP (Model Context Protocol) client over stdio.

Servers are configured in `mcp.json` (or a `mcpServers` block in config.json):

    {
      "mcpServers": {
        "filesystem": {
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
          "env": {"SOME_TOKEN": "..."},
          "cwd": ".",
          "timeout": 30
        }
      }
    }

Each server's tools are registered as `mcp__<server>__<tool>` and become normal
agent tools. Only stdio servers are supported; `url`/SSE entries are reported
as configuration errors.
"""

from __future__ import annotations

import collections
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .tools import Registry
from .util import VERSION

PROTOCOL_VERSION = "2025-06-18"

_VAR_PATTERN = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")


class MCPError(RuntimeError):
    """Raised when a server misbehaves or is unreachable."""


@dataclass
class MCPServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    timeout: float = 30.0


def load_mcp_config(path: Path) -> tuple[dict[str, MCPServerConfig], list[str]]:
    """Parse an MCP config file. Returns (servers, errors)."""
    path = Path(path)
    servers: dict[str, MCPServerConfig] = {}
    errors: list[str] = []
    if not path.is_file():
        return servers, errors
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"{path}: {exc}"]
    if not isinstance(data, dict):
        return {}, [f"{path}: expected a JSON object"]

    block = data.get("mcpServers", data.get("servers", data))
    if not isinstance(block, dict):
        return {}, [f"{path}: 'mcpServers' must be an object"]

    for name, entry in block.items():
        if not isinstance(entry, dict):
            errors.append(f"{name}: expected an object")
            continue
        if entry.get("disabled") is True or entry.get("enabled") is False:
            continue
        kind = str(entry.get("type") or "").lower()
        if entry.get("url") or kind in ("http", "sse", "streamable-http"):
            errors.append(f"{name}: HTTP/SSE servers are not supported; use a stdio command")
            continue
        command = entry.get("command")
        if not command:
            errors.append(f"{name}: missing 'command'")
            continue
        raw_args = entry.get("args") or []
        if not isinstance(raw_args, list):
            errors.append(f"{name}: 'args' must be a list")
            continue
        raw_env = entry.get("env") or {}
        if not isinstance(raw_env, dict):
            errors.append(f"{name}: 'env' must be an object")
            continue
        try:
            timeout = float(entry.get("timeout", 30))
        except (TypeError, ValueError):
            timeout = 30.0

        missing: list[str] = []
        resolved_env = {
            str(key): _expand(str(value), missing) for key, value in raw_env.items()
        }
        if missing:
            names = ", ".join(sorted(set(missing)))
            errors.append(
                f"{name}: environment variable(s) not set: {names} "
                "(put them in .env or your shell); server skipped"
            )
            continue

        servers[str(name)] = MCPServerConfig(
            name=str(name),
            command=_expand(str(command)),
            args=[_expand(str(arg)) for arg in raw_args],
            env=resolved_env,
            cwd=_expand(str(entry["cwd"])) if entry.get("cwd") else None,
            timeout=timeout,
        )
    return servers, errors


def _expand(value: str, missing: list[str] | None = None) -> str:
    """Expand $VAR / ${VAR} using os.environ (unknown vars are left literal)."""

    def replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        if name in os.environ:
            return os.environ[name]
        if missing is not None:
            missing.append(name)
        return match.group(0)

    return _VAR_PATTERN.sub(replace, value)


class MCPClient:
    """One stdio MCP server: process, JSON-RPC transport, tool list."""

    def __init__(self, config: MCPServerConfig, log: Callable[[str], None] = print) -> None:
        self.config = config
        self.log = log
        self.proc: subprocess.Popen[str] | None = None
        self.tools: list[dict[str, Any]] = []
        self.server_info: dict[str, Any] = {}
        self._inbox: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stderr: collections.deque[str] = collections.deque(maxlen=40)
        self._next_id = 0

    # -- lifecycle ----------------------------------------------------------

    def start(self, timeout: float | None = None) -> "MCPClient":
        command = _resolve_command(self.config.command)
        argv = (command if isinstance(command, list) else [command]) + self.config.args
        env = dict(os.environ)
        env.update(self.config.env)
        try:
            self.proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                cwd=self.config.cwd,
            )
        except OSError as exc:
            raise MCPError(f"cannot start {self.config.command!r}: {exc}") from exc

        threading.Thread(target=self._read_stdout, name=f"mcp-{self.config.name}-out", daemon=True).start()
        threading.Thread(target=self._read_stderr, name=f"mcp-{self.config.name}-err", daemon=True).start()

        result = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "python-ai-agent", "version": VERSION},
            },
            timeout=timeout or self.config.timeout,
        )
        self.server_info = result.get("serverInfo") or {}
        self.notify("notifications/initialized")
        self.tools = self.list_tools()
        return self

    def stop(self) -> None:
        proc = self.proc
        self.proc = None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        except OSError:
            pass

    # -- transport ----------------------------------------------------------

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._send(payload)

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        deadline = time.monotonic() + (timeout or self.config.timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPError(f"{self.config.name}: timed out waiting for {method}")
            try:
                message = self._inbox.get(timeout=remaining)
            except queue.Empty:
                raise MCPError(f"{self.config.name}: timed out waiting for {method}") from None
            if message.get("__eof__"):
                self._inbox.put(message)  # keep the signal for later calls
                raise MCPError(f"{self.config.name}: server exited{self._stderr_tail()}")
            if message.get("id") != request_id:
                continue  # notifications / unrelated responses
            if "error" in message:
                error = message.get("error") or {}
                detail = error.get("message") if isinstance(error, dict) else error
                raise MCPError(f"{self.config.name}: {detail or 'unknown error'}")
            result = message.get("result")
            return result if isinstance(result, dict) else {}

    def _send(self, payload: dict[str, Any]) -> None:
        proc = self.proc
        if proc is None or proc.stdin is None:
            raise MCPError(f"{self.config.name}: server is not running")
        try:
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise MCPError(f"{self.config.name}: lost connection ({exc})") from exc

    def _read_stdout(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            text = line.strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict):
                self._inbox.put(message)
        self._inbox.put({"__eof__": True})

    def _read_stderr(self) -> None:
        proc = self.proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            line = line.rstrip()
            if line:
                self._stderr.append(line)

    def _stderr_tail(self) -> str:
        if not self._stderr:
            return ""
        return "\n" + "\n".join(list(self._stderr)[-8:])

    # -- MCP operations -----------------------------------------------------

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            result = self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(tool for tool in (result.get("tools") or []) if isinstance(tool, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        return format_tool_result(result), bool(result.get("isError"))


def connect_servers(
    configs: dict[str, MCPServerConfig],
    registry: Registry,
    log: Callable[[str], None] = print,
    timeout: float = 30.0,
) -> tuple[list[MCPClient], list[str]]:
    """Start every server and register its tools. Broken servers are reported."""
    clients: list[MCPClient] = []
    errors: list[str] = []
    for name, config in configs.items():
        client = MCPClient(config, log=log)
        try:
            client.start(timeout=timeout)
        except MCPError as exc:
            errors.append(str(exc))
            client.stop()
            continue
        registered = 0
        for tool in client.tools:
            tool_name = _register_tool(registry, client, tool)
            if tool_name:
                registered += 1
        clients.append(client)
        log(f"mcp server '{name}' ready · {registered} tool(s)")
    return clients, errors


def stop_clients(clients: list[MCPClient]) -> None:
    for client in clients:
        client.stop()


def format_tool_result(result: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in result.get("content") or []:
        if isinstance(item, str):
            parts.append(item)
            continue
        if not isinstance(item, dict):
            parts.append(str(item))
            continue
        kind = item.get("type")
        if kind == "text":
            parts.append(str(item.get("text") or ""))
        elif kind == "image":
            parts.append("[image content]")
        elif kind == "resource":
            resource = item.get("resource") or {}
            text = resource.get("text") if isinstance(resource, dict) else None
            parts.append(str(text) if text else json.dumps(resource, default=str)[:2000])
        elif kind == "resource_link":
            parts.append(f"[resource {item.get('uri')}]")
        else:
            parts.append(json.dumps(item, ensure_ascii=False, default=str)[:2000])
    if not parts and result.get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"], ensure_ascii=False, default=str))
    return "\n".join(part for part in parts if part).strip() or "(no content)"


def _register_tool(registry: Registry, client: MCPClient, tool: dict[str, Any]) -> str | None:
    raw_name = str(tool.get("name") or "").strip()
    if not raw_name:
        return None
    full_name = f"mcp__{_sanitize(client.config.name)}__{_sanitize(raw_name)}"
    if full_name in registry.tools:
        full_name = f"{full_name}_{len(client.tools)}"
    schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {}
    if not schema:
        schema = {"type": "object", "properties": {}}
    description = str(tool.get("description") or raw_name)
    prefix = f"[{client.config.name}] "
    annotations = tool.get("annotations") if isinstance(tool.get("annotations"), dict) else {}
    read_only = bool(annotations.get("readOnlyHint"))

    def call(**arguments: Any) -> Any:
        text, is_error = client.call_tool(raw_name, arguments)
        return f"Error: {text}" if is_error else text

    registry.add(full_name, prefix + description, schema, call, read_only=read_only)
    return full_name


def _sanitize(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name) or "server"


def _resolve_command(command: str) -> str | list[str]:
    """Resolve a command, handling Windows `.cmd`/`.bat` shims (npx, uvx, ...)."""
    if os.name != "nt":
        return command
    found = shutil.which(command)
    if found:
        if found.lower().endswith((".cmd", ".bat")):
            return ["cmd.exe", "/c", found]
        return found
    for suffix in (".cmd", ".bat", ".exe"):
        found = shutil.which(command + suffix)
        if found:
            if found.lower().endswith((".cmd", ".bat")):
                return ["cmd.exe", "/c", found]
            return found
    return command
