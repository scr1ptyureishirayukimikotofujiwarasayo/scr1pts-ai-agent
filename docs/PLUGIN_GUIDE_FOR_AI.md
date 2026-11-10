# Plugin guide for AI agents

You are an AI coding agent and the user wants to change how **this harness**
behaves — new tools, new slash commands, extra system-prompt rules, request
rewrites, guardrails, or a custom loop. Almost all of that can be done with one
plugin file in `plugins/`, without touching core code.

This guide is written for you: read it, copy the patterns, keep the change
minimal, and verify it before finishing.

---

## 1. Ground rules

- **Pure standard library.** The harness has no dependencies. Do not add any
  (no `pip install`). Plugins run under the same interpreter as the agent.
- **Never hardcode secrets.** Read `os.environ` (or `ctx.settings`) instead, and
  never print tokens.
- **Never block at import.** `register(ctx)` runs during startup; do network or
  slow work lazily inside a tool/command.
- **Be idempotent.** Plugins are re-imported on `/settings reload`,
  `/settings plugins_dir …` and `/mcp reload`. Module state resets; global
  monkeypatches do not — guard them (see §6).
- **Fail small.** A broken plugin is reported and skipped; a broken *registration*
  can still leave partial state. Validate inputs in tools/commands and let errors
  propagate as tool results, not crashes.
- **No secrets, no telemetry, no background threads** unless the user asked for
  them.

Useful files to read first:

| file | why |
|---|---|
| `agent/plugins.py` | loader, `PluginContext`, hooks |
| `agent/tools.py` | registry, `Tool` flags (`dangerous`, `read_only`, `priority`) |
| `agent/agent.py` | loop, where hooks fire, system prompt assembly |
| `agent/cli.py` | slash commands, palette, `/help`, output rendering |
| `agent/llm.py` | request shape, streaming, reasoning usage |
| `agent/config.py` | `Settings` fields available to plugins |
| `tests/fixtures/plugin_demo.py` | working example of every extension point |

---

## 2. The contract

A plugin is a `.py` file or a package folder in `plugins/`. The loader skips
names starting with `.` or `_`, imports the module, and calls `register(ctx)` if
present.

```python
"""plugins/wordcount.py"""
from __future__ import annotations

NAME = "wordcount"
DESCRIPTION = "Adds /wc and a word_count tool"


def register(ctx):
    ...
```

`ctx` exposes:

| attribute / method | purpose |
|---|---|
| `ctx.tool(name, description, parameters=None, dangerous=False, read_only=False, priority=0)` | decorator registering a tool; receives `**arguments` matching the JSON schema |
| `ctx.command(name, args="", description="")` | decorator registering a slash command; handler receives the raw argument string |
| `ctx.prompt(text)` | append a section to the system prompt |
| `ctx.hook(event, fn)` | subscribe to an event (see §4) |
| `ctx.registry` | `Registry`: `add/get/names/specs`, inspect other tools |
| `ctx.hooks` | `Hooks`: `on/run/first_result` |
| `ctx.settings` | live `Settings` (provider, model, mode, reasoning effort, dirs …) |
| `ctx.workspace` | `Path` of the workspace |
| `ctx.root` | `Path` of the plugins folder |
| `ctx.log(msg)` | startup-safe logging (printed dimmed) |
| `ctx.plugin` | `Plugin` dataclass (name, path, description, tools) |

### Tool flags

- `dangerous=True` → asks the user (unless `confirm_shell: false`) and is
  blocked in **plan mode**.
- `read_only=True` → available in plan mode (read/inspect tools only).
- `priority>0` sorts earlier in the schema the model receives (built-in
  `run_python` uses 10).
- Name collisions are auto-namespaced: `myplugin.toolname`.

Return a `str`, `dict`/`list` (JSON-rendered), or raise; exceptions become an
`Error: …` tool result the model can react to.

---

## 3. Recipes

### 3.1 A tool

```python
import json
import urllib.request

def register(ctx):
    @ctx.tool(
        "http_get_json",
        "Fetch a URL and return parsed JSON.",
        {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
        read_only=True,
    )
    def http_get_json(url: str):
        request = urllib.request.Request(url, headers={"User-Agent": "python-ai-agent"})
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
```

