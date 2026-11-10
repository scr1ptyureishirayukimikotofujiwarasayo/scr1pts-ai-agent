"""Conversation compaction: turn older turns into a compact summary."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .llm import Client, LLMError

SUMMARY_HEADING = "## Earlier conversation (compacted)"

SUMMARY_SYSTEM = """\
You compress an AI agent's conversation so the agent can keep working with less
context. Preserve, in terse bullets:
- the user's goals, constraints and preferences
- decisions made and why
- files/directories touched (exact paths) and what changed
- findings, command/test results, errors and how they were fixed
- current state and pending TODO items / open questions
Drop pleasantries, restated code and anything already superseded.
Reply with the summary only, no preamble."""

MAX_TRANSCRIPT_CHARS = 48_000
MAX_DIGEST_CHARS = 4_000


@dataclass
class CompactionResult:
    before: int
    after: int
    summary_chars: int
    method: str  # "model" | "digest" | "noop"
    error: str = ""


def split_index(messages: list[dict[str, Any]], keep_turns: int) -> int:
    """Index of the first message to keep: starts at the keep_turns-th user turn."""
    users = [index for index, message in enumerate(messages) if message.get("role") == "user"]
    if keep_turns < 1 or len(users) <= keep_turns:
        return 0
    return users[-keep_turns]


def transcript_text(messages: list[dict[str, Any]], limit: int = MAX_TRANSCRIPT_CHARS) -> str:
    """Flatten messages for the summarizer, tail-first when over budget."""
    lines: list[str] = []
    for message in messages:
        role = message.get("role")
        if role == "user":
            lines.append("USER: " + _one_line(message.get("content")))
        elif role == "assistant":
            content = _one_line(message.get("content"))
            if content:
                lines.append("ASSISTANT: " + content)
            calls = message.get("tool_calls") or []
            for call in calls:
                function = call.get("function") or {}
                arguments = function.get("arguments") or ""
                if isinstance(arguments, dict):
                    arguments = json.dumps(arguments, ensure_ascii=False)
                lines.append(f"ASSISTANT TOOL CALL {function.get('name')}: {str(arguments)[:400]}")
        elif role == "tool":
            lines.append("TOOL RESULT: " + _one_line(message.get("content"))[:400])
        elif role == "system":
            lines.append("SYSTEM: " + _one_line(message.get("content")))
    text = "\n".join(lines)
    if len(text) > limit:
        text = "... [earlier part omitted]\n" + text[-limit:]
    return text


def summarize(client: Client, transcript: str, previous: str = "") -> str:
    """Ask the active model for a summary. Raises LLMError."""
    parts: list[str] = []
    if previous.strip():
        parts.append("Summary so far:\n" + previous.strip())
    parts.append("Conversation to fold in:\n" + transcript)
    parts.append("Return the updated summary.")
    completion = client.chat(
        [
            {"role": "system", "content": SUMMARY_SYSTEM},
            {"role": "user", "content": "\n\n".join(parts)},
        ],
        tools=None,
        on_delta=None,
    )
    return completion.content.strip()


def digest(messages: list[dict[str, Any]]) -> str:
    """Deterministic fallback when the model cannot summarise."""
    notes: list[str] = []
    for message in messages:
        role = message.get("role")
        if role == "user":
            notes.append("- user: " + _one_line(message.get("content"))[:300])
        elif role == "assistant":
            names = [
                str((call.get("function") or {}).get("name") or "")
                for call in message.get("tool_calls") or []
            ]
            text = _one_line(message.get("content"))[:200]
            if text:
                notes.append("- assistant: " + text)
            if names:
                notes.append("- tools used: " + ", ".join(name for name in names if name))
        elif role == "tool":
            notes.append("  result: " + _one_line(message.get("content"))[:160])
    text = "\n".join(notes)
    if not text:
        return "(no usable history)"
    if len(text) > MAX_DIGEST_CHARS:
        text = "... [truncated]\n" + text[-MAX_DIGEST_CHARS:]
    return text


def _one_line(value: Any, limit: int = 800) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit] + "..."


def summary_section(summary: str) -> str:
    if not summary.strip():
        return ""
    return f"{SUMMARY_HEADING}\n{summary.strip()}"
