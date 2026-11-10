"""Interactive terminal interface: `python run.py`."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .agent import Agent
from .compact import SUMMARY_HEADING, summary_section
from .config import ROOT, ConfigError, Settings, build_provider, load_config_file, load_settings, save_config_file
from .essentials import register_essentials
from .lineedit import LineEditor, interactive
from .llm import Client, LLMError
from .mcp import MCPClient, connect_servers, load_mcp_config, stop_clients
from .models import ModelListError, fetch_models
from .modelsdev import describe as describe_model
from .modelsdev import info as model_info
from .modelsdev import load_catalog
from .modelsdev import lookup as catalog_lookup
from .modelsdev import models_for as catalog_models
from .picker import select as select_model
from .secrets import mask, parse_env, remove_env_var, set_env_var
from .plugins import Hooks, load_plugins
from .sessions import SessionInfo, SessionStore, slug
from .settings_spec import DEFAULTS as SETTING_DEFAULTS
from .settings_spec import SETTINGS, SETTINGS_BY_KEY, coerce, display_value, hint, resolve_key
from .skills import SkillLoader
from .tools import Registry
from .usage import context_limit, cost_for, format_cost, format_percent, format_tokens
from .util import (
    VERSION,
    bold,
    cyan,
    dim,
    enable_ansi,
    enable_utf8,
    green,
    red,
    truncate,
    yellow,
)

BANNER = r"""
██████╗ ██╗   ██╗████████╗██╗  ██╗ ██████╗ ███╗   ██╗
██╔══██╗╚██╗ ██╔╝╚══██╔══╝██║  ██║██╔═══██╗████╗  ██║
██████╔╝ ╚████╔╝    ██║   ███████║██║   ██║██╔██╗ ██║
██╔═══╝   ╚██╔╝     ██║   ██╔══██║██║   ██║██║╚██╗██║
██║        ██║      ██║   ██║  ██║╚██████╔╝██║ ╚████║
╚═╝        ╚═╝      ╚═╝   ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝

 █████╗ ██╗     █████╗  ██████╗ ███████╗███╗   ██╗████████╗