### 3.2 A slash command (palette + `/help` included)

```python
def register(ctx):
    @ctx.command("wc", args="<path>", description="count lines/words/bytes")
    def word_count(arguments):
        from pathlib import Path
        path = Path(arguments.strip() or ".")
        if path.is_dir():
            files = [p for p in path.rglob("*") if p.is_file()]
            print(f"{len(files)} files under {path}")
            return
        text = path.read_text(encoding="utf-8", errors="replace")
        print(f"{len(text.splitlines())} lines · {len(text.split())} words · {len(text)} bytes")
```

Rules:

- The handler name must be unique across plugins; static commands (`/help`,
  `/model`, …) always win over plugin commands.
- Printing is the output. Return `"exit"` to quit the REPL.
- Exceptions are caught and shown as `plugin command /x failed: …`.

### 3.3 Extra system-prompt rules

```python
def register(ctx):
    ctx.prompt(
        "## Team conventions\n"
        "- Python 3.10+, type hints, `pathlib` over `os.path`.\n"
        "- Run `python tests/smoke_test.py` after changes."
    )
```

`ctx.prompt()` cannot replace existing sections; for that, edit
`agent/agent.py` or monkeypatch (see §6).

### 3.4 Rewrite the outgoing request

```python
def register(ctx):
    def add_datetime(messages, tools=None, **kwargs):
        from datetime import datetime
        messages.append({
            "role": "user",
            "content": f"Local time is {datetime.now():%Y-%m-%d %H:%M}.",
        })
    ctx.hook("on_request", add_datetime)
```

Mutate `messages` in place. `tools` is the (possibly plan-mode-filtered) schema
list; mutate it to add/remove tools for one request.

### 3.5 Inspect or rewrite responses

```python
def register(ctx):
    def tag(completion, **kwargs):
        completion.content = completion.content.replace("I think", "Probably")
    ctx.hook("on_response", tag)
```

`completion` has `.content`, `.reasoning`, `.tool_calls`, `.usage`,
`.finish_reason`. Mutating `.tool_calls` before the loop executes them works.

### 3.6 Replace the user's message

```python
def register(ctx):
    ctx.hook("on_user_message", lambda text, **_: text.replace("colour", "color"))
```

Return a string to replace, `None` to leave it unchanged.

### 3.7 Veto a tool call

`on_tool_call` may return a string to block execution and feed that message back
to the model, or `False` for a generic block. This is enforced in the loop — the
tool never runs.

```python
def register(ctx):
    def deny_dangerous_writes(name, arguments, **kwargs):
        if name == "write_file" and str(arguments.get("path", "")).startswith("prod/"):
            return "writes under prod/ are disabled by policy"
        return None
    ctx.hook("on_tool_call", deny_dangerous_writes)
```

Ordering: plan mode runs first and wins; then plugin veto; then confirmation;
then execution.

### 3.8 Rewrite a tool result

`on_tool_result` can return a replacement string (useful for redaction, trimming,
or adding guidance). Returning `None` observes only.

```python
def register(ctx):
    def redact(name, result, is_error, **kwargs):
        if not is_error:
            return result.replace("sk-", "sk-<redacted>")
        return None
    ctx.hook("on_tool_result", redact)
```

### 3.9 React to lifecycle changes

Keep plugin state coherent across `/clear`, `/new`, `/switch`, `/mode`,
`/compact` and model switches:

```python
STATE = {"session": None}

def register(ctx):
    ctx.hook("on_session_change",
             lambda kind, name=None, **_: STATE.update(session=name, kind=kind))
    ctx.hook("on_model_change",
             lambda provider, model, **_: ctx.log(f"now using {provider}/{model}"))
    ctx.hook("on_mode_change", lambda mode, **_: ctx.log(f"mode is {mode}"))
    ctx.hook("on_compact", lambda result, **_: ctx.log(f"compacted via {result.method}"))
```

### 3.10 Guardrail for shell commands

```python
def register(ctx):
    def screen(name, arguments, **kwargs):
        if name == "run_command" and "rm -rf /" in arguments.get("command", ""):
            return {"command": "echo blocked by safety hook"}
    ctx.hook("on_tool_call", screen)
```

