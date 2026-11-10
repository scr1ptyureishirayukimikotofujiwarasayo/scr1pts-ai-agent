"""The agent loop: system prompt -> user turn -> tool calls -> final answer."""

from __future__ import annotations

import platform
from pathlib import Path
from typing import Any, Callable

from .agentsmd import Instructions, discover, prompt_block
from .compact import (
    CompactionResult,
    digest,
    split_index,
    summarize,
    summary_section,
    transcript_text,
)
from .config import Settings
from .llm import Client, Completion, LLMError
from .plugins import Hooks
from .skills import SkillLoader
from .tools import Registry
from .usage import estimate_messages_tokens, estimate_specs_tokens, estimate_tokens

DEFAULT_SYSTEM_PROMPT = """\
You are a coding and research agent running in the user's terminal.

You can read and write files, search code, run Python snippets (the default
execution path) or the project's own shell tooling, and fetch URLs through
tools. Prefer acting over asking: inspect the project, make the change, then
verify it (run the code, the tests, or the linter).

Rules of engagement:
- Read a file before editing it, and use exact snippets with `edit_file`.
- Paths are relative to the workspace unless absolute.
- Keep tool output small: grep/search instead of dumping whole trees.
- Never fabricate file contents, command output, or API responses.
- Say what you did, then what remains. No filler.
"""

PYTHON_FIRST = """\
## Tooling: Python first

Prefer Python for shell work and scripted tasks — it is the most reliable path:
- `run_python` for computation, parsing, file/JSON/CSV processing, HTTP, quick
  checks, and anything you would otherwise do with sed/awk/find one-liners.
- `run_command` only for the project's own tooling (git, make, npm/pnpm, uv,
  docker, its test runner).
- Do not rely on bash-only syntax: the shell is cmd.exe on Windows and sh
  elsewhere. Python behaves identically on both.
- When a task needs several steps or branching, write one Python script instead
  of chaining shell commands.
- Never call `run_command` with `python -c ...` or a heredoc: call `run_python`
  directly, so quoting and platform differences cannot break it.
- Use pathlib, quote paths, and print explicit output so results are verifiable.
"""

MODE_PROMPTS = {
    "plan": """\
## Mode: PLAN (read-only)

You are in read-only planning mode. Mutating and executing tools are not available.
- Investigate with read-only tools only: read_file, list_dir, search_files, grep,
  read_agents_md, use_skill, todo_write, GET/HEAD http_request, and MCP tools
  annotated read-only.
- Do not claim you changed or ran anything; you cannot.
- Deliver a precise plan: goal, files to change (real paths), ordered steps,
  risks/edge cases, verification commands, open questions.
- End by asking the user to approve and switch with /mode build.""",
    "build": """\
## Mode: BUILD

You have full access. Implement the change end to end: read before editing, make
focused edits, verify with the project's own commands, then report what changed
and what remains.""",
}

