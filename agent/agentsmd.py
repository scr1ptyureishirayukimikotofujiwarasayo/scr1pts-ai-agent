"""AGENTS.md project instructions.

The harness looks for `AGENTS.md` in the workspace and in each parent directory
(nearest first) and injects the contents into the system prompt, so a repository
can tell the agent how it wants work done.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

FILENAMES = ("AGENTS.md", "agents.md", "Agents.md", "AGENT.md")
MAX_FILE_CHARS = 20_000
MAX_TOTAL_CHARS = 40_000


@dataclass
class Instructions:
    path: Path
    text: str

    @property
    def directory(self) -> Path:
        return self.path.parent


def find_in(directory: Path) -> Path | None:
    """Return the AGENTS.md inside `directory`, if any."""
    for name in FILENAMES:
        candidate = Path(directory) / name
        if candidate.is_file():
            return candidate
    return None


def discover(start: Path, include_parents: bool = True) -> list[Instructions]:
    """Collect AGENTS.md files from `start` upwards, nearest first."""
    found: list[Instructions] = []
    seen: set[Path] = set()
    try:
        current = Path(start).resolve()
    except OSError:
        return found
    if current.is_file():
        current = current.parent

    while True:
        path = find_in(current)
        if path is not None:
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                found.append(_load(path))
        if not include_parents or current.parent == current:
            return found
        current = current.parent


def prompt_block(instructions: list[Instructions]) -> str:
    """Format discovered instructions for the system prompt."""
    if not instructions:
        return ""
    parts = [
        "## Project instructions (AGENTS.md)",
        "The user placed AGENTS.md files for this project. Treat them as binding "
        "instructions for this repository: follow their conventions, commands and "
        "constraints; when they conflict with your defaults, AGENTS.md wins.",
    ]
    budget = MAX_TOTAL_CHARS
    for item in instructions:
        text = item.text
        if len(text) > MAX_FILE_CHARS:
            text = text[:MAX_FILE_CHARS] + "\n... [AGENTS.md truncated]"
        if len(text) > budget:
            if budget <= 0:
                break
            text = text[:budget] + "\n... [AGENTS.md truncated]"
        parts.append(f"### {item.path}\n{text}")
        budget -= len(text)
    return "\n\n".join(parts)


def format_text(instructions: list[Instructions]) -> str:
    """Raw text for the read_agents_md tool."""
    if not instructions:
        return ""
    return "\n\n".join(f"# {item.path}\n{item.text}" for item in instructions)


def _load(path: Path) -> Instructions:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        text = ""
    return Instructions(path=path, text=text)
