"""Plugin system.

Drop a `.py` file (or a package folder) into `plugins/`. Each plugin may expose:

    NAME = "weather"                 # optional, defaults to the file name
    DESCRIPTION = "Adds weather tools"

    def register(ctx):
        # add tools...
        ctx.tool("get_weather", "Get current weather.", {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        })(get_weather)

        # add a slash command...
        @ctx.command("weather", args="<city>", description="show the weather")
        def weather(arguments):
            print(get_weather(city=arguments.strip()))

        # add instructions to the system prompt...
        ctx.prompt("Always answer in metric units.")

        # observe / react to agent events...
        ctx.hook("on_tool_result", lambda name, result, **_: print(name))

Available hooks:
    on_start(agent)
    on_system_prompt(agent)        -> may return extra prompt text
    on_user_message(text)          -> may return a replacement string
    on_request(messages, tools)     -> mutate the outgoing request in place
    on_response(completion)         -> mutate the Completion before it is used
    on_assistant_message(text)
    on_tool_call(name, arguments)  -> dict replaces args; str/False vetoes the call
    on_tool_result(name, result, is_error) -> may return a replacement string
    on_turn_end(text)
    on_session_change(kind, name)   -> kind = "new" | "switch" | "clear"
    on_model_change(provider, model)
    on_mode_change(mode)            -> "plan" | "build"
    on_compact(result)
    on_shutdown()

A broken plugin is reported and skipped; it never takes the agent down.
"""

from __future__ import annotations

import importlib.util
import sys
import traceback
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from .config import Settings
from .tools import Registry

HookFn = Callable[..., Any]


class Hooks:
    def __init__(self) -> None:
        self._hooks: dict[str, list[tuple[str, HookFn]]] = defaultdict(list)
        self.errors: list[str] = []

    def on(self, event: str, func: HookFn, owner: str = "plugin") -> None:
        self._hooks[event].append((owner, func))

    def run(self, event: str, **kwargs: Any) -> list[Any]:
        results: list[Any] = []
        for owner, func in list(self._hooks.get(event, [])):
            try:
                results.append(func(**kwargs))
            except Exception as exc:  # noqa: BLE001 - plugins must not crash the agent
                message = f"plugin '{owner}' failed in {event}: {type(exc).__name__}: {exc}"
                self.errors.append(message)
                print(f"[plugin error] {message}", file=sys.stderr)
        return results

    def first_result(self, event: str, **kwargs: Any) -> Any:
        for result in self.run(event, **kwargs):
            if result is not None:
                return result
        return None

    def describe(self) -> dict[str, int]:
        return {event: len(funcs) for event, funcs in self._hooks.items() if funcs}


@dataclass
class Plugin:
    name: str
    path: Path
    description: str = ""
    module: ModuleType | None = None
    tools: list[str] = field(default_factory=list)