_OS = f"{platform.system()} {platform.release()}"


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class Agent:
    def __init__(
        self,
        settings: Settings,
        client: Client,
        registry: Registry,
        skills: SkillLoader,
        hooks: Hooks,
        log: Callable[[str], None] = print,
        confirm: Callable[[str, dict[str, Any]], bool] | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.registry = registry
        self.skills = skills
        self.hooks = hooks
        self.log = log
        self.confirm = confirm
        self.messages: list[dict[str, Any]] = []
        self.instructions: list[Instructions] = []
        self.summary: str = ""
        self.system_prompt = self.build_system_prompt()
        self.total_usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
        self.usage_by_model: dict[str, dict[str, dict[str, int]]] = {}
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_usage_estimated = False

    # -- prompt -------------------------------------------------------------

    def build_system_prompt(self) -> str:
        """Rebuild the prompt, re-reading AGENTS.md from the current workspace."""
        parts: list[str] = [self.settings.system_prompt.strip() or DEFAULT_SYSTEM_PROMPT]
        parts.append(PYTHON_FIRST)
        parts.append(MODE_PROMPTS.get(self.settings.mode, MODE_PROMPTS["build"]))
        self.instructions = discover(Path(self.settings.workspace))
        project = prompt_block(self.instructions)
        if project:
            parts.append(project)
        compacted = summary_section(self.summary)
        if compacted:
            parts.append(compacted)
        parts.append(
            "## Environment\n"
            f"- workspace: {Path(self.settings.workspace)}\n"
            f"- platform: {_OS}\n"
            f"- python: {platform.python_version()}\n"
            f"- model: {self.settings.provider.name}/{self.settings.model}"
        )
        index = self.skills.prompt_index()
        if index:
            parts.append(index)
        return "\n\n".join(part for part in parts if part).strip()

    # -- conversation -------------------------------------------------------

    def reset(self) -> None:
        self.messages.clear()
        self.summary = ""
        self.reset_usage()
        self.system_prompt = self.build_system_prompt()

    def load_messages(self, messages: list[dict[str, Any]]) -> None:
        self.messages = list(messages)
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0

    # -- usage accounting ---------------------------------------------------

    def reset_usage(self) -> None:
        self.total_usage = {"prompt_tokens": 0, "completion_tokens": 0}
        self.usage_by_model = {}
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_usage_estimated = False

    def usage_state(self) -> dict[str, Any]:
        return {
            "total": dict(self.total_usage),
            "by_model": self.usage_by_model,
            "last_prompt_tokens": self.last_prompt_tokens,
            "last_completion_tokens": self.last_completion_tokens,
            "last_usage_estimated": self.last_usage_estimated,
        }

    def load_usage_state(self, state: dict[str, Any] | None) -> None:
        if not isinstance(state, dict):
            return
        total = state.get("total")
        if total is None and ("prompt_tokens" in state or "completion_tokens" in state):
            total = state  # legacy session format stored totals directly
        total = total or {}
        for key in self.total_usage:
            try:
                self.total_usage[key] = int(total.get(key) or 0)
            except (TypeError, ValueError):
                self.total_usage[key] = 0
        by_model = state.get("by_model")
        if isinstance(by_model, dict):
            self.usage_by_model = {
                str(provider): {
                    str(model): {
                        "requests": int(counts.get("requests") or 0),
                        "prompt_tokens": int(counts.get("prompt_tokens") or 0),
                        "completion_tokens": int(counts.get("completion_tokens") or 0),
                        "estimated": int(counts.get("estimated") or 0),
                    }
                    for model, counts in models.items()
                    if isinstance(counts, dict)
                }
                for provider, models in by_model.items()
                if isinstance(models, dict)
            }
        try:
            self.last_prompt_tokens = int(state.get("last_prompt_tokens") or 0)
            self.last_completion_tokens = int(state.get("last_completion_tokens") or 0)
        except (TypeError, ValueError):
            self.last_prompt_tokens = 0
            self.last_completion_tokens = 0
        self.last_usage_estimated = bool(state.get("last_usage_estimated"))

    def context_size(self) -> tuple[int, bool]:
        """Approximate tokens currently in context. (tokens, exact)."""
        if self.last_prompt_tokens:
            return self.last_prompt_tokens + self.last_completion_tokens, not self.last_usage_estimated
        only = self.registry.read_only_names() if self.settings.mode == "plan" else None
        tokens = estimate_messages_tokens(self._request_messages())
        tokens += estimate_specs_tokens(self.registry.specs(only=only))
        return tokens, False

    def compact(self, keep_turns: int | None = None) -> CompactionResult:
        """Fold older turns into the summary and keep recent turns verbatim."""
        total = len(self.messages)
        keep = max(
            1,
            int(keep_turns if keep_turns is not None else self.settings.compact_keep_turns),
        )
        cutoff = split_index(self.messages, keep)
        if cutoff <= 0:
            return CompactionResult(total, total, len(self.summary), "noop")

        older = self.messages[:cutoff]
        method = "model"
        error = ""
        try:
            summary = summarize(self.client, transcript_text(older), self.summary)
        except LLMError as exc:
            summary = digest(older)
            method = "digest"
            error = str(exc)

        if not summary.strip():
            summary = digest(older)
            method = "digest"

        if method == "digest" and self.summary.strip():
            self.summary = f"{self.summary.strip()}\n{summary}".strip()
        else:
            self.summary = summary.strip()

        if len(self.summary) > 12_000:
            self.summary = self.summary[:6000] + "\n... [summary truncated] ...\n" + self.summary[-6000:]

        self.messages = self.messages[cutoff:]
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.system_prompt = self.build_system_prompt()
        return CompactionResult(total, len(self.messages), len(self.summary), method, error)

    def run_turn(
        self,
        user_text: str,
        on_delta: Callable[[str], None] | None = None,
        on_tool: Callable[[str, dict[str, Any]], None] | None = None,
        on_tool_result: Callable[[str, str, bool], None] | None = None,
    ) -> str:
        """Run one user turn to completion. Returns the final assistant text."""
        replacement = self.hooks.first_result("on_user_message", text=user_text)
        if isinstance(replacement, str) and replacement.strip():
            user_text = replacement
        self.messages.append({"role": "user", "content": user_text})

        final_text = ""
        for step in range(1, max(1, self.settings.max_steps) + 1):
            completion = self._complete(on_delta)

            if not completion.tool_calls:
                final_text = completion.content
                self.messages.append({"role": "assistant", "content": final_text})
                self.hooks.run("on_assistant_message", text=final_text)
                self.hooks.run("on_turn_end", text=final_text)
                return final_text

            self.messages.append(completion.message)
            for call in completion.tool_calls:
                self._run_tool(call, step, on_tool, on_tool_result)

        text = (
            f"Stopped after {self.settings.max_steps} model steps without a final answer. "
            "Increase max_steps in config.json or split the task."
        )
        self.messages.append({"role": "assistant", "content": text})
        return text

    def _complete(self, on_delta: Callable[[str], None] | None) -> Completion:
        try:
            only = self.registry.read_only_names() if self.settings.mode == "plan" else None
            completion = self.client.chat(
                self._request_messages(),
                tools=self.registry.specs(only=only),
                on_delta=on_delta,
            )
        except LLMError:
            # Drop the dangling user message so the session stays usable.
            if self.messages and self.messages[-1]["role"] == "user":
                self.messages.pop()
            raise
        self._record_usage(completion)
        return completion

    def _record_usage(self, completion: Completion) -> None:
        usage = completion.usage or {}
        prompt = _int(usage.get("prompt_tokens"))
        output = _int(usage.get("completion_tokens"))
        estimated = False
        if prompt <= 0:
            only = self.registry.read_only_names() if self.settings.mode == "plan" else None
            prompt = estimate_messages_tokens(self._request_messages())
            prompt += estimate_specs_tokens(self.registry.specs(only=only))
            estimated = True
        if output <= 0 and completion.content:
            output = estimate_tokens(completion.content)
            estimated = True

        self.total_usage["prompt_tokens"] = self.total_usage.get("prompt_tokens", 0) + prompt
        self.total_usage["completion_tokens"] = self.total_usage.get("completion_tokens", 0) + output
        provider = self.settings.provider.name
        model = self.settings.model
        bucket = (
            self.usage_by_model
            .setdefault(provider, {})
            .setdefault(model, {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "estimated": 0})
        )
        bucket["requests"] += 1
        bucket["prompt_tokens"] += prompt
        bucket["completion_tokens"] += output
        if estimated:
            bucket["estimated"] += 1
        self.last_prompt_tokens = prompt
        self.last_completion_tokens = output
        self.last_usage_estimated = estimated

    def _request_messages(self) -> list[dict[str, Any]]:
        return [{"role": "system", "content": self.system_prompt}, *self.messages]

    def _mode_block(self, name: str, arguments: dict[str, Any]) -> str:
        """Explain why a tool is unavailable in plan mode ('' = allowed)."""
        if self.settings.mode != "plan":
            return ""
        tool = self.registry.get(name)
        if tool is not None and tool.read_only:
            if name == "http_request":
                method = str(arguments.get("method") or "GET").upper()
                if method not in ("GET", "HEAD"):
                    return "Blocked by plan mode: only GET/HEAD HTTP requests are allowed."
            return ""
        return (
            f"Blocked by plan mode (read-only): '{name}' is not available. "
            "Investigate with read-only tools, present the plan, then ask the user "
            "to switch with /mode build before executing anything."
        )

    def _run_tool(
        self,
        call: Any,
        step: int,
        on_tool: Callable[[str, dict[str, Any]], None] | None,
        on_tool_result: Callable[[str, str, bool], None] | None,
    ) -> None:
        arguments = dict(call.arguments)
        patched = self.hooks.first_result("on_tool_call", name=call.name, arguments=arguments)
        if isinstance(patched, dict):
            arguments = patched

        if on_tool:
            on_tool(call.name, arguments)

        blocked = self._mode_block(call.name, arguments)
        tool = self.registry.get(call.name)
        denied = bool(
            tool
            and tool.dangerous
            and self.confirm
            and not self.confirm(call.name, arguments)
        )

        if blocked:
            result, is_error = blocked, True
        elif denied:
            result, is_error = (
                "Denied by the user. Do not retry blindly: explain what you wanted to do "
                "and ask how to proceed.",
                True,
            )
        elif call.error:
            result, is_error = f"Error: {call.error}", True
        else:
            result, is_error = self.registry.call(call.name, arguments)

        self.messages.append(
            {
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.name,
                "content": result,
            }
        )
        self.hooks.run("on_tool_result", name=call.name, result=result, is_error=is_error)
        if on_tool_result:
            on_tool_result(call.name, result, is_error)
