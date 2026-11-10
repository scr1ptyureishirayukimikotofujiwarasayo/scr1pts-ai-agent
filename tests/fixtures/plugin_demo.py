"""Fixture plugin used by tests/smoke_test.py.

Not loaded by the harness itself; demonstrates every extension point:
a tool, a slash command, a system-prompt section, request/response hooks,
tool veto + result rewriting, and lifecycle events.
"""

from __future__ import annotations

NAME = "demo"
DESCRIPTION = "Test plugin: tool, command, prompt, hooks and lifecycle events"

EVENTS: list[str] = []


def _note(label: str) -> None:
    EVENTS.append(label)


def register(ctx):
    @ctx.tool("now", "Current time.", {"type": "object", "properties": {}})
    def now():
        return "12:00"

    @ctx.command("hello", args="[name]", description="plugin greeting")
    def hello(argument):
        print("plugin-hello", argument.strip() or "world")

    ctx.prompt("PLUGIN-PROMPT: keep answers short.")

    def on_request(messages, tools=None, **kwargs):
        messages.append({"role": "user", "content": "PLUGIN-REQUEST-MARKER"})

    def on_tool_call(name, arguments, **kwargs):
        if name == "write_file" and arguments.get("path") == "blocked.txt":
            return "blocked.txt is not allowed"
        return None

    def on_tool_result(name, result, is_error, **kwargs):
        if name == "write_file" and not is_error and "rewritten.txt" in result:
            return "result-rewritten"
        return None

    def on_session_change(kind, name=None, **kwargs):
        _note(f"session:{kind}")

    def on_model_change(provider, model, **kwargs):
        _note(f"model:{provider}/{model}")

    def on_mode_change(mode, **kwargs):
        _note(f"mode:{mode}")

    def on_compact(result, **kwargs):
        _note(f"compact:{result.method}")

    ctx.hook("on_request", on_request)
    ctx.hook("on_tool_call", on_tool_call)
    ctx.hook("on_tool_result", on_tool_result)
    ctx.hook("on_session_change", on_session_change)
    ctx.hook("on_model_change", on_model_change)
    ctx.hook("on_mode_change", on_mode_change)
    ctx.hook("on_compact", on_compact)
    ctx.hook("on_turn_end", lambda text, **_: None)