Returning a dict replaces the arguments (there is no veto; return a harmless
replacement). Plan mode is the hard, non-bypassable read-only gate.

---

## 4. Event reference

Hooks are called as `fn(**kwargs)`; an exception is printed and ignored.

| event | arguments | return value honoured |
|---|---|---|
| `on_start` | `agent` | ignored |
| `on_system_prompt` | `agent` | `str` sections appended |
| `on_user_message` | `text` | `str` replaces the message |
| `on_request` | `messages`, `tools`, `agent` | ignore (mutate in place) |
| `on_response` | `completion`, `agent` | ignore (mutate in place) |
| `on_tool_call` | `name`, `arguments` | `dict` replaces arguments; `str`/`False` **vetoes** the call |
| `on_tool_result` | `name`, `result`, `is_error` | `str` replaces the result |
| `on_assistant_message` | `text` | ignored |
| `on_turn_end` | `text` | ignored |
| `on_session_change` | `kind`, `name`, `agent` | ignored (`kind` = `new` / `switch` / `clear`) |
| `on_model_change` | `provider`, `model`, `agent` | ignored (also fires on capability reload) |
| `on_mode_change` | `mode`, `agent` | ignored (`plan` / `build`) |
| `on_compact` | `result`, `agent` | ignored (after `/compact`) |
| `on_shutdown` | — | ignored |

Fire order per turn: `on_user_message` → `on_request` → `on_response` →
(`on_tool_call` → `on_tool_result`)* → `on_assistant_message` → `on_turn_end`.

---

## 5. Changing the harness overall

Pick the smallest lever that does the job:

1. **New capability for the model** → `ctx.tool`.
2. **New user-facing workflow** → `ctx.command`.
3. **Persistent behavior change** → `ctx.prompt` / `on_system_prompt`.
4. **Per-request shaping** (headers, context injection, redaction) →
   `on_request`; for HTTP headers or retries, edit `agent/llm.py` instead.
5. **Cross-cutting loop change** (auto-compaction, retry policy, budget caps) →
   `on_response`/`on_turn_end` for bookkeeping, or patch `agent/agent.py` when a
   hook cannot express it.
6. **Genuinely new core feature** → edit `agent/*.py`, add tests, update
   `README.md`; do not fake it in a plugin.

Examples of each:

| goal | where |
|---|---|
| add `/deploy`, `/backup` | plugin command |
| "always answer in metric" | plugin prompt |
| redact secrets before sending | `on_request` |
| cap response length | `on_response` or `max_tokens` setting |
| add a new provider | `config.json` `providers` (no code) |
| change the agent loop / tool policy | `agent/agent.py` |
| new transport (native Anthropic/Gemini) | `agent/llm.py` |

### Deep patching (use sparingly)

Plugins are Python modules, so they can patch anything at import time. This runs
on every reload, so guard it:

```python
def register(ctx):
    import agent.agent as core

    if not getattr(core.Agent, "_my_patch", False):
        original = core.Agent.build_system_prompt

        def patched(self):
            return original(self) + "\n\n## Custom policy\nNever use `rm`."

        core.Agent.build_system_prompt = patched
        core.Agent._my_patch = True
```

Prefer hooks; monkeypatching is global, hidden from `/help`, and easy to stack
twice. If you patch, document it in the plugin docstring and keep it reversible.

---

## 5.1 What a plugin can and cannot change

| can do today | how |
|---|---|
| add tools (with schemas, danger/read-only flags, ordering) | `ctx.tool` |
| add slash commands, palette entries, `/help` docs | `ctx.command` |
| append system-prompt sections | `ctx.prompt` / `on_system_prompt` |
| rewrite outbound messages/tools per request | `on_request` |
| rewrite replies, reasoning, tool calls before execution | `on_response` |
| veto or rewrite tool calls and results | `on_tool_call` / `on_tool_result` |
| react to sessions, model/mode switches, compaction | lifecycle hooks (§3.9) |
| read/modify live settings (provider headers, mode, effort) | `ctx.settings` |
| inspect/remove tools at runtime | `ctx.registry` |
| keep state across turns | module globals (reset on reload) |
| patch the loop/transport/UI arbitrarily | monkeypatch (§5.2) |