class PluginContext:
    """The object handed to a plugin's `register(ctx)` function."""

    def __init__(
        self,
        plugin: Plugin,
        registry: Registry,
        hooks: Hooks,
        settings: Settings,
        log: Callable[[str], None] = print,
        commands: dict[str, Any] | None = None,
    ) -> None:
        self.plugin = plugin
        self.registry = registry
        self.hooks = hooks
        self.settings = settings
        self.workspace = Path(settings.workspace)
        self.root = Path(settings.plugins_dir)
        self.log = log
        self.commands = commands if commands is not None else {}

    def tool(self, name: str, description: str, parameters: dict[str, Any] | None = None,
             dangerous: bool = False,
             read_only: bool = False) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator that registers a tool. Namespaced as '<plugin>.<name>' if clashing."""

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            tool_name = name
            if tool_name in self.registry.tools:
                tool_name = f"{self.plugin.name}.{name}"
            self.registry.add(
                tool_name, description, parameters, func,
                dangerous=dangerous, read_only=read_only,
            )
            self.plugin.tools.append(tool_name)
            return func

        return decorator

    def command(
        self, name: str, args: str = "", description: str = ""
    ) -> Callable[[Callable[[str], Any]], Callable[[str], Any]]:
        """Register a slash command. The handler receives the raw argument string."""
        key = name.strip().lstrip("/")
        if not key:
            raise ValueError("command name must not be empty")

        def decorator(func: Callable[[str], Any]) -> Callable[[str], Any]:
            self.commands[key] = {
                "name": key,
                "args": args,
                "description": description,
                "handler": func,
                "plugin": self.plugin.name,
            }
            return func

        return decorator

    def prompt(self, text: str) -> None:
        """Append a section to the system prompt (handy for house rules)."""
        value = text.strip()
        if not value:
            return
        self.hooks.on(
            "on_system_prompt",
            lambda agent=None, **_: value,
            owner=self.plugin.name,
        )

    def hook(self, event: str, func: HookFn) -> HookFn:
        if not event.startswith("on_"):
            raise ValueError(f"hook name must start with 'on_': {event}")
        self.hooks.on(event, func, owner=self.plugin.name)
        return func


def load_plugins(
    directory: Path,
    registry: Registry,
    hooks: Hooks,
    settings: Settings,
    log: Callable[[str], None] = print,
    commands: dict[str, Any] | None = None,
) -> tuple[list[Plugin], list[str]]:
    """Import every plugin in `directory`. Returns (plugins, errors)."""
    directory = Path(directory)
    loaded: list[Plugin] = []
    errors: list[str] = []
    if not directory.is_dir():
        return loaded, errors

    candidates = sorted(
        path
        for path in directory.iterdir()
        if not path.name.startswith((".", "_"))
        and (
            (path.is_file() and path.suffix == ".py")
            or (path.is_dir() and (path / "__init__.py").is_file())
        )
    )

    for candidate in candidates:
        name = candidate.stem if candidate.is_file() else candidate.name
        plugin = Plugin(name=name, path=candidate)
        try:
            module = _import_plugin(candidate, name)
            plugin.module = module
            plugin.name = str(getattr(module, "NAME", None) or name)
            plugin.description = str(getattr(module, "DESCRIPTION", "") or "")
            register = getattr(module, "register", None)
            if callable(register):
                context = PluginContext(
                    plugin, registry, hooks, settings, log=log, commands=commands
                )
                register(context)
            loaded.append(plugin)
            log(f"plugin loaded: {plugin.name}" + (f" ({plugin.description})" if plugin.description else ""))
        except Exception as exc:  # noqa: BLE001
            detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            errors.append(f"{candidate.name}: {detail}")
            log(f"plugin failed: {candidate.name}: {detail}")

    return loaded, errors


def _import_plugin(path: Path, name: str) -> ModuleType:
    safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in name)
    module_name = f"agent_plugin_{safe}"
    if module_name in sys.modules:
        del sys.modules[module_name]

    if path.is_dir():
        spec = importlib.util.spec_from_file_location(
            module_name, path / "__init__.py", submodule_search_locations=[str(path)]
        )
    else:
        spec = importlib.util.spec_from_file_location(module_name, path)

    if spec is None or spec.loader is None:
        raise ImportError(f"cannot create import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    plugin_dir = str(path.parent if path.is_file() else path)
    inserted = plugin_dir not in sys.path
    if inserted:
        sys.path.insert(0, plugin_dir)
    try:
        spec.loader.exec_module(module)
    finally:
        if inserted:
            try:
                sys.path.remove(plugin_dir)
            except ValueError:
                pass
    return module


def unload_hooks(hooks: Hooks, plugins: list[Plugin]) -> None:
    """Best-effort hook cleanup (used when reloading plugins)."""
    owners = {plugin.name for plugin in plugins}
    for event, funcs in list(hooks._hooks.items()):  # noqa: SLF001 - same module
        hooks._hooks[event] = [(owner, fn) for owner, fn in funcs if owner not in owners]  # noqa: SLF001
