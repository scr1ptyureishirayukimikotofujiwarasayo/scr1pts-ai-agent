"""Offline smoke test — no API key, no network.

Runs the whole harness against a tiny fake OpenAI-compatible server:
tool calling, streaming, skills, plugins, sandboxed filesystem tools.

    python tests/smoke_test.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
import tempfile
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.agent import Agent  # noqa: E402
from agent.config import ConfigError, load_settings  # noqa: E402
from agent.essentials import register_essentials  # noqa: E402
from agent.llm import Client, LLMError  # noqa: E402
from agent.models import ModelListError, fetch_models  # noqa: E402
from agent.plugins import Hooks, load_plugins  # noqa: E402
from agent.skills import SkillLoader, parse_frontmatter  # noqa: E402
from agent.tools import Registry  # noqa: E402

STATE = {"calls": 0}

TEST_CATALOG = {
    "smoke": {
        "id": "smoke",
        "name": "Smoke Provider",
        "models": {
            "fake-model": {
                "id": "fake-model",
                "name": "Fake One",
                "limit": {"context": 128000, "output": 8192},
                "cost": {"input": 3, "output": 15},
                "tool_call": True,
                "reasoning": True,
            },
            "catalog-only": {
                "id": "catalog-only",
                "name": "Catalog Only",
                "limit": {"context": 32000, "output": 4096},
                "cost": {"input": 0.5, "output": 1.5},
                "tool_call": False,
            },
        },
    },
    "openrouter": {
        "id": "openrouter",
        "name": "OpenRouter",
        "models": {
            "anthropic/claude-sonnet-4.5": {
                "id": "anthropic/claude-sonnet-4.5",
                "name": "Claude Sonnet 4.5",
                "limit": {"context": 200000, "output": 64000},
                "cost": {"input": 3, "output": 15, "cache_read": 0.3},
                "tool_call": True,
                "reasoning": True,
                "attachment": True,
                "release_date": "2025-09-29",
            }
        },
    },
    "togetherai": {
        "id": "togetherai",
        "models": {"meta-llama/Llama-3.3-70B": {"limit": {"context": 131072}}},
    },
}

TOOL_CALL_STREAM = [
    {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "function": {"name": "write_file", "arguments": ""},
                        }
                    ]
                }
            }
        ]
    },
    {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "function": {"arguments": '{"path": "hello.txt", "content": "hi"}'},
                        }
                    ]
                }
            }
        ]
    },
    {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def do_GET(self):  # noqa: N802
        if self.path == "/api.json":
            self._send(json.dumps(TEST_CATALOG).encode(), "application/json")
            return
        if self.path.rstrip("/") != "/v1/models":
            self.send_error(404)
            return
        if (self.headers.get("authorization") or "") != "Bearer test-key":
            body = json.dumps({"error": {"message": "bad key"}}).encode()
            self.send_response(401)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        payload = {
            "object": "list",
            "data": [{"id": name} for name in ("fake-model", "fake-model-2", "other-model")],
        }
        self._send(json.dumps(payload).encode(), "application/json")

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        STATE["last_body"] = body
        STATE["calls"] += 1
        has_tool_result = any(m.get("role") == "tool" for m in body["messages"])
        auth = self.headers.get("authorization") or ""

        if not body.get("stream"):
            payload = {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": f"ok (auth={'set' if auth else 'none'}) "
                            f"tools={len(body.get('tools') or [])}",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            }
            self._send(json.dumps(payload).encode(), "application/json")
            return

        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        chunks = [] if has_tool_result else TOOL_CALL_STREAM
        if has_tool_result:
            chunks = [
                {"choices": [{"delta": {"content": "done"}}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            ]
        chunks = list(chunks) + [
            {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 4}}
        ]
        for chunk in chunks:
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _send(self, data: bytes, content_type: str):
        self.send_response(200)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        raise SystemExit(1)


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    os.environ["AGENT_OFFLINE"] = "1"  # no background models.dev fetch in tests
    workdir = Path(tempfile.mkdtemp(prefix="agent-smoke-"))
    (workdir / "demo.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (workdir / "AGENTS.md").write_text(
        "# Project rules\n\nAlways run the tests before committing.\n", encoding="utf-8"
    )
    (workdir / "skills" / "writer").mkdir(parents=True)
    (workdir / "skills" / "writer" / "SKILL.md").write_text(
        "---\nname: writer\ndescription: Write things carefully.\n---\n\nStep 1: think.\n",
        encoding="utf-8",
    )

    os.environ["SMOKE_BASE_URL"] = f"http://127.0.0.1:{port}/v1"
    os.environ["SMOKE_API_KEY"] = "test-key"

    (workdir / "empty-mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")
    config = workdir / "config.json"
    config.write_text(
        json.dumps(
            {
                "provider": "smoke",
                "stream": True,
                "confirm_shell": False,
                "auto_save": False,
                "workspace": str(workdir),
                "skills_dir": str(workdir / "skills"),
                "plugins_dir": str(ROOT / "plugins"),
                "mcp_config": str(workdir / "empty-mcp.json"),
                "providers": {
                    "smoke": {
                        "base_url": "http://127.0.0.1:1/v1",
                        "base_url_env": "SMOKE_BASE_URL",
                        "api_key_env": ["SMOKE_API_KEY"],
                        "default_model": "fake-model",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_path=config, workspace=workdir)
    check("provider endpoint", settings.provider.endpoint.endswith("/chat/completions"))
    check("env base_url override", str(port) in settings.provider.base_url)

    registry = Registry()
    skills = SkillLoader(settings.skills_dir)
    register_essentials(registry, settings, skill_reader=skills.read)
    hooks = Hooks()
    plugins, plugin_errors = load_plugins(settings.plugins_dir, registry, hooks, settings, log=lambda _: None)
    check("plugins load", not plugin_errors and bool(plugins), str(plugin_errors))
    check("plugin tool registered", "now" in registry.tools)

    # -- tools --------------------------------------------------------------
    result, error = registry.call("read_file", {"path": "demo.py"})
    check("read_file", not error and "return a + b" in result, result)
    result, error = registry.call("write_file", {"path": "out.txt", "content": "hello"})
    check("write_file", not error and (workdir / "out.txt").read_text() == "hello", result)
    result, error = registry.call("edit_file", {"path": "out.txt", "old_text": "hello", "new_text": "bye"})
    check("edit_file", not error and (workdir / "out.txt").read_text() == "bye", result)
    result, error = registry.call("grep", {"pattern": r"def \w+", "path": "."})
    check("grep", not error and "demo.py" in result, result)
    result, error = registry.call("search_files", {"pattern": "*.py"})
    check("search_files", not error and "demo.py" in result, result)
    result, error = registry.call("run_command", {"command": "echo hi"})
    check("run_command", not error and "hi" in result, result)
    result, error = registry.call("use_skill", {"name": "writer"})
    check("use_skill", not error and "Step 1" in result, result)
    result, error = registry.call("nope", {})
    check("unknown tool is an error", error)

    # -- frontmatter --------------------------------------------------------
    meta, body = parse_frontmatter("---\nname: x\ndescription: y\n---\nbody")
    check("frontmatter", meta == {"name": "x", "description": "y"} and body == "body")

    # -- streaming + tool loop ---------------------------------------------
    client = Client(settings)
    agent = Agent(settings, client, registry, skills, hooks)
    streamed: list[str] = []
    tools_used: list[str] = []
    final = agent.run_turn(
        "write hello.txt",
        on_delta=streamed.append,
        on_tool=lambda name, args: tools_used.append(name),
    )
    check("tool was executed", tools_used == ["write_file"], str(tools_used))
    check("tool wrote the file", (workdir / "hello.txt").read_text() == "hi")
    check("final answer streamed", final == "done" and "".join(streamed) == "done", f"{final!r}")
    check("request count", STATE["calls"] == 2, str(STATE["calls"]))
    check(
        "tool result fed back",
        any(m.get("role") == "tool" and "hello.txt" in m.get("content", "") for m in agent.messages),
    )
    check("token usage tracked", agent.total_usage["prompt_tokens"] >= 0)
    check(
        "streaming asks for usage",
        (STATE.get("last_body") or {}).get("stream_options") == {"include_usage": True},
        str((STATE.get("last_body") or {}).get("stream_options")),
    )
    check(
        "last request tokens recorded",
        agent.last_prompt_tokens == 11 and agent.last_completion_tokens == 4,
        f"{agent.last_prompt_tokens}/{agent.last_completion_tokens}",
    )
    check(
        "usage tracked per model",
        agent.usage_by_model.get("smoke", {}).get(settings.model, {}).get("requests", 0) >= 1,
        str(agent.usage_by_model),
    )

    # -- non-streaming ------------------------------------------------------
    settings.stream = False
    plain = agent.run_turn("hello")
    check("non-streaming response", "tools=" in plain and "auth=set" in plain, plain)

    # -- confirmation gate --------------------------------------------------
    settings.stream = True
    agent.confirm = lambda name, arguments: False
    agent.reset()  # the fake server only emits a tool call when no tool message exists
    agent.run_turn("write denied.txt", on_delta=lambda _: None)
    check("denied tool did not run", not (workdir / "denied.txt").exists())
    check(
        "denial is reported to the model",
        any(m.get("role") == "tool" and "Denied" in m.get("content", "") for m in agent.messages),
    )
    agent.confirm = None

    # -- sessions -----------------------------------------------------------
    settings.sessions_dir = workdir / "sessions"
    from agent.cli import Application, COMMAND_INDEX  # local import keeps the module graph tidy
    from agent.sessions import SessionStore, slug

    store = SessionStore(workdir / "sessions")
    check("sessions folder created", (workdir / "sessions").is_dir())
    check("slug", slug("My Chat 2") == "My-Chat-2")
    store.save("unit test", {"messages": [{"role": "user", "content": "hi"}], "provider": "smoke"})
    check("session file written", (workdir / "sessions" / "unit-test.json").is_file())
    check("session listing", [i.name for i in store.list()] == ["unit-test"])
    check("session preview", store.list()[0].preview == "hi")

    app = Application(settings)
    app.agent.messages = [{"role": "user", "content": "first chat about widgets"}]
    app._save_session("alpha")
    time.sleep(0.02)
    app.agent.messages = [{"role": "user", "content": "second chat about gadgets"}]
    app._save_session("beta")
    listing = app._refresh_listing()
    check("newest session first", [i.name for i in listing][:2] == ["beta", "alpha"], str([i.name for i in listing]))

    app.cmd_switch("2")
    check(
        "switch by number",
        app.session_name == "alpha" and app.agent.messages[0]["content"].startswith("first"),
        f"{app.session_name}: {app.agent.messages[:1]}",
    )
    app.cmd_switch("beta")
    check(
        "switch by name",
        app.session_name == "beta" and app.agent.messages[0]["content"].startswith("second"),
    )

    app.cmd_new("gamma")
    check("new session", app.session_name == "gamma" and app.agent.messages == [])
    app.agent.messages = [{"role": "user", "content": "gamma chat"}]
    app.cmd_save("")
    check("save current name", app.session_name == "gamma" and store.exists("gamma"))
    app.cmd_delete("gamma")
    check("delete session", not store.exists("gamma") and app.session_name == "")

    # -- settings -----------------------------------------------------------
    from agent.settings_spec import SETTINGS_BY_KEY as SPEC
    from agent.settings_spec import coerce as coerce_setting

    check("bool coercion", coerce_setting(SPEC["stream"], "off") is False)
    check("int coercion", coerce_setting(SPEC["max_steps"], "12") == 12)
    resolved = coerce_setting(SPEC["workspace"], ".")
    check("path coercion", isinstance(resolved, Path) and resolved.is_absolute())
    try:
        coerce_setting(SPEC["temperature"], "9")
        check("range validation", False)
    except ValueError:
        check("range validation", True)

    def quiet(func, *args):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            func(*args)
        return buffer.getvalue()

    output = quiet(app.cmd_settings, "")
    check("settings listing", "temperature" in output and "show_tool_results" in output)

    quiet(app.cmd_settings, "temperature 0.25")
    check("set temperature", app.settings.temperature == 0.25)
    quiet(app.cmd_settings, "stream off")
    check("set bool", app.settings.stream is False)
    quiet(app.cmd_settings, "max_steps 7")
    check("set int", app.settings.max_steps == 7)
    quiet(app.cmd_settings, "temperature 99")
    check("invalid value rejected", app.settings.temperature == 0.25)
    output = quiet(app.cmd_settings, "bogus 1")
    check("unknown setting rejected", "unknown setting" in output)

    quiet(app.cmd_settings, "save")
    written = json.loads(config.read_text(encoding="utf-8"))
    check("settings saved", written["temperature"] == 0.25 and written["max_steps"] == 7)
    check("shell persisted", written.get("shell") == "auto")
    check("show_usage persisted", written.get("show_usage") is True)
    check("settings keep providers", "smoke" in (written.get("providers") or {}))
    check(
        "settings save mcp/env keys",
        written.get("mcp_config") == str((workdir / "empty-mcp.json").resolve())
        and written.get("env_file") == ".env",
        str({k: written.get(k) for k in ("mcp_config", "env_file")}),
    )
    check("config path recorded", app.settings.config_path == config.resolve())

    quiet(app.cmd_settings, "temperature 1.5")
    quiet(app.cmd_settings, "reset temperature")
    check("reset from config.json", app.settings.temperature == 0.25)

    extra_skill = workdir / "skills-b" / "extra-skill"
    extra_skill.mkdir(parents=True)
    (extra_skill / "SKILL.md").write_text(
        "---\nname: extra-skill\ndescription: extra\n---\nbody\n", encoding="utf-8"
    )
    quiet(app.cmd_settings, f"skills_dir {workdir / 'skills-b'}")
    check("skills dir reload", "extra-skill" in app.skills.skills)
    check("use_skill still registered", "use_skill" in app.registry.tools)

    # -- AGENTS.md ----------------------------------------------------------
    check(
        "AGENTS.md discovered",
        [item.path.name for item in app.agent.instructions] == ["AGENTS.md"],
        str([str(i.path) for i in app.agent.instructions]),
    )
    check("AGENTS.md in system prompt", "Always run the tests" in app.agent.system_prompt)
    agents_listing = quiet(app.cmd_agents, "")
    check("agents listing", "AGENTS.md" in agents_listing)

    nested = workdir / "nested"
    nested.mkdir()
    (nested / "AGENTS.md").write_text("Nested rule: use tabs.", encoding="utf-8")
    result, error = app.registry.call("read_agents_md", {"path": "nested"})
    check("read_agents_md tool", not error and "Nested rule" in result, result)

    repo = workdir / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("Repo rule: deploy with make release.", encoding="utf-8")
    quiet(app.cmd_settings, f"workspace {repo}")
    check("workspace reloads AGENTS.md", "Repo rule" in app.agent.system_prompt)
    quiet(app.cmd_settings, f"workspace {workdir}")
    quiet(app.cmd_agents, "reload")
    check("agents reload", "Always run the tests" in app.agent.system_prompt)

    quiet(app.cmd_settings, "system_prompt Be terse.")
    check("system prompt setting", app.agent.system_prompt.startswith("Be terse."))
    quiet(app.cmd_settings, "model other-model")
    check("model via settings", app.settings.model == "other-model")
    quiet(app.cmd_settings, "provider smoke")
    check("provider via settings", app.settings.provider.name == "smoke")

    # -- API keys -----------------------------------------------------------
    from agent.secrets import mask

    env_file = workdir / ".env"
    app.settings.env_file = env_file
    check("keys listing", "openrouter" in quiet(app.cmd_keys, ""))

    quiet(app.cmd_keys, "set openrouter sk-or-test-abcdef123456")
    check(
        "key written to .env",
        "OPENROUTER_API_KEY=sk-or-test-abcdef123456" in env_file.read_text(encoding="utf-8"),
    )
    check("key exported to environment", os.environ.get("OPENROUTER_API_KEY") == "sk-or-test-abcdef123456")
    listing = quiet(app.cmd_keys, "")
    check("key masked in listing", "sk-or-test-abcdef123456" not in listing and "sk-or-…3456" in listing)
    check("mask helper", mask("short") == "…rt" and mask("") == "(unset)")

    quiet(app.cmd_keys, "clear openrouter")
    check("key cleared from environment", os.environ.get("OPENROUTER_API_KEY") is None)
    check("key cleared from .env", "OPENROUTER_API_KEY" not in env_file.read_text(encoding="utf-8"))

    quiet(app.cmd_keys, "set deepseek ds-test-000000000000")
    quiet(app.cmd_keys, "clear all")
    check("clear all", os.environ.get("DEEPSEEK_API_KEY") is None)

    quiet(app.cmd_keys, "set smoke test-key")
    check("key restored for smoke provider", os.environ.get("SMOKE_API_KEY") == "test-key")
    check_keys_out = quiet(app.cmd_keys, "check smoke")
    check(
        "keys check validates by listing models",
        "✓ smoke" in check_keys_out and "3 models" in check_keys_out,
        check_keys_out,
    )

    # -- MCP ----------------------------------------------------------------
    from agent.mcp import load_mcp_config

    mcp_file = workdir / "mcp.json"
    mcp_file.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fake": {
                        "command": sys.executable,
                        "args": [str(ROOT / "tests" / "fake_mcp_server.py")],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    quiet(app.cmd_settings, f"mcp_config {mcp_file}")
    check("mcp server connected", len(app.mcp_clients) == 1, str(app.mcp_errors))
    check("mcp tools registered", any(n.startswith("mcp__fake__") for n in app.registry.names()))
    result, error = app.registry.call("mcp__fake__echo", {"text": "hi"})
    check("mcp tool call", not error and "echo: hi" in result, result)
    result, error = app.registry.call("mcp__fake__add", {"a": 2, "b": 3})
    check("mcp numeric tool", not error and "5" in result, result)
    listing = quiet(app.cmd_mcp, "")
    check("mcp listing", "fake" in listing and "running" in listing, listing)
    tools_out = quiet(app.cmd_mcp, "tools")
    check("mcp tools listing", "mcp__fake__echo" in tools_out, tools_out)

    expanded = workdir / "mcp-env.json"
    expanded.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "expanded": {
                        "command": "run",
                        "args": ["--flag", "${SMOKE_ARG}"],
                        "env": {"TOKEN": "${SMOKE_TOKEN}"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    os.environ["SMOKE_TOKEN"] = "tok-123"
    os.environ["SMOKE_ARG"] = "arg-456"
    expanded_servers, expanded_errors = load_mcp_config(expanded)
    check(
        "mcp expands ${ENV} in env and args",
        not expanded_errors
        and expanded_servers["expanded"].env["TOKEN"] == "tok-123"
        and expanded_servers["expanded"].args[-1] == "arg-456",
        str(expanded_errors),
    )
    del os.environ["SMOKE_TOKEN"]
    del os.environ["SMOKE_ARG"]

    missing_env = workdir / "mcp-missing-env.json"
    missing_env.write_text(
        json.dumps(
            {"mcpServers": {"needs": {"command": "run", "env": {"TOKEN": "${NOT_SET_XYZ}"}}}}
        ),
        encoding="utf-8",
    )
    missing_servers, missing_errors = load_mcp_config(missing_env)
    check(
        "mcp skips servers with unset env vars",
        missing_servers == {} and "NOT_SET_XYZ" in missing_errors[0],
        str(missing_errors),
    )

    shipped = json.loads((ROOT / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    check(
        "github mcp is configured",
        shipped.get("github", {}).get("command") == "npx"
        and shipped["github"]["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "${GITHUB_TOKEN}"
        and not shipped["github"].get("disabled"),
        str(list(shipped)),
    )

    bad = workdir / "bad-mcp.json"
    bad.write_text(
        json.dumps({"mcpServers": {"web": {"url": "https://example.com/mcp"}}}), encoding="utf-8"
    )
    servers, errors = load_mcp_config(bad)
    check("mcp rejects url servers", not servers and "HTTP/SSE" in errors[0], str(errors))

    check(
        "mcp readOnlyHint honoured",
        bool(app.registry.get("mcp__fake__echo").read_only)
        and not app.registry.get("mcp__fake__add").read_only,
    )

    app._stop_mcp()
    check("mcp stopped", app.mcp_clients == [])

    # -- modes: plan (read-only) / build ------------------------------------
    from agent.settings_spec import SETTINGS_BY_KEY as SPEC_MODE

    check("mode coercion", coerce_setting(SPEC_MODE["mode"], "PLAN") == "plan")
    try:
        coerce_setting(SPEC_MODE["mode"], "yolo")
        check("mode validation", False)
    except ValueError:
        check("mode validation", True)

    app.settings.stream = False
    quiet(app.cmd_mode, "build")
    build_reply = app.agent.run_turn("count the tools")
    build_tools = int(re.search(r"tools=(\d+)", build_reply).group(1))
    check(
        "build mode prompt has python-first guidance",
        "Python first" in app.agent.system_prompt and "run_python" in app.agent.system_prompt,
    )
    tool_names = [spec["function"]["name"] for spec in app.registry.specs()]
    check(
        "run_python is offered before run_command",
        tool_names.index("run_python") < tool_names.index("run_command"),
        str(tool_names),
    )
    check(
        "prompt forbids python via the shell tool",
        "Never call `run_command` with `python -c" in app.agent.system_prompt,
    )
    saved_system_prompt = app.settings.system_prompt
    app.settings.system_prompt = ""
    default_prompt = " ".join(app.agent.build_system_prompt().split())
    app.settings.system_prompt = saved_system_prompt
    app.agent.system_prompt = app.agent.build_system_prompt()
    check(
        "python is the stated default execution path",
        "default execution path" in default_prompt,
    )
    check("build mode prompt", "Mode: BUILD" in app.agent.system_prompt and "plan>" not in app._prompt())

    quiet(app.cmd_mode, "plan")
    plan_reply = app.agent.run_turn("count the tools")
    plan_tools = int(re.search(r"tools=(\d+)", plan_reply).group(1))
    check("plan mode exposes fewer tools", plan_tools < build_tools, f"{plan_tools} vs {build_tools}")
    check("plan mode prompt", "plan>" in app._prompt() and "Mode: PLAN" in app.agent.system_prompt)
    check(
        "plan mode blocks mutating tools",
        app.agent._mode_block("run_python", {}) != ""
        and app.agent._mode_block("write_file", {}) != ""
        and app.agent._mode_block("read_file", {}) == ""
        and app.agent._mode_block("mcp__fake__add", {}) != ""
        and app.agent._mode_block("mcp__fake__echo", {}) == "",
    )
    check(
        "plan mode restricts http to GET/HEAD",
        "GET/HEAD" in app.agent._mode_block("http_request", {"method": "POST"})
        and app.agent._mode_block("http_request", {"method": "GET"}) == "",
    )

    # a model that ignores the filtered schemas still cannot mutate anything
    hello = workdir / "hello.txt"
    if hello.exists():
        hello.unlink()
    app.settings.stream = True
    app.agent.reset()
    final = app.agent.run_turn("write hello.txt", on_delta=lambda _: None)
    check("plan mode refuses to write", not hello.exists())
    check(
        "plan refusal reaches the model",
        any(
            message.get("role") == "tool" and "Blocked by plan mode" in message.get("content", "")
            for message in app.agent.messages
        ),
    )

    quiet(app.cmd_mode, "build")
    app.agent.reset()
    app.agent.run_turn("write hello.txt", on_delta=lambda _: None)
    check("build mode writes again", hello.exists() and hello.read_text() == "hi")

    quiet(app.cmd_settings, "mode plan")
    check("settings can switch mode", app.settings.mode == "plan")
    quiet(app.cmd_settings, "mode nope")
    check("invalid mode rejected by settings", app.settings.mode == "plan")
    quiet(app.cmd_settings, "mode build")
    quiet(app.cmd_settings, "save")
    check("mode persisted", json.loads(config.read_text(encoding="utf-8")).get("mode") == "build")

    # -- python-first execution --------------------------------------------
    result, error = app.registry.call(
        "run_python", {"code": "import sys; print(6 * 7); print(sys.argv[1:])", "args": ["a", "b"]}
    )
    check("run_python output", not error and "42" in result and "['a', 'b']" in result, result)

    result, error = app.registry.call("run_python", {"code": "raise SystemExit(3)"})
    check("run_python exit code", "[exit 3]" in result, result)

    result, error = app.registry.call("run_python", {"code": "import os; print(os.getcwd())"})
    check("run_python uses workspace", not error and "agent-smoke" in result, result)

    result, error = app.registry.call("run_python", {"code": "import time; time.sleep(5)", "timeout": 1})
    check("run_python timeout", "timed out" in result.lower(), result)

    # -- shell selection (bash support) ------------------------------------
    from agent.shells import ShellError, detect as detect_shell, resolve as resolve_shell

    detected = detect_shell()
    check("shell detected", detected.kind in ("bash", "sh", "cmd"), str(detected))
    try:
        resolve_shell("nonsense")
        check("unknown shell rejected", False)
    except ShellError:
        check("unknown shell rejected", True)

    if detected.kind == "bash":
        bash_argv = resolve_shell("bash").argv("echo hi")
        check(
            "bash argv",
            bool(bash_argv)
            and bash_argv[0].lower().endswith(("bash", "bash.exe"))
            and any(flag in ("-c", "-lc") for flag in bash_argv),
            str(bash_argv),
        )
        bash_result, bash_error = app.registry.call(
            "run_command",
            {"command": "[[ 1 -eq 1 ]] && echo bash-works", "shell": "bash"},
        )
        check("explicit bash works", not bash_error and "bash-works" in bash_result, bash_result)
        auto_result, auto_error = app.registry.call(
            "run_command", {"command": "echo shell-$BASH_VERSION"}
        )
        check(
            "auto prefers bash",
            not auto_error and "· bash" in auto_result and "shell-" in auto_result,
            auto_result,
        )
    else:
        print("[SKIP] bash-specific checks (no bash installed)")

    if os.name != "nt":
        cmd_result, _ = app.registry.call(
            "run_command", {"command": "echo hi", "shell": "cmd"}
        )
        check("cmd rejected on posix", "Windows-only" in cmd_result, cmd_result)
        sh_result, sh_error = app.registry.call(
            "run_command", {"command": "echo hi", "shell": "sh"}
        )
        check("sh works on posix", not sh_error and "hi" in sh_result and "· sh" in sh_result, sh_result)

    quiet(app.cmd_settings, "shell bash")
    check("shell setting applies", app.settings.shell == "bash")
    quiet(app.cmd_settings, "shell bogus")
    check("invalid shell rejected by settings", app.settings.shell == "bash")
    quiet(app.cmd_settings, "shell auto")

    # -- provider catalogue -------------------------------------------------
    from agent.config import DEFAULT_PROVIDERS, build_provider

    check("provider count", len(DEFAULT_PROVIDERS) >= 25, str(len(DEFAULT_PROVIDERS)))
    for provider_name in (
        "openrouter", "opencode-go", "opencode", "openai", "anthropic", "google",
        "venice", "groq", "deepseek", "mistral", "together", "fireworks", "xai",
        "cerebras", "perplexity", "moonshot", "zhipu", "dashscope", "novita",
        "nebius", "hyperbolic", "github", "ollama", "lmstudio", "custom",
    ):
        check(f"provider {provider_name}", provider_name in DEFAULT_PROVIDERS)

    os.environ["VENICE_API_KEY"] = "venice-test-key"
    try:
        venice = build_provider("venice", DEFAULT_PROVIDERS)
        check(
            "venice wiring",
            venice.endpoint == "https://api.venice.ai/api/v1/chat/completions"
            and venice.api_key == "venice-test-key"
            and venice.model == "venice-uncensored",
            f"{venice.endpoint} {venice.model}",
        )
        check("venice listed", "venice" in quiet(app.cmd_providers, ""))
    finally:
        del os.environ["VENICE_API_KEY"]

    # -- compaction ---------------------------------------------------------
    from agent.compact import SUMMARY_HEADING

    def history() -> list[dict]:
        return [
            {"role": "user", "content": "first task: build a widget"},
            {"role": "assistant", "content": "started"},
            {"role": "tool", "tool_call_id": "c1", "name": "read_file", "content": "x"},
            {"role": "user", "content": "second task: fix the bug"},
            {"role": "assistant", "content": "fixed"},
            {"role": "user", "content": "third task: add tests"},
            {"role": "assistant", "content": "tests added"},
            {"role": "user", "content": "fourth task: review the diff"},
            {"role": "assistant", "content": "reviewed"},
        ]

    app.agent.reset()
    app.agent.messages = history()
    app.agent.system_prompt = app.agent.build_system_prompt()
    result = app.agent.compact(keep_turns=2)
    check("compact reduces history", (result.before, result.after) == (9, 4), f"{result.before}->{result.after}")
    check("compact uses the model", result.method == "model" and result.summary_chars > 0, result.method)
    check("compact keeps whole turns", app.agent.messages[0]["role"] == "user")
    check(
        "compact keeps recent turns",
        "third task" in app.agent.messages[0]["content"]
        and "review the diff" in str(app.agent.messages),
    )
    check("compact drops folded turns from history", "first task" not in str(app.agent.messages))
    check("compact injects summary into prompt", SUMMARY_HEADING in app.agent.system_prompt)

    app.agent.reset()
    app.agent.messages = history()
    app.agent.system_prompt = app.agent.build_system_prompt()
    original_chat = app.agent.client.chat
    app.agent.client.chat = lambda *a, **k: (_ for _ in ()).throw(LLMError("offline"))
    try:
        fallback = app.agent.compact(keep_turns=1)
    finally:
        app.agent.client.chat = original_chat
    check(
        "compact falls back to a digest",
        fallback.method == "digest"
        and "first task" in app.agent.summary
        and fallback.error,
        app.agent.summary,
    )

    app.agent.reset()
    check("compact no-op without history", "nothing to compact" in quiet(app.cmd_compact, ""))
    app.agent.summary = "important compacted facts"
    app.agent.system_prompt = app.agent.build_system_prompt()
    check("compact show", "important compacted facts" in quiet(app.cmd_compact, "show"))

    app.agent.messages = [{"role": "user", "content": "still here"}]
    quiet(app.cmd_save, "with-summary")
    quiet(app.cmd_new, "")
    quiet(app.cmd_switch, "with-summary")
    check(
        "session restores summary",
        app.agent.summary == "important compacted facts"
        and SUMMARY_HEADING in app.agent.system_prompt,
        app.agent.summary,
    )
    quiet(app.cmd_settings, "compact_keep_turns 3")
    check("compact keep-turns setting", app.settings.compact_keep_turns == 3)

    # -- models.dev catalogue ----------------------------------------------
    from agent import modelsdev
    from agent.modelsdev import describe as describe_meta
    from agent.modelsdev import info as meta_info
    from agent.modelsdev import load_catalog, lookup, models_for

    entries = models_for(TEST_CATALOG, "openrouter")
    check("models.dev provider lookup", "anthropic/claude-sonnet-4.5" in entries)
    check("models.dev alias lookup", bool(models_for(TEST_CATALOG, "together")))
    missing_provider = models_for(TEST_CATALOG, "does-not-exist")
    check("models.dev unknown provider", missing_provider == {})

    sonnet = entries["anthropic/claude-sonnet-4.5"]
    description = describe_meta(sonnet)
    check(
        "models.dev describe",
        "200k ctx" in description and "$3/$15 1M" in description and "tools" in description,
        description,
    )
    check(
        "models.dev number formatting",
        "128k ctx" in describe_meta({"limit": {"context": 128000}})
        and "1M ctx" in describe_meta({"limit": {"context": 1000000}}),
    )
    check(
        "models.dev flags",
        "no tools" in describe_meta({"tool_call": False})
        and "reasoning" in describe_meta({"reasoning": True}),
    )
    rows = dict(meta_info(sonnet))
    check(
        "models.dev info rows",
        rows.get("context") == "200k" and rows.get("tool calls") == "yes" and rows.get("max output") == "64k",
        str(rows),
    )
    check("models.dev lookup case-insensitive", lookup(entries, "ANTHROPIC/CLAUDE-SONNET-4.5") is sonnet)

    cache_file = workdir / "models-cache.json"
    catalog_url = f"http://127.0.0.1:{port}/api.json"
    catalog, note = load_catalog(url=catalog_url, cache_file=cache_file, force=True, max_age=3600)
    check("models.dev fetch", note == "" and "smoke" in catalog, note)
    check("models.dev cache written", cache_file.is_file())

    cached, note = load_catalog(
        url="http://127.0.0.1:1/api.json", cache_file=cache_file, max_age=3600
    )
    check("models.dev cache is used", cached == catalog and note == "", note)

    stale, note = load_catalog(
        url="http://127.0.0.1:1/api.json", cache_file=cache_file, max_age=0, timeout=1
    )
    check("models.dev offline fallback", stale == catalog and "cached" in note, note)

    # -- cleanup before sharing --------------------------------------------
    from agent.cleanup import CLEAN_CONFIG
    from agent.cleanup import clean as run_clean
    from agent.cleanup import main as cleanup_main
    from agent.cleanup import preview as cleanup_preview

    share = workdir / "share"
    (share / "sessions").mkdir(parents=True)
    (share / ".cache").mkdir()
    (share / "plugins").mkdir()
    (share / "agent" / "__pycache__").mkdir(parents=True)
    (share / ".env.example").write_text(
        "# keys\nOPENROUTER_API_KEY=\n# OPENCODE_BASE_URL=x\nGITHUB_TOKEN=\n",
        encoding="utf-8",
    )
    (share / ".env").write_text("OPENROUTER_API_KEY=secret-123\nSOME_OTHER=1\n", encoding="utf-8")
    (share / ".env.bak").write_text("OPENROUTER_API_KEY=old-secret\n", encoding="utf-8")
    (share / "config.json").write_text(
        json.dumps(
            {
                "provider": "openai",
                "workspace": "C:/private/project",
                "providers": {"x": {"api_key": "sk-private"}},
            }
        ),
        encoding="utf-8",
    )
    (share / "mcp.json").write_text(
        json.dumps({"mcpServers": {"fs": {"command": "npx", "env": {"TOKEN": "secret"}}}}),
        encoding="utf-8",
    )
    (share / "sessions" / "chat.json").write_text("{}", encoding="utf-8")
    (share / ".cache" / "m.json").write_text("{}", encoding="utf-8")
    (share / "agent" / "__pycache__" / "x.pyc").write_text("")
    (share / "plugins" / "mine.py").write_text("# keep me", encoding="utf-8")
    (share / "run.log").write_text("log", encoding="utf-8")

    planned = cleanup_preview(share)
    check(
        "cleanup preview",
        any(".env" in line for line in planned)
        and any("sessions" in line for line in planned)
        and any("config.json" in line for line in planned),
        str(planned),
    )

    dry = run_clean(share, dry_run=True)
    check(
        "cleanup dry run changes nothing",
        (share / ".env").read_text(encoding="utf-8") == "OPENROUTER_API_KEY=secret-123\nSOME_OTHER=1\n"
        and bool(dry.removed),
    )

    run_clean(share)
    env_text = (share / ".env").read_text(encoding="utf-8")
    check(
        "cleanup blanks keys",
        "secret-123" not in env_text
        and "OPENROUTER_API_KEY=\n" in env_text
        and "GITHUB_TOKEN=" in env_text,
        env_text,
    )
    check("cleanup keeps env comments", "# OPENCODE_BASE_URL=x" in env_text)
    check("cleanup removes env backups", not (share / ".env.bak").exists())
    cleaned_config = json.loads((share / "config.json").read_text(encoding="utf-8"))
    check(
        "cleanup resets config",
        cleaned_config == CLEAN_CONFIG and "private" not in json.dumps(cleaned_config),
        json.dumps(cleaned_config),
    )
    check(
        "cleanup resets mcp",
        json.loads((share / "mcp.json").read_text(encoding="utf-8")) == {"mcpServers": {}},
    )
    check(
        "cleanup drops sessions",
        list((share / "sessions").glob("*.json")) == []
        and (share / "sessions" / ".gitkeep").is_file(),
    )
    check(
        "cleanup drops caches",
        not (share / ".cache").exists() and not (share / "agent" / "__pycache__").exists(),
    )
    check("cleanup drops logs", not (share / "run.log").exists())
    check("cleanup keeps code and plugins", (share / "plugins" / "mine.py").read_text() == "# keep me")
    check("cleanup keeps .env.example", (share / ".env.example").is_file())

    keep_share = workdir / "share-keep"
    keep_share.mkdir()
    (keep_share / "config.json").write_text('{"provider":"openai","workspace":"/private"}')
    (keep_share / "mcp.json").write_text('{"mcpServers":{"x":{"env":{"K":"v"}}}}')
    run_clean(keep_share, keep_config=True, keep_mcp=True)
    check(
        "cleanup keep flags",
        "private" in (keep_share / "config.json").read_text(encoding="utf-8")
        and "K" in (keep_share / "mcp.json").read_text(encoding="utf-8"),
    )

    with contextlib.redirect_stdout(io.StringIO()):
        exit_code = cleanup_main(["--yes", "--root", str(share)])
    check("cleanup entrypoint exits cleanly", exit_code == 0)

    # -- starting without an API key ---------------------------------------
    import subprocess as _subprocess

    os.environ.pop("NOKEY_UNSET_XYZ", None)
    nokey_config = workdir / "nokey-config.json"
    nokey_config.write_text(
        json.dumps(
            {
                "provider": "nokey",
                "providers": {
                    "nokey": {
                        "base_url": "http://127.0.0.1:1/v1",
                        "api_key_env": ["NOKEY_UNSET_XYZ"],
                        "default_model": "nokey-model",
                    }
                },
                "mcp_enabled": False,
            }
        ),
        encoding="utf-8",
    )
    try:
        load_settings(config_path=nokey_config)
        check("strict settings still require a key", False)
    except ConfigError:
        check("strict settings still require a key", True)

    nokey = load_settings(config_path=nokey_config, allow_missing_key=True)
    check(
        "lenient settings start without a key",
        nokey.provider.name == "nokey" and nokey.provider.api_key == "",
        str(nokey.provider),
    )
    try:
        Client(nokey).chat([{"role": "user", "content": "hi"}])
        check("keyless chat gives a clear error", False)
    except LLMError as exc:
        check("keyless chat gives a clear error", "No API key" in str(exc), str(exc))
    try:
        fetch_models(nokey.provider)
        check("keyless model fetch gives a clear error", False)
    except ModelListError as exc:
        check("keyless model fetch gives a clear error", "no API key" in str(exc), str(exc))

    run_env = {key: value for key, value in os.environ.items() if key != "OPENROUTER_API_KEY"}
    run_env["AGENT_OFFLINE"] = "1"
    run_cfg = workdir / "run-nokey.json"
    run_cfg.write_text(
        json.dumps(
            {
                "provider": "openrouter",
                "model": "",
                "env_file": str(workdir / "empty.env"),
                "mcp_config": str(workdir / "empty-mcp.json"),
                "sessions_dir": str(workdir / "nokey-sessions"),
            }
        ),
        encoding="utf-8",
    )
    (workdir / "empty.env").write_text("", encoding="utf-8")
    proc = _subprocess.run(
        [sys.executable, "run.py", "--config", str(run_cfg)],
        input="/keys\n/exit\n",
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=run_env,
        timeout=120,
    )
    check("run.py starts without an API key", proc.returncode == 0, proc.stderr[-400:])
    check(
        "run.py points at /keys and /provider",
        "no API key for openrouter" in proc.stdout and "OPENROUTER_API_KEY" in proc.stdout,
        proc.stdout[-500:],
    )

    # -- model discovery + picker ------------------------------------------
    import builtins

    from agent.config import Provider
    from agent.picker import select as pick_model

    fetched = fetch_models(settings.provider)
    check("fetch models", fetched == ["fake-model", "fake-model-2", "other-model"], str(fetched))
    check("fetch models sorted", fetched == sorted(fetched))

    unauthorized = Provider(
        name="smoke", base_url=settings.provider.base_url, model="x", api_key=""
    )
    try:
        fetch_models(unauthorized)
        check("missing key rejected before the request", False)
    except ModelListError as exc:
        check("missing key rejected before the request", "no api key" in str(exc).lower(), str(exc))

    wrong_key = Provider(
        name="smoke", base_url=settings.provider.base_url, model="x", api_key="wrong-key"
    )
    try:
        fetch_models(wrong_key)
        check("bad key rejected", False)
    except ModelListError as exc:
        check("bad key rejected", "401" in str(exc), str(exc))

    missing = Provider(
        name="smoke",
        base_url=settings.provider.base_url + "/nope",
        model="x",
        api_key="test-key",
    )
    try:
        fetch_models(missing)
        check("missing /models endpoint handled", False)
    except ModelListError as exc:
        check("missing /models endpoint handled", "404" in str(exc) or "/models" in str(exc), str(exc))

    model_list = quiet(app.cmd_model, "list")
    check("model list command", "fake-model-2" in model_list and "other-model" in model_list)
    bare_model = quiet(app.cmd_model, "")
    check("bare /model lists models without a TTY", "fake-model" in bare_model, bare_model)

    original_input = builtins.input
    try:
        builtins.input = lambda prompt="": "2"
        check("picker picks by number", pick_model("pick", ["m-one", "m-two", "m-three"]) == "m-two")
        builtins.input = lambda prompt="": "m-three"
        check("picker picks by id", pick_model("pick", ["m-one", "m-two", "m-three"]) == "m-three")
        builtins.input = lambda prompt="": ""
        check(
            "picker keeps current",
            pick_model("pick", ["m-one", "m-two"], current="m-one") == "m-one",
        )
        builtins.input = lambda prompt="": "typed-new-model"
        check("picker allows custom ids", pick_model("pick", ["m-one"]) == "typed-new-model")
    finally:
        builtins.input = original_input

    # -- models.dev annotations in the CLI ----------------------------------
    app._catalog = TEST_CATALOG
    app._model_cache.clear()
    rich_list = quiet(app.cmd_model, "list")
    check(
        "model list shows models.dev metadata",
        "128k ctx" in rich_list and "$3/$15 1M" in rich_list,
        rich_list,
    )

    previous_model = app.settings.model
    app.settings.model = "catalog-only"
    info_out = quiet(app.cmd_model, "info")
    check(
        "model info shows details",
        "context" in info_out and "32k" in info_out and "tool calls" in info_out,
        info_out,
    )
    app.settings.model = previous_model

    from agent.picker import _compose

    composed, _filtered = _compose(
        "t", ["a-model", "b-model"], None, "", 0, 10, {"a-model": "128k ctx · tools"}
    )
    check("picker shows metadata", any("128k ctx" in line for line in composed), str(composed))

    saved_base = app.settings.provider.base_url
    app.settings.provider.base_url = saved_base + "/nope"
    app._model_cache.clear()
    fallback_list = quiet(app.cmd_model, "list")
    check(
        "models.dev fallback when /models fails",
        "catalog-only" in fallback_list,
        fallback_list,
    )
    app.settings.provider.base_url = saved_base
    app._model_cache.clear()

    # -- context, tokens and cost ------------------------------------------
    from agent.usage import context_limit as ctx_limit
    from agent.usage import cost_for, estimate_tokens, format_cost, format_tokens

    check("token estimate", estimate_tokens("a" * 400) == 100 and estimate_tokens("") == 0)
    check(
        "token formatting",
        format_tokens(999) == "999"
        and format_tokens(128000) == "128k"
        and format_tokens(1_500_000) == "1.5M",
        format_tokens(128000),
    )
    check(
        "cost formatting",
        format_cost(0) == "$0"
        and format_cost(0.012345) == "$0.0123"
        and format_cost(0.000066) == "$0.00007",
        format_cost(0.000066),
    )
    priced = {"limit": {"context": 200000}, "cost": {"input": 3, "output": 15}}
    check("cost math", abs(cost_for(1_000_000, 1_000_000, priced) - 18.0) < 1e-9)
    check("cost unknown without pricing", cost_for(10, 10, {}) is None)
    check("context limit lookup", ctx_limit(priced) == 200000 and ctx_limit({}) == 0)

    app._catalog = TEST_CATALOG
    app.settings.model = "fake-model"
    app.settings.stream = False
    app.agent.run_turn("usage check")
    usage_out = quiet(app.cmd_usage, "")
    check(
        "usage command shows context and %",
        "context" in usage_out and "128k" in usage_out and "%" in usage_out,
        usage_out,
    )
    check(
        "usage command shows tokens and cost",
        "session" in usage_out and "in " in usage_out and "$" in usage_out,
        usage_out,
    )
    check(
        "usage command breaks down by model",
        "smoke/fake-model" in usage_out,
        usage_out,
    )
    line = app._usage_line()
    check("usage line", "ctx" in line and "session" in line and "~$" in line, line)

    quiet(app.cmd_settings, "show_usage off")
    check("usage line hidden when disabled", app._usage_line() == "")
    quiet(app.cmd_settings, "show_usage on")

    usage_before = dict(app.agent.total_usage)
    quiet(app.cmd_settings, "model fake-model")
    check("usage survives model switch", app.agent.total_usage == usage_before)

    quiet(app.cmd_save, "usage-session")
    quiet(app.cmd_new, "")
    check(
        "new session resets usage",
        app.agent.total_usage == {"prompt_tokens": 0, "completion_tokens": 0},
        str(app.agent.total_usage),
    )
    quiet(app.cmd_switch, "usage-session")
    check(
        "session restores usage",
        app.agent.total_usage == usage_before and bool(app.agent.usage_by_model),
        f"{app.agent.total_usage} vs {usage_before}",
    )

    # -- command palette ----------------------------------------------------
    from agent.lineedit import LineEditor

    lines = app.palette_lines("/")
    check("palette lists commands", any("/switch" in line for line in lines) and len(lines) >= 8)
    filtered = app.palette_lines("/sw")
    commands_shown = [line for line in filtered if line.strip().startswith("/")]
    check("palette filters", commands_shown == [filtered[0]] and "/switch" in filtered[0], str(filtered))
    check("palette no match", "no command matches" in app.palette_lines("/zz")[0])
    check("palette hidden after space", app.palette_lines("/model gpt") == [])
    check("command index complete", all(app.palette_lines("/" + n) for n in COMMAND_INDEX))
    check("command names", app.command_names("sw") == ["switch"])

    editor = LineEditor()
    import builtins

    original_input = builtins.input
    builtins.input = lambda prompt="": "typed line"
    try:
        check("editor non-tty fallback", editor.read("> ") == "typed line")
    finally:
        builtins.input = original_input
    check("editor history", editor.history == ["typed line"])

    server.shutdown()
    print("\nAll smoke tests passed.")


if __name__ == "__main__":
    main()