Not first-class yet — keep it in a plugin or edit core:

| limitation | workaround |
|---|---|
| add persisted `/settings` keys | own command + JSON file under `ctx.workspace`, or edit `agent/settings_spec.py` |
| add CLI flags | env vars read in `register`, or edit `agent/cli.py` |
| new providers/transports | `config.json` providers; edit `agent/llm.py` for a new wire protocol |
| async/background agents | not supported; run work synchronously in tools |
| custom TUI widgets | print from commands; deeper changes belong in `agent/cli.py` |
| replace the system prompt (not append) | monkeypatch `build_system_prompt` or set `ctx.settings.system_prompt` |

Use this table to decide whether the task needs a plugin or a core patch.

## 6. Test your plugin

Fast manual loop:

```bat
copy plugins\myplugin.py plugins\ 2>nul
python run.py --provider lmstudio
```
```
> /reload            # re-import plugins/skills/MCP without restarting
> /plugins
> /tools
> /help
> /wc agent/cli.py
```

After editing a plugin file, `/reload` is the iteration loop: it rebuilds the
registry, re-registers commands/prompts/hooks and preserves the conversation.
`/reload config` additionally re-reads `config.json` (useful after adding a
provider or changing a setting).

Offline, deterministic checks (no network, no key):

```bash
python -m compileall -q agent tests
python tests/smoke_test.py
```

For an isolated plugin test, reuse the harness API:

```python
import json, os, tempfile
from pathlib import Path
from agent.cli import Application
from agent.config import load_settings

os.environ["AGENT_OFFLINE"] = "1"
tmp = Path(tempfile.mkdtemp())
(tmp / "plugins").mkdir()
(tmp / "plugins" / "demo.py").write_text(PLUGIN_SOURCE, encoding="utf-8")
config = tmp / "config.json"
config.write_text(json.dumps({
    "provider": "lmstudio",
    "plugins_dir": str(tmp / "plugins"),
    "sessions_dir": str(tmp / "sessions"),
    "mcp_config": str(tmp / "mcp.json"),
}), encoding="utf-8")
(tmp / "mcp.json").write_text('{"mcpServers": {}}\n', encoding="utf-8")

app = Application(load_settings(config_path=config, allow_missing_key=True))
assert "my_tool" in app.registry.names()
assert "my_cmd" in app.command_names("")
assert "MY PROMPT" in app.agent.system_prompt
```

Note: tools run as Python in-process — keep tests' plugins side-effect free.

---

## 7. AI-agent checklist

Before you say the task is done:

- [ ] Read `agent/plugins.py`, `agent/tools.py` and `agent/agent.py` first.
- [ ] One plugin file in `plugins/`, `NAME` + `DESCRIPTION` + `register(ctx)`.
- [ ] Unique tool/command names; correct schema types; `required` set.
- [ ] `read_only=True` for inspection tools, `dangerous=True` for writers.
- [ ] No secrets, no network at import, no blocking work.
- [ ] Hooks mutate only what they must; they cannot veto (say so in the docstring).
- [ ] Monkeypatches guarded against double-application.
- [ ] Tested: `/reload`, `/tools`, `/help`, the command/tool itself, `/clear` still works.
- [ ] `python tests/smoke_test.py` passes (Linux and Windows if you can).
- [ ] README/docs updated if user-facing behavior changed.
- [ ] You never edited core files to do something a hook/command could do.

## 8. Copy-paste task brief

> Add a plugin at `plugins/<name>.py` for this harness.
> 1. Read `docs/PLUGIN_GUIDE_FOR_AI.md`, `agent/plugins.py`, `agent/agent.py`.
> 2. Implement exactly what the user asked, using `ctx.tool`, `ctx.command`,
>    `ctx.prompt` or hooks as appropriate — no core edits unless unavoidable.
> 3. Use only the Python standard library, no secrets, no startup blocking.
> 4. Add no example/testing code to `plugins/` beyond what is needed.
> 5. Verify with the app (tool/command/palette/help) and run
>    `python tests/smoke_test.py`.
> 6. Report: files changed, how to invoke it, and test results.