██╔══██╗██║    ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝
███████║██║    ███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║
██╔══██║██║    ██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║
██║  ██║██║    ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║
╚═╝  ╚═╝╚═╝    ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝
"""


@dataclass(frozen=True)
class Command:
    name: str
    args: str
    description: str

    @property
    def usage(self) -> str:
        return f"/{self.name}" + (f" {self.args}" if self.args else "")


COMMANDS: tuple[Command, ...] = (
    Command("new", "[name]", "start a new session (saves the current one)"),
    Command("sessions", "", "list saved sessions, numbered"),
    Command("switch", "<n|name>", "switch to another saved session"),
    Command("save", "[name]", "save the current session (optionally as)"),
    Command("delete", "<n|name>", "delete a saved session"),
    Command("compact", "[keep-turns|show]", "summarise older turns, keep recent ones"),
    Command("clear", "", "forget the conversation in this session"),
    Command("model", "[name|list|info]", "pick a model (↑/↓ + Enter) or set one directly"),
    Command("provider", "<name> [model]", "switch provider"),
    Command("providers", "", "list configured providers"),
    Command("usage", "", "context %, tokens and estimated cost"),
    Command("mode", "[plan|build]", "show or switch: plan (read-only) / build"),
    Command("agents", "[reload]", "show AGENTS.md project instructions"),
    Command("tools", "", "list registered tools"),
    Command("skills", "", "list installed skills"),
    Command("plugins", "", "list loaded plugins"),
    Command("system", "[text]", "show or replace the system prompt"),
    Command("verbose", "", "toggle latency / token stats"),
    Command("settings", "[key] [value]", "view or change settings (save/reload/reset)"),
    Command("keys", "[list|set|clear] [provider] [key]", "list, set or clear API keys"),
    Command("mcp", "[reload|tools|path]", "list or reload MCP servers"),
    Command("help", "", "show this help"),
    Command("version", "", "print the version"),
    Command("quit", "", "leave the agent"),
)

COMMAND_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Sessions", ("new", "sessions", "switch", "save", "delete", "compact", "clear")),
    ("Model", ("model", "provider", "providers")),
    ("Agent", ("usage", "mode", "agents", "tools", "skills", "plugins", "system", "verbose")),
    ("Config", ("settings", "keys", "mcp")),
    ("App", ("help", "version", "quit")),
)

ALIASES = {
    "exit": "quit",
    "q": "quit",
    "quit!": "quit",
    "?": "help",
    "h": "help",
    "resume": "switch",
    "load": "switch",
    "open": "switch",
    "rm": "delete",
    "providerlist": "providers",
    "list": "sessions",
    "config": "settings",
    "set": "settings",
    "apikey": "keys",
    "apikeys": "keys",
    "key": "keys",
    "servers": "mcp",
    "models": "model",
    "cost": "usage",
    "tokens": "usage",
    "context": "usage",
    "agentsmd": "agents",
    "instructions": "agents",
}

COMMAND_INDEX = {command.name: command for command in COMMANDS}


def help_text() -> str:
    lines = [bold("Commands") + dim("  (type / and the list appears as you type)")]
    for group, names in COMMAND_GROUPS:
        lines.append("")
        lines.append(dim(group))
        width = max(len(COMMAND_INDEX[name].usage) for name in names)
        for name in names:
            command = COMMAND_INDEX[name]
            lines.append(f"  {command.usage:<{width}}  {command.description}")
    lines.append("")
    lines.append(dim("Any other input is sent to the model. End a line with \\ to continue it."))
    return "\n".join(lines)


HELP = help_text()


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="A small, plugin-friendly Python AI agent harness.",
    )
    parser.add_argument("-p", "--prompt", help="run a single prompt and exit")
    parser.add_argument("--provider", help="provider name, e.g. openrouter")
    parser.add_argument("-m", "--model", help="model id")
    parser.add_argument("-w", "--workspace", help="workspace directory for file tools")
    parser.add_argument("--config", help="path to config.json")
    parser.add_argument("--no-stream", action="store_true", help="disable token streaming")
    parser.add_argument("--yes", action="store_true", help="auto-approve dangerous tools")
    parser.add_argument("--mode", choices=("plan", "build"), help="start in read-only plan mode or build mode")
    parser.add_argument(
        "--clean",
        action="store_true",
        help="remove API keys, sessions, caches and reset configs, then exit",
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=f"python ai agent {VERSION}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    enable_ansi()
    enable_utf8()
    args = parse_args(argv)

    if args.clean:
        # Runs before settings are loaded: cleanup must work with no valid key.
        from .cleanup import main as cleanup_main

        return cleanup_main(["--yes"] if args.yes else [])

    try:
        settings = load_settings(
            config_path=Path(args.config).resolve() if args.config else None,
            provider=args.provider,
            model=args.model,
            workspace=Path(args.workspace) if args.workspace else None,
            allow_missing_key=True,  # keys can be added from inside the REPL
        )
    except ConfigError as exc:
        print(red(f"Configuration error: {exc}"), file=sys.stderr)
        print(dim(f"Hint: copy .env.example to .env in {ROOT} and add your key."), file=sys.stderr)
        return 2

    if args.no_stream:
        settings.stream = False
    if args.verbose:
        settings.verbose = True
    if args.yes:
        settings.confirm_shell = False
    if args.mode:
        settings.mode = args.mode

    app = Application(settings)
    try:
        if args.prompt:
            return app.run_once(args.prompt)
        return app.repl()
    finally:
        app._stop_mcp()


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# application
# ---------------------------------------------------------------------------


class Application:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.hooks = Hooks()
        self.registry = Registry()
        self.skills = SkillLoader(settings.skills_dir)
        register_essentials(self.registry, settings, skill_reader=self.skills.read)

        self.plugins, self.plugin_errors = load_plugins(
            settings.plugins_dir, self.registry, self.hooks, settings,
            log=lambda msg: print(dim(msg)),
        )
        for error in self.plugin_errors:
            print(yellow(f"plugin error: {error}"))

        self.mcp_clients: list[MCPClient] = []
        self.mcp_errors: list[str] = []
        self._connect_mcp()

        self.client = Client(settings)
        self.agent = Agent(
            settings, self.client, self.registry, self.skills, self.hooks,
            confirm=self.confirm_tool,
        )
        self.approved: set[str] = set()
        self._last_elapsed = 0.0
        self._model_cache: dict[str, list[str]] = {}
        self._catalog: dict[str, Any] | None = None
        self._catalog_note = ""
        self._catalog_lock = threading.Lock()
        if os.environ.get("AGENT_OFFLINE") != "1":
            threading.Thread(
                target=self._warm_catalog, name="modelsdev-warm", daemon=True
            ).start()

        # sessions
        self.sessions = SessionStore(settings.sessions_dir)
        self.session_name = ""
        self.session_created = _now()
        self._listing: list[SessionInfo] = []

        self.editor = LineEditor(
            palette=self.palette_lines, completer=self.command_names, max_palette=20
        )
        self.hooks.run("on_start", agent=self.agent)

    # -- command palette ----------------------------------------------------

    def command_names(self, prefix: str = "") -> list[str]:
        return [command.name for command in COMMANDS if command.name.startswith(prefix)]

    def palette_lines(self, buffer: str) -> list[str]:
        """Lines shown under the prompt while the user types a `/` command."""
        if not buffer.startswith("/") or " " in buffer:
            return []
        typed = buffer[1:].lower()
        matches = [command for command in COMMANDS if command.name.startswith(typed)]
        if not matches:
            return [dim(f"  no command matches /{typed}")]
        if not typed:
            return _compact_menu(matches)
        width = max(len(command.usage) for command in matches)
        lines = [f"  {command.usage:<{width}}  {dim(command.description)}" for command in matches]
        lines.append(dim("  Tab completes · Enter runs · /help for details"))
        return lines

    # -- tools confirmation -------------------------------------------------

    def confirm_tool(self, name: str, arguments: dict[str, Any]) -> bool:
        if not self.settings.confirm_shell or name in self.approved:
            return True
        preview = json.dumps(arguments, ensure_ascii=False, indent=2, default=str)
        print(yellow(f"\n[tool] {name}") + dim("\n" + truncate(preview, 1200)))
        try:
            answer = input(bold("Allow? [y]es / [n]o / [a]lways: ")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        if answer in ("a", "always", "all"):
            self.approved.add(name)
            return True
        return answer in ("", "y", "yes")

    # -- turns --------------------------------------------------------------

    def _callbacks(self) -> dict[str, Any]:
        state = {"wrote": False}

        def on_delta(piece: str) -> None:
            state["wrote"] = True
            sys.stdout.write(piece)
            sys.stdout.flush()

        def on_tool(name: str, arguments: dict[str, Any]) -> None:
            if state["wrote"]:
                sys.stdout.write("\n")
                state["wrote"] = False
            detail = _summarise_arguments(arguments)
            print(cyan(f"  -> {name}") + (dim(f" {detail}") if detail else ""))

        def on_tool_result(name: str, result: str, is_error: bool) -> None:
            if not self.settings.show_tool_results:
                return
            first = result.strip().splitlines()[0] if result.strip() else "(empty)"
            colour = red if is_error else green
            print(colour(f"  <- {name}: ") + dim(truncate(first, 160)))

        return {"on_delta": on_delta, "on_tool": on_tool, "on_tool_result": on_tool_result, "state": state}

    def run_once(self, prompt: str) -> int:
        callbacks = self._callbacks()
        started = time.monotonic()
        try:
            text = self.agent.run_turn(
                prompt,
                on_delta=callbacks["on_delta"],
                on_tool=callbacks["on_tool"],
                on_tool_result=callbacks["on_tool_result"],
            )
        except LLMError as exc:
            print(red(f"\nModel error: {exc}"), file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print(yellow("\n[interrupted]"))
            return 130
        if not callbacks["state"]["wrote"]:
            print(text)
        else:
            print()
        self._print_stats(started)
        self._print_usage()
        self.auto_save()
        return 0

    def repl(self) -> int:
        print(cyan(BANNER))
        print(
            bold(f"python ai agent {VERSION}")
            + dim(f"  ·  {self.settings.provider.name}/{self.settings.model}")
            + (yellow("  ·  mode: plan (read-only)") if self.settings.mode == "plan" else "")
        )
        if self.settings.provider.requires_key and not self.settings.provider.api_key:
            print(
                yellow(f"no API key for {self.settings.provider.name}")
                + dim(
                    f"  ·  /keys set {self.settings.provider.name} <key>"
                    "  ·  /provider <name> to switch  ·  /keys to list"
                )
            )
        print(dim(f"workspace: {self.settings.workspace}"))
        print(
            dim(
                f"tools: {len(self.registry.tools)}  ·  skills: {len(self.skills.skills)}"
                f"  ·  plugins: {len(self.plugins)}"
                + (f"  ·  mcp: {len(self.mcp_clients)}" if self.mcp_clients else "")
                + "  ·  /help for commands"
            )
        )
        if self.agent.instructions:
            print(
                dim(
                    f"project instructions: {len(self.agent.instructions)} AGENTS.md file(s)"
                    f"  ·  /agents to inspect"
                )
            )
        listing = self._refresh_listing()
        if listing:
            print(dim(f"sessions: {len(listing)} saved  ·  /sessions to list, /switch <n|name>"))
        if self.skills.errors:
            for error in self.skills.errors:
                print(yellow(f"skill warning: {error}"))

        while True:
            try:
                raw = self.editor.read(self._prompt())
            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                print()
                print(dim("(cancelled · Ctrl+D or /quit to exit)"))
                continue
            text = _read_multiline(self.editor, raw)
            if not text:
                continue
            if text.startswith("/"):
                if self.handle_command(text) == "exit":
                    break
                continue
            self._turn(text)

        try:
            self.hooks.run("on_shutdown")
        finally:
            self.auto_save()
        return 0

    def _turn(self, text: str) -> bool:
        callbacks = self._callbacks()
        started = time.monotonic()
        try:
            result = self.agent.run_turn(
                text,
                on_delta=callbacks["on_delta"],
                on_tool=callbacks["on_tool"],
                on_tool_result=callbacks["on_tool_result"],
            )
        except LLMError as exc:
            print(red(f"Model error: {exc}"))
            return False
        except KeyboardInterrupt:
            print(yellow("\n[interrupted]"))
            self._repair_history()
            return False
        if not callbacks["state"]["wrote"]:
            print(result)
        else:
            print()
        self._print_stats(started)
        self._print_usage()
        return True

    def _print_stats(self, started: float) -> None:
        self._last_elapsed = time.monotonic() - started
        if not self.settings.verbose:
            return
        usage = self.agent.total_usage
        print(
            dim(
                f"  [{self._last_elapsed:.1f}s · {self.settings.provider.name}/"
                f"{self.settings.model} · in {usage['prompt_tokens']} tok"
                f" / out {usage['completion_tokens']} tok]"
            )
        )

    def _repair_history(self) -> None:
        """Drop a half-finished tool exchange so the next turn is valid."""
        messages = self.agent.messages
        while messages:
            last = messages[-1]
            if last.get("role") == "assistant" and last.get("tool_calls"):
                messages.pop()
                continue
            break

    # -- command dispatch ---------------------------------------------------

    def handle_command(self, line: str) -> str | None:
        parts = line.split(maxsplit=1)
        entered = parts[0].lower().lstrip("/")
        argument = parts[1].strip() if len(parts) > 1 else ""
        name = ALIASES.get(entered, entered)

        if name not in COMMAND_INDEX:
            print(yellow(f"unknown command: /{entered}"))
            lines = self.palette_lines("/")
            print("\n".join(lines))
            return None

        handlers: dict[str, Callable[[str], None]] = {
            "help": self.cmd_help,
            "new": self.cmd_new,
            "sessions": self.cmd_sessions,
            "switch": self.cmd_switch,
            "save": self.cmd_save,
            "delete": self.cmd_delete,
            "compact": self.cmd_compact,
            "clear": self.cmd_clear,
            "model": self.cmd_model,
            "provider": self.cmd_provider,
            "providers": self.cmd_providers,
            "tools": self.cmd_tools,
            "skills": self.cmd_skills,
            "plugins": self.cmd_plugins,
            "system": self.cmd_system,
            "verbose": self.cmd_verbose,
            "settings": self.cmd_settings,
            "usage": self.cmd_usage,
            "mode": self.cmd_mode,
            "agents": self.cmd_agents,
            "keys": self.cmd_keys,
            "mcp": self.cmd_mcp,
            "version": self.cmd_version,
        }
        if name == "quit":
            return "exit"
        handlers[name](argument)
        return None

    # -- commands: sessions -------------------------------------------------

    def cmd_help(self, _argument: str) -> None:
        print(HELP)

    def cmd_new(self, argument: str) -> None:
        if self.agent.messages:
            self._save_session(quiet=True)
        self.agent.reset()
        self.session_name = slug(argument) if argument else ""
        self.session_created = _now()
        if self.session_name and self.sessions.exists(self.session_name):
            print(yellow(f"note: '{self.session_name}' exists and will be overwritten on save"))
        label = self.session_name or "(unnamed, saved on exit)"
        print(green(f"new session: {label}"))

    def cmd_sessions(self, _argument: str) -> None:
        listing = self._refresh_listing()
        if not listing:
            print(dim(f"(no sessions yet in {self.settings.sessions_dir})"))
            return
        for index, info in enumerate(listing, 1):
            marker = cyan("*") if info.name == self.session_name else " "
            meta = f"{info.message_count:>3} msgs · {info.updated} · {info.provider or '?'}/{info.model or '?'}"
            print(f" {marker}{index:>2}) {bold(info.name):<24} {dim(meta)}")
            if info.preview:
                print(f"       {dim(info.preview)}")
        print(dim("switch with /switch <number|name>"))

    def cmd_switch(self, argument: str) -> None:
        if not argument:
            print(yellow("usage: /switch <number|name>   (see /sessions)"))
            return
        # Resolve before saving: saving reorders sessions by modified time.
        name = self._resolve_session(argument)
        if name is None:
            return
        if name == self.session_name:
            print(yellow(f"already in '{name}'"))
            return
        if self.agent.messages:
            self._save_session(quiet=True)
        try:
            payload = self.sessions.load(name)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(red(f"cannot read '{name}': {exc}"))
            return

        provider = str(payload.get("provider") or "")
        model = str(payload.get("model") or "")
        if provider and (provider != self.settings.provider.name or model != self.settings.model):
            try:
                self._switch_provider_settings(provider, model)
                self._rebuild_agent()
            except ConfigError as exc:
                print(yellow(f"staying on {self.settings.provider.name}: {exc}"))

        system_prompt = payload.get("system_prompt")
        if isinstance(system_prompt, str) and system_prompt.strip():
            self.agent.system_prompt = system_prompt
        self.agent.summary = ""
        summary = payload.get("summary")
        if isinstance(summary, str) and summary.strip():
            self.agent.summary = summary
            if SUMMARY_HEADING not in self.agent.system_prompt:
                self.agent.system_prompt = (
                    self.agent.system_prompt.rstrip() + "\n\n" + summary_section(summary)
                ).strip()
        messages = payload.get("messages") or []
        self.agent.load_messages(messages)
        self.agent.load_usage_state(payload.get("usage"))
        self.session_name = name
        self.session_created = str(payload.get("created") or _now())
        print(
            green(f"switched to '{name}'")
            + dim(f" · {len(messages)} messages · {provider or '?'}/{model or '?'}")
        )

    def cmd_save(self, argument: str) -> None:
        self._save_session(argument or None)

    def cmd_delete(self, argument: str) -> None:
        if not argument:
            print(yellow("usage: /delete <number|name>"))
            return
        name = self._resolve_session(argument)
        if name is None:
            return
        if self.sessions.delete(name):
            if name == self.session_name:
                self.session_name = ""
                print(
                    green(f"deleted '{name}'")
                    + dim(" · current conversation kept in memory, will save under a new name")
                )
            else:
                print(green(f"deleted '{name}'"))
            self._refresh_listing()
        else:
            print(red(f"could not delete '{name}'"))

    def cmd_compact(self, argument: str) -> None:
        argument = argument.strip().lower()
        if argument in ("show", "summary"):
            if not self.agent.summary.strip():
                print(dim("no summary yet · /compact to create one"))
                return
            print(bold("Compacted summary") + dim(f" · {len(self.agent.summary)} chars"))
            print(self.agent.summary)
            return
        keep = None
        if argument:
            try:
                keep = int(argument)
                if keep < 1:
                    raise ValueError
            except ValueError:
                print(yellow("usage: /compact [turns | show]"))
                return
        print(dim("compacting conversation…"))
        result = self.agent.compact(keep)
        if result.method == "noop":
            needed = keep if keep is not None else self.settings.compact_keep_turns
            print(dim(f"nothing to compact yet (needs more than {needed} user turns)"))
            return
        if result.error:
            print(yellow(f"summariser unavailable ({result.error}) · used a local digest"))
        print(
            green(f"compacted {result.before} -> {result.after} messages")
            + dim(f" · summary {result.summary_chars} chars · {result.method}")
        )
        print(dim("summary is in the system prompt · /compact show to read it · /save to persist"))

    def cmd_clear(self, _argument: str) -> None:
        self.agent.reset()
        print(dim("conversation cleared"))

    # -- commands: model ----------------------------------------------------

    def cmd_model(self, argument: str) -> None:
        argument = argument.strip()
        lowered = argument.lower()
        if not argument:
            if interactive():
                self._pick_model()
            else:
                print(f"{self.settings.provider.name}/{self.settings.model}")
                self._print_models()
            return
        if lowered in ("list", "ls", "all"):
            self._print_models()
            return
        if lowered == "info":
            self._print_model_info()
            return
        if lowered in ("pick", "choose", "select", "refresh"):
            if lowered == "refresh":
                self._model_cache.pop(self.settings.provider.name, None)
                self._catalog_data(force=True)
            if interactive():
                self._pick_model()
            else:
                self._print_models(force=lowered == "refresh")
            return
        self.settings.model = argument
        self._rebuild_agent()
        print(green(f"model -> {argument}"))

    def _warm_catalog(self) -> None:
        """Fetch models.dev in the background so cost/context data is ready."""
        catalog, note = load_catalog()
        if not catalog:
            return
        with self._catalog_lock:
            if self._catalog is None:
                self._catalog = catalog
                self._catalog_note = note

    def _catalog_data(self, force: bool = False) -> dict[str, Any]:
        """models.dev catalogue, loaded lazily and cached on disk."""
        if force or self._catalog is None:
            catalog, note = load_catalog(force=force)
            with self._catalog_lock:
                self._catalog = catalog
                self._catalog_note = note
            if note:
                print(dim(f"models.dev: {note}"))
        return self._catalog or {}

    def _usage_catalog(self) -> dict[str, Any]:
        """Catalogue for usage lines: cache only, never a network call."""
        if self._catalog is not None:
            return self._catalog
        cached, _note = load_catalog(allow_network=False)
        return cached or {}

    def _usage_meta(self, provider: str, model: str, allow_network: bool = False):
        catalog = self._catalog_data() if allow_network else self._usage_catalog()
        entries = catalog_models(catalog, provider)
        return catalog_lookup(entries, model)

    def _usage_snapshot(self, allow_network: bool = False) -> dict[str, Any]:
        tokens, exact = self.agent.context_size()
        provider = self.settings.provider.name
        model = self.settings.model
        meta = self._usage_meta(provider, model, allow_network)
        limit = context_limit(meta)
        total_in = int(self.agent.total_usage.get("prompt_tokens") or 0)
        total_out = int(self.agent.total_usage.get("completion_tokens") or 0)
        cost = 0.0
        priced = 0
        unpriced: list[str] = []
        requests = 0
        estimated_calls = 0
        for provider_name, models in self.agent.usage_by_model.items():
            for model_name, counts in models.items():
                requests += counts.get("requests", 0)
                estimated_calls += counts.get("estimated", 0)
                model_cost = cost_for(
                    counts.get("prompt_tokens", 0),
                    counts.get("completion_tokens", 0),
                    self._usage_meta(provider_name, model_name, allow_network),
                )
                if model_cost is None:
                    unpriced.append(f"{provider_name}/{model_name}")
                else:
                    cost += model_cost
                    priced += 1
        return {
            "tokens": tokens,
            "exact": exact,
            "limit": limit,
            "pct": (tokens / limit * 100) if limit else None,
            "in": total_in,
            "out": total_out,
            "cost": cost,
            "priced": priced,
            "unpriced": unpriced,
            "requests": requests,
            "estimated_calls": estimated_calls,
            "provider": provider,
            "model": model,
            "meta": meta,
        }

    def _usage_line(self) -> str:
        if not self.settings.show_usage:
            return ""
        snap = self._usage_snapshot()
        tokens = snap["tokens"]
        parts: list[str] = []
        if snap["limit"]:
            parts.append(
                f"ctx {format_percent(tokens, snap['limit'])}"
                f" {format_tokens(tokens)}/{format_tokens(snap['limit'])}"
            )
        else:
            parts.append(f"ctx {format_tokens(tokens)} tok")
        session_tokens = snap["in"] + snap["out"]
        if session_tokens:
            parts.append(f"session {format_tokens(session_tokens)} tok")
        if snap["cost"] > 0:
            parts.append(f"~{format_cost(snap['cost'])}" + ("+" if snap["unpriced"] else ""))
        if not snap["exact"] or snap["estimated_calls"]:
            parts.append("est.")
        return " · ".join(parts)

    def _print_usage(self) -> None:
        line = self._usage_line()
        if not line:
            return
        print(dim(f"  {line}"))
        snap = self._usage_snapshot()
        if snap["pct"] is not None and snap["pct"] >= 85:
            print(yellow(f"  context {snap['pct']:.0f}% used · /compact to shrink"))

    def cmd_usage(self, _argument: str) -> None:
        snap = self._usage_snapshot(allow_network=True)
        print(bold("Usage") + dim(f"  ·  {snap['provider']}/{snap['model']}"))
        tokens = snap["tokens"]
        if snap["limit"]:
            print(
                f"  context      {format_tokens(tokens)} / {format_tokens(snap['limit'])}"
                f"  ({format_percent(tokens, snap['limit'])})"
            )
        else:
            print(f"  context      {format_tokens(tokens)} tokens (model limit unknown)")
        print(
            f"  last request in {format_tokens(self.agent.last_prompt_tokens)}"
            f" · out {format_tokens(self.agent.last_completion_tokens)}"
        )
        if self.agent.last_usage_estimated:
            print(dim("               (estimated: provider did not report usage)"))
        print(
            f"  session      in {format_tokens(snap['in'])}"
            f" · out {format_tokens(snap['out'])}"
            f" · {snap['requests']} request(s)"
        )
        if snap["priced"]:
            note = " · some models unpriced" if snap["unpriced"] else ""
            print(
                f"  est. cost    {format_cost(snap['cost'])}{note}"
                + dim("  (models.dev pricing, cache discounts ignored)")
            )
        else:
            print(dim("  est. cost    unknown (model not priced in models.dev)"))
        if self.agent.usage_by_model:
            print(dim("  by model"))
            for provider_name, models in self.agent.usage_by_model.items():
                for model_name, counts in models.items():
                    model_meta = self._usage_meta(provider_name, model_name, allow_network=True)
                    model_cost = cost_for(
                        counts.get("prompt_tokens", 0),
                        counts.get("completion_tokens", 0),
                        model_meta,
                    )
                    cost_text = format_cost(model_cost) if model_cost is not None else "-"
                    print(
                        f"    {provider_name}/{model_name}:"
                        f" {counts.get('requests', 0)} req ·"
                        f" {format_tokens(counts.get('prompt_tokens', 0))} in /"
                        f" {format_tokens(counts.get('completion_tokens', 0))} out ·"
                        f" {cost_text}"
                    )
        if snap["pct"] is not None and snap["pct"] >= 85:
            print(yellow(f"  context {snap['pct']:.0f}% used · run /compact to shrink it"))

    def _model_details(self, models: list[str]) -> dict[str, str]:
        entries = catalog_models(self._catalog_data(), self.settings.provider.name)
        if not entries:
            return {}
        details: dict[str, str] = {}
        for model in models:
            meta = catalog_lookup(entries, model)
            if meta:
                text = describe_model(meta)
                if text:
                    details[model] = text
        return details

    def _print_model_info(self) -> None:
        model = self.settings.model
        provider = self.settings.provider.name
        print(bold(model) + dim(f"  ·  {provider}"))
        entries = catalog_models(self._catalog_data(), provider)
        meta = catalog_lookup(entries, model)
        if not meta:
            print(dim(f"not in the models.dev catalogue for '{provider}'"))
            print(dim("/model refresh reloads the catalogue · /model list shows all ids"))
            return
        for label, value in model_info(meta):
            print(f"  {label:<12} {value}")

    def _fetch_models(self, force: bool = False) -> list[str]:
        name = self.settings.provider.name
        if not force and name in self._model_cache:
            return self._model_cache[name]
        try:
            models = fetch_models(self.settings.provider, timeout=min(self.settings.timeout, 20))
        except ModelListError as exc:
            print(red(f"models: {exc}"))
            catalog = sorted(catalog_models(self._catalog_data(), name))
            if catalog:
                print(
                    dim(
                        f"using models.dev catalogue for '{name}' ({len(catalog)} models)"
                        " · ids are unvalidated"
                    )
                )
                self._model_cache[name] = catalog
                return catalog
            return []
        self._model_cache[name] = models
        return models

    def _print_models(self, force: bool = False) -> None:
        models = self._fetch_models(force)
        if not models:
            return
        current = self.settings.model
        print(
            bold(f"Models · {self.settings.provider.name}")
            + dim(f" · {len(models)} available")
        )
        limit = 200
        shown = models[:limit]
        details = self._model_details(shown)
        width = min(52, max((len(name) for name in shown), default=0))
        for name in shown:
            line = ("* " if name == current else "  ") + f"{name:<{width}}"
            suffix = details.get(name)
            if suffix:
                line += "  " + dim(suffix)
            print(line.rstrip())
        if len(models) > limit:
            print(dim(f"   ... {len(models) - limit} more"))
        print(dim("/model opens the picker · /model <id> sets directly"))

    def _pick_model(self) -> None:
        models = self._fetch_models()
        if not models:
            return
        choice = select_model(
            f"Model · {self.settings.provider.name}",
            models,
            current=self.settings.model,
            details=self._model_details(models),
        )
        if not choice:
            return
        if choice == self.settings.model:
            print(dim(f"model unchanged ({choice})"))
            return
        self.settings.model = choice
        self._rebuild_agent()
        print(green(f"model -> {choice}"))

    def _check_provider_key(self) -> list[str]:
        """Validate the active key by listing models; cache the catalogue."""
        if self.settings.provider.requires_key and not self.settings.provider.api_key:
            print(
                dim(
                    f"no API key for '{self.settings.provider.name}' yet"
                    f" · /keys set {self.settings.provider.name} <key>"
                )
            )
            return []
        try:
            models = fetch_models(self.settings.provider, timeout=min(self.settings.timeout, 20))
        except ModelListError as exc:
            print(yellow(f"key check: {exc}"))
            return []
        self._model_cache[self.settings.provider.name] = models
        print(dim(f"key accepted · {len(models)} models · /model to pick"))
        return models

    def cmd_provider(self, argument: str) -> None:
        pieces = argument.split(maxsplit=1)
        if not pieces:
            print(f"{self.settings.provider.name} (model: {self.settings.model})")
            return
        name = pieces[0]
        model = pieces[1].strip() if len(pieces) > 1 else None
        try:
            self._switch_provider_settings(name, model)
        except ConfigError as exc:
            print(red(str(exc)))
            return
        self._rebuild_agent()
        print(green(f"provider -> {self.settings.provider.name}/{self.settings.model}"))
        self._check_provider_key()

    def cmd_providers(self, _argument: str) -> None:
        for name in sorted(self.settings.providers):
            mark = "*" if name == self.settings.provider.name else " "
            print(f" {mark} {name:<12} {dim(self.settings.providers[name].get('base_url', ''))}")

    # -- commands: agent ----------------------------------------------------

    def _prompt(self) -> str:
        return bold(yellow("plan> ")) if self.settings.mode == "plan" else bold("> ")

    def cmd_mode(self, argument: str) -> None:
        value = argument.strip().lower()
        if not value:
            if self.settings.mode == "plan":
                print(f"mode: {bold('plan')}  " + dim("read-only · inspect and plan"))
            else:
                print(f"mode: {bold('build')}  " + dim("full access · edit and execute"))
            return
        if value not in ("plan", "build"):
            print(red("mode must be 'plan' or 'build'"))
            return
        self.settings.mode = value
        self.agent.system_prompt = self.agent.build_system_prompt()
        if value == "plan":
            print(
                yellow("mode -> plan")
                + dim(" · read-only: writes, shell and run_python are blocked")
            )
        else:
            print(green("mode -> build") + dim(" · full access restored"))

    def cmd_agents(self, argument: str) -> None:
        action = argument.strip().lower()
        if action in ("reload", "refresh", "r"):
            self.agent.system_prompt = self.agent.build_system_prompt()
            print(
                green(f"project instructions reloaded · {len(self.agent.instructions)} file(s)")
                + dim(f" · workspace {self.settings.workspace}")
            )
            return
        if not self.agent.instructions:
            print(dim(f"no AGENTS.md found from {self.settings.workspace} upward"))
            print(dim("place AGENTS.md in the directory you work in (or a parent) to add project rules"))
            return
        print(bold("Project instructions") + dim(f"  ·  workspace {self.settings.workspace}"))
        for item in self.agent.instructions:
            print(f" · {item.path} {dim(f'({len(item.text)} chars)')}")
        print(dim("/agents reload re-reads them · now in the system prompt"))

    def cmd_tools(self, _argument: str) -> None:
        for tool in self.registry.describe():
            flag = "!" if tool["dangerous"] == "yes" else " "
            ro = "r" if tool["read_only"] == "yes" else " "
            print(f" {flag}{ro} {tool['name']:<16} {dim(tool['description'])}")
        print(dim("  ! = asks for confirmation · r = allowed in plan mode"))

    def cmd_skills(self, _argument: str) -> None:
        if not self.skills.skills:
            print(dim(f"(no skills in {self.settings.skills_dir})"))
            return
        for skill in self.skills.to_list():
            print(f" · {bold(skill['name'])}: {dim(skill['description'])}")

    def cmd_plugins(self, _argument: str) -> None:
        if not self.plugins:
            print(dim(f"(no plugins in {self.settings.plugins_dir})"))
            return
        for plugin in self.plugins:
            tools = f" -> {', '.join(plugin.tools)}" if plugin.tools else ""
            print(f" · {bold(plugin.name)}{tools} {dim(plugin.description)}")

    def cmd_system(self, argument: str) -> None:
        if argument:
            self.agent.system_prompt = argument
            print(green("system prompt replaced"))
        else:
            print(self.agent.system_prompt)

    def cmd_verbose(self, _argument: str) -> None:
        self.settings.verbose = not self.settings.verbose
        print(dim(f"verbose {'on' if self.settings.verbose else 'off'}"))

    def cmd_version(self, _argument: str) -> None:
        print(VERSION)

    # -- commands: settings -------------------------------------------------

    def cmd_settings(self, argument: str) -> None:
        argument = argument.strip()
        lowered = argument.lower()
        if not argument or lowered in ("list", "show", "all"):
            self._list_settings()
            return
        if lowered == "save":
            self._save_config()
            return
        if lowered == "path":
            print(self.settings.config_path)
            return
        if lowered == "reload":
            self._reload_settings()
            return
        if lowered.startswith("reset"):
            _, _, rest = argument.partition(" ")
            self._reset_settings(rest.strip())
            return

        key, _, raw = argument.partition(" ")
        setting = SETTINGS_BY_KEY.get(resolve_key(key))
        if setting is None:
            print(red(f"unknown setting: {key}"))
            print(dim("available: " + ", ".join(item.key for item in SETTINGS)))
            return
        if not raw.strip():
            self._show_setting(setting)
            return
        self._change_setting(setting, raw.strip())

    def _list_settings(self) -> None:
        print(bold("Settings") + dim(f"  ·  config: {self.settings.config_path}"))
        width = max(len(item.key) for item in SETTINGS)
        for setting in SETTINGS:
            value = display_value(self.settings, setting.key)
            value = _short_path(value) if setting.kind == "path" else truncate(value, 44)
            print(f"  {setting.key:<{width}}  {value:<44} {dim(setting.description)}")
        print()
        print(
            dim(
                "/settings <key> <value> to change · /settings save to write config.json"
                " · /settings reset all to reload"
            )
        )

    def _show_setting(self, setting) -> None:
        print(f"{bold(setting.key)} = {display_value(self.settings, setting.key)}")
        print(dim(setting.description))
        print(dim(f"accepts: {hint(setting)}"))
        if setting.key == "provider":
            print(dim("providers: " + ", ".join(sorted(self.settings.providers))))
        print(dim(f"change with: /settings {setting.key} <value>"))

    def _change_setting(self, setting, raw: str, from_file: bool = False) -> None:
        try:
            value = coerce(setting, raw)
        except ValueError as exc:
            print(red(f"{setting.key}: {exc}"))
            print(dim(f"accepts: {hint(setting)}"))
            return
        try:
            note = self._apply_setting(setting.key, value)
        except (ConfigError, OSError) as exc:
            print(red(f"{setting.key}: {exc}"))
            return
        line = green(f"{setting.key} = {display_value(self.settings, setting.key)}")
        if note:
            line += dim(f" · {note}")
        print(line)
        print(dim("(from config.json)" if from_file else "not persisted · /settings save"))

    def _apply_setting(self, key: str, value) -> str | None:
        if key == "provider":
            adopted = self.settings.with_provider(str(value))
            self.settings.provider = adopted.provider
            self.settings.model = adopted.model
            self._rebuild_agent()
            return f"model -> {self.settings.model}"
        if key == "model":
            self.settings.model = str(value)
            self._rebuild_agent()
            return None
        if key == "mode":
            self.settings.mode = str(value)
            self.agent.system_prompt = self.agent.build_system_prompt()
            return "read-only" if value == "plan" else "full access"
        if key == "system_prompt":
            self.settings.system_prompt = str(value)
            self.agent.system_prompt = self.agent.build_system_prompt()
            return "system prompt rebuilt"
        if key == "skills_dir":
            self.settings.skills_dir = Path(value)
            self._reload_capabilities()
            return None
        if key == "plugins_dir":
            self.settings.plugins_dir = Path(value)
            self._reload_capabilities()
            return None
        if key == "sessions_dir":
            self.settings.sessions_dir = Path(value)
            self.sessions = SessionStore(self.settings.sessions_dir)
            self.session_name = ""
            self.session_created = _now()
            self._refresh_listing()
            return "session store switched"
        if key == "workspace":
            self.settings.workspace = Path(value)
            self.agent.system_prompt = self.agent.build_system_prompt()
            return f"tools resolve here · {len(self.agent.instructions)} AGENTS.md file(s)"
        if key == "env_file":
            self.settings.env_file = Path(value)
            return "API keys now read/written here"
        if key in ("mcp_config", "mcp_enabled", "mcp_timeout"):
            setattr(self.settings, key, value)
            self._reload_capabilities()
            return "MCP servers reloaded"
        setattr(self.settings, key, value)
        return None

    def _reload_capabilities(self) -> None:
        """Rebuild tools, skills, plugins and MCP servers."""
        self._stop_mcp()
        self.registry = Registry()
        self.hooks = Hooks()
        self.skills = SkillLoader(self.settings.skills_dir)
        register_essentials(self.registry, self.settings, skill_reader=self.skills.read)
        self.plugins, self.plugin_errors = load_plugins(
            self.settings.plugins_dir, self.registry, self.hooks, self.settings,
            log=lambda msg: print(dim(msg)),
        )
        for error in self.plugin_errors:
            print(yellow(f"plugin error: {error}"))
        self._connect_mcp()
        self._rebuild_agent()
        self.hooks.run("on_start", agent=self.agent)
        print(
            dim(
                f"reloaded · tools: {len(self.registry.tools)}"
                f" · skills: {len(self.skills.skills)} · plugins: {len(self.plugins)}"
                f" · mcp: {len(self.mcp_clients)}"
            )
        )

    def _save_config(self) -> None:
        path = self.settings.config_path
        try:
            data = load_config_file(path)
        except ConfigError as exc:
            print(red(str(exc)))
            return
        data.update(
            {
                "provider": self.settings.provider.name,
                "model": self.settings.model,
                "temperature": self.settings.temperature,
                "max_tokens": self.settings.max_tokens,
                "max_steps": self.settings.max_steps,
                "compact_keep_turns": self.settings.compact_keep_turns,
                "stream": self.settings.stream,
                "timeout": self.settings.timeout,
                "confirm_shell": self.settings.confirm_shell,
                "auto_save": self.settings.auto_save,
                "show_tool_results": self.settings.show_tool_results,
                "show_usage": self.settings.show_usage,
                "verbose": self.settings.verbose,
                "mode": self.settings.mode,
                "shell": self.settings.shell,
                "workspace": _config_path(self.settings.workspace),
                "skills_dir": _config_path(self.settings.skills_dir),
                "plugins_dir": _config_path(self.settings.plugins_dir),
                "sessions_dir": _config_path(self.settings.sessions_dir),
                "env_file": _config_path(self.settings.env_file),
                "mcp_config": _config_path(self.settings.mcp_config),
                "mcp_enabled": self.settings.mcp_enabled,
                "mcp_timeout": self.settings.mcp_timeout,
                "system_prompt": self.settings.system_prompt,
            }
        )
        try:
            save_config_file(path, data)
        except OSError as exc:
            print(red(f"cannot write {path}: {exc}"))
            return
        print(green(f"saved settings to {path}"))

    def _reload_settings(self) -> None:
        try:
            self.settings = load_settings(
                config_path=self.settings.config_path, allow_missing_key=True
            )
        except ConfigError as exc:
            print(red(f"cannot reload settings: {exc}"))
            return
        self.sessions = SessionStore(self.settings.sessions_dir)
        self.session_name = ""
        self._reload_capabilities()
        print(green(f"settings reloaded · {self.settings.provider.name}/{self.settings.model}"))

    def _reset_settings(self, reference: str) -> None:
        if not reference or reference.lower() == "all":
            self._reload_settings()
            return
        setting = SETTINGS_BY_KEY.get(resolve_key(reference))
        if setting is None:
            print(red(f"unknown setting: {reference}"))
            return
        if setting.key in ("provider", "model"):
            self._reload_settings()
            return
        try:
            data = load_config_file(self.settings.config_path)
        except ConfigError as exc:
            print(red(str(exc)))
            return
        raw = data.get(setting.key, SETTING_DEFAULTS.get(setting.key))
        if raw is None:
            print(yellow(f"no default recorded for {setting.key}; set it explicitly"))
            return
        if isinstance(raw, bool):
            raw = "on" if raw else "off"
        self._change_setting(setting, str(raw), from_file=True)

    # -- commands: API keys -------------------------------------------------

    def cmd_keys(self, argument: str) -> None:
        parts = argument.split(maxsplit=2)
        action = parts[0].lower() if parts and parts[0].strip() else "list"
        if action in ("list", "show"):
            self._list_keys()
            return
        if action == "path":
            print(self.settings.env_file)
            return
        if action == "set":
            if len(parts) < 3 or not parts[2].strip():
                print(yellow("usage: /keys set <provider> <key>"))
                return
            self._set_key(parts[1], parts[2].strip())
            return
        if action in ("check", "verify", "validate"):
            self._check_keys(parts[1] if len(parts) > 1 else "all")
            return
        if action in ("clear", "remove", "delete", "unset"):
            if len(parts) < 2 or not parts[1].strip():
                print(yellow("usage: /keys clear <provider|all>"))
                return
            self._clear_key(parts[1])
            return
        print(
            yellow(
                "usage: /keys [list | set <provider> <key> | clear <provider|all>"
                " | check [provider|all] | path]"
            )
        )

    def _provider_spec(self, name: str):
        key = name.strip().lower()
        spec = self.settings.providers.get(key)
        if not isinstance(spec, dict):
            print(red(f"unknown provider: {name}"))
            print(dim("known: " + ", ".join(sorted(self.settings.providers))))
            return None
        envs = spec.get("api_key_env") or []
        if isinstance(envs, str):
            envs = [envs]
        return key, spec, [str(env) for env in envs if env]

    def _list_keys(self) -> None:
        file_keys = parse_env(self.settings.env_file)
        print(bold("API keys") + dim(f"  ·  {self.settings.env_file}"))
        width = max(len(name) for name in self.settings.providers)
        for name in sorted(self.settings.providers):
            spec = self.settings.providers[name]
            envs = spec.get("api_key_env") or []
            if isinstance(envs, str):
                envs = [envs]
            envs = [str(env) for env in envs]
            value = source = ""
            for env in envs:
                if os.environ.get(env):
                    value = os.environ[env]
                    source = ".env" if file_keys.get(env) == value else "environment"
                    break
            if value:
                print(
                    green("  set   ")
                    + f"{name:<{width}}  {mask(value):<20} "
                    + dim(f"({source}) {envs[0] if envs else ''}")
                )
            elif spec.get("requires_key", True) is False:
                print(dim(f"  local {name:<{width}}  no key needed"))
            else:
                print(yellow("  unset ") + f"{name:<{width}}  " + dim(", ".join(envs)))
        print(dim("/keys set <provider> <key> · /keys clear <provider|all> · /keys path"))

    def _set_key(self, provider: str, value: str) -> None:
        found = self._provider_spec(provider)
        if found is None:
            return
        name, _spec, envs = found
        if not envs:
            print(red(f"provider '{name}' has no api_key_env configured"))
            return
        env = envs[0]
        try:
            set_env_var(self.settings.env_file, env, value)
        except OSError as exc:
            print(red(f"cannot write {self.settings.env_file}: {exc}"))
            return
        print(
            green(f"{name}: {env} set")
            + dim(f" · {mask(value)} · {self.settings.env_file}")
        )
        self._refresh_active_provider()
        if name == self.settings.provider.name:
            self._check_provider_key()
        else:
            print(dim(f"/provider {name} then /model to pick from its catalogue"))

    def _check_keys(self, target: str) -> None:
        target = target.strip().lower()
        everything = target in ("all", "*", "", "--all")
        names = sorted(self.settings.providers) if everything else [target]
        checked = 0
        for name in names:
            spec = self.settings.providers.get(name)
            if not isinstance(spec, dict):
                print(red(f"unknown provider: {name}"))
                continue
            envs = spec.get("api_key_env") or []
            if isinstance(envs, str):
                envs = [envs]
            if not any(os.environ.get(str(env)) for env in envs):
                if not everything:
                    print(yellow(f"{name}: no key set · /keys set {name} <key>"))
                continue
            try:
                provider = build_provider(name, self.settings.providers)
            except ConfigError as exc:
                print(red(str(exc)))
                continue
            try:
                models = fetch_models(provider, timeout=min(self.settings.timeout, 15))
            except ModelListError as exc:
                checked += 1
                print(red(f"  ✗ {name}: {exc}"))
                continue
            checked += 1
            self._model_cache[name] = models
            print(green(f"  ✓ {name}: {len(models)} models"))
        if not checked:
            print(dim("no keys to check · /keys set <provider> <key>"))

    def _clear_key(self, target: str) -> None:
        target = target.strip()
        if target.lower() in ("all", "*", "--all"):
            names = sorted(self.settings.providers)
        else:
            found = self._provider_spec(target)
            if found is None:
                return
            names = [found[0]]

        cleared = 0
        active_cleared = False
        for name in names:
            spec = self.settings.providers.get(name) or {}
            envs = spec.get("api_key_env") or []
            if isinstance(envs, str):
                envs = [envs]
            for env in envs:
                if env and remove_env_var(self.settings.env_file, str(env)):
                    cleared += 1
            self._strip_config_key(name)
            if name == self.settings.provider.name:
                active_cleared = True

        if cleared:
            print(green(f"cleared {cleared} key variable(s)") + dim(f" · {self.settings.env_file} + this session"))
        else:
            print(yellow("no keys were set for " + (target if target.lower() not in ("all", "*", "--all") else "any provider")))
        if active_cleared:
            self.settings.provider.api_key = ""
            self._rebuild_agent()
            print(
                yellow(
                    f"'{self.settings.provider.name}' has no key now · "
                    "/keys set <provider> <key> or /provider <name>"
                )
            )

    def _strip_config_key(self, provider: str) -> bool:
        path = self.settings.config_path
        try:
            data = load_config_file(path)
        except ConfigError as exc:
            print(red(str(exc)))
            return False
        providers = data.get("providers")
        if not isinstance(providers, dict):
            return False
        entry = providers.get(provider)
        if not isinstance(entry, dict) or "api_key" not in entry:
            return False
        entry.pop("api_key", None)
        try:
            save_config_file(path, data)
        except OSError as exc:
            print(red(f"cannot update {path}: {exc}"))
            return False
        print(dim(f"removed api_key override for '{provider}' from {path}"))
        return True

    def _refresh_active_provider(self) -> None:
        """Re-read the active provider's key after /keys changes."""
        self._model_cache.pop(self.settings.provider.name, None)
        try:
            self._switch_provider_settings(self.settings.provider.name, self.settings.model)
        except ConfigError:
            self.settings.provider.api_key = ""
        self._rebuild_agent()

    # -- commands: MCP ------------------------------------------------------

    def cmd_mcp(self, argument: str) -> None:
        action = argument.strip().lower()
        if action == "path":
            print(self.settings.mcp_config)
            return
        if action == "tools":
            names = [name for name in self.registry.names() if name.startswith("mcp__")]
            if not names:
                print(dim("no MCP tools registered"))
                return
            for name in names:
                tool = self.registry.get(name)
                description = (tool.description if tool else "").splitlines()[0]
                print(f" · {bold(name)}: {dim(description)}")
            return
        if action in ("reload", "restart", "reconnect"):
            self._reload_capabilities()
            return

        print(
            bold("MCP servers")
            + dim(f"  ·  {self.settings.mcp_config}")
            + ("" if self.settings.mcp_enabled else yellow("  (disabled)"))
        )
        clients = {client.config.name: client for client in self.mcp_clients}
        configs, errors = load_mcp_config(self.settings.mcp_config)
        if not configs and not clients:
            print(dim(f"none running · add servers to {self.settings.mcp_config} (see README)"))
        for name in sorted(set(configs) | set(clients)):
            client = clients.get(name)
            config = configs.get(name) or (client.config if client else None)
            tools = len(client.tools) if client else 0
            status = green("running") if client else red("not running")
            command = config.command if config else "?"
            print(f"  {name:<18} {status}  {dim(f'{tools} tools · {command}')}")
        for message in dict.fromkeys(errors + self.mcp_errors):
            print(yellow(f"  ! {message}"))
        print(dim("/mcp reload restarts them · /mcp tools lists tools · /mcp path"))

    def _mcp_log(self, message: str) -> None:
        print(dim(f"mcp: {message}"))

    def _connect_mcp(self) -> None:
        self._stop_mcp()
        self.mcp_errors = []
        if not self.settings.mcp_enabled:
            return
        configs, errors = load_mcp_config(self.settings.mcp_config)
        self.mcp_errors.extend(errors)
        if configs:
            clients, failures = connect_servers(
                configs,
                self.registry,
                log=self._mcp_log,
                timeout=float(self.settings.mcp_timeout),
            )
            self.mcp_clients = clients
            self.mcp_errors.extend(failures)
        for message in self.mcp_errors:
            print(yellow(f"mcp: {message}"))

    def _stop_mcp(self) -> None:
        clients = getattr(self, "mcp_clients", None)
        if not clients:
            return
        stop_clients(clients)
        self.mcp_clients = []

    # -- internals ----------------------------------------------------------

    def _switch_provider_settings(self, name: str, model: str | None = None) -> None:
        """Swap provider/model in place so tools keep reading the same Settings."""
        updated = self.settings.with_provider(name, model)
        self.settings.provider = updated.provider
        self.settings.model = updated.model

    def _rebuild_agent(self) -> None:
        """Recreate the agent (new provider/model) while keeping the transcript."""
        history = list(self.agent.messages)
        usage = self.agent.usage_state()
        self.client = Client(self.settings)
        self.agent = Agent(
            self.settings, self.client, self.registry, self.skills, self.hooks,
            confirm=self.confirm_tool,
        )
        self.agent.load_messages(history)
        self.agent.load_usage_state(usage)

    # -- session persistence ------------------------------------------------

    def _refresh_listing(self) -> list[SessionInfo]:
        self._listing = self.sessions.list()
        return self._listing

    def _resolve_session(self, reference: str) -> str | None:
        reference = reference.strip()
        if not reference:
            return None
        if reference.isdigit():
            listing = self._refresh_listing()
            index = int(reference)
            if 1 <= index <= len(listing):
                return listing[index - 1].name
            span = f"1-{len(listing)}" if listing else "none saved"
            print(red(f"no session #{index} ({span}) · see /sessions"))
            return None
        name = slug(reference)
        if not self.sessions.exists(name):
            print(red(f"no such session: {reference}"))
            print(dim("see /sessions"))
            return None
        return name

    def _session_payload(self) -> dict[str, Any]:
        return {
            "created": self.session_created,
            "provider": self.settings.provider.name,
            "model": self.settings.model,
            "system_prompt": self.agent.system_prompt,
            "summary": self.agent.summary,
            "usage": self.agent.usage_state(),
            "messages": self.agent.messages,
        }

    def _save_session(self, name: str | None = None, quiet: bool = False) -> Path | None:
        if not self.agent.messages:
            if not quiet:
                print(yellow("nothing to save yet"))
            return None

        if name:
            target = slug(name)
            if target != self.session_name:
                self.session_created = _now()
        else:
            target = self.session_name or f"session-{time.strftime('%Y%m%d-%H%M%S')}"

        self.session_name = target
        path = self.sessions.save(target, self._session_payload())
        if not quiet:
            print(green(f"saved '{target}'") + dim(f" · {len(self.agent.messages)} messages · {path}"))
        return path

    def auto_save(self) -> None:
        if not self.settings.auto_save or not self.agent.messages:
            return
        path = self._save_session(quiet=True)
        if path:
            print(dim(f"session saved: {path}"))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _read_multiline(reader: LineEditor, first: str) -> str:
    """Let the user continue a message with a trailing backslash."""
    lines = [first]
    while lines[-1].rstrip().endswith("\\"):
        lines[-1] = lines[-1].rstrip()[:-1]
        try:
            lines.append(reader.read(dim("... ")))
        except (EOFError, KeyboardInterrupt):
            print()
            break
    return "\n".join(lines).strip()


def _compact_menu(commands: tuple[Command, ...] | list[Command]) -> list[str]:
    """Column-major menu of command names, so `/` stays a short list.

    Argument hints and descriptions appear once you narrow the list with a
    prefix (e.g. `/sw`), or via /help.
    """
    cells = [f"/{command.name}" for command in commands]
    cell_width = max(len(cell) for cell in cells) + 4
    columns = max(1, min(3, 78 // cell_width))
    rows = (len(cells) + columns - 1) // columns
    lines: list[str] = []
    for row in range(rows):
        parts: list[str] = []
        for column in range(columns):
            index = row + column * rows
            if index >= len(cells):
                break
            parts.append(cells[index].ljust(cell_width))
        lines.append("  " + "".join(parts).rstrip())
    lines.append(dim("  Tab completes · Enter runs · /help for details"))
    return lines


def _short_path(text: str, limit: int = 42) -> str:
    return text if len(text) <= limit else "…" + text[-(limit - 1) :]


def _config_path(path: Path) -> str:
    """Store paths relative to the project when possible, absolute otherwise."""
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def _summarise_arguments(arguments: dict[str, Any]) -> str:
    for key in ("path", "command", "url", "pattern", "name", "query"):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            return truncate(value.replace("\n", " "), 120)
    if arguments:
        return truncate(json.dumps(arguments, ensure_ascii=False, default=str), 120)
    return ""
