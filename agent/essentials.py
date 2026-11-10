"""Essential built-in tools: files, search, shell, http, skills."""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .agentsmd import discover, format_text
from .config import Settings
from .shells import ShellError, resolve as resolve_shell
from .tools import Registry
from .util import truncate

MAX_OUTPUT = 30_000
SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
    ".idea",
    ".vscode",
}


def register_essentials(
    registry: Registry,
    settings: Settings,
    skill_reader: Callable[[str], str] | None = None,
) -> None:
    """Attach the core tool set to a registry."""
    def resolve(path: str | None) -> Path:
        raw = (path or ".").strip()
        candidate = Path(os.path.expanduser(raw))
        if not candidate.is_absolute():
            candidate = Path(settings.workspace).resolve() / candidate
        return candidate.resolve()

    # -- filesystem ---------------------------------------------------------

    @registry.register(
        "list_dir",
        "List files and folders in a directory, one per line, with '/' suffix for folders.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path (default: workspace root)"},
                "max_entries": {"type": "integer", "description": "Cap on entries returned", "default": 300},
            },
        },
        read_only=True,
    )
    def list_dir(path: str = ".", max_entries: int = 300) -> str:
        target = resolve(path)
        if not target.exists():
            return f"Error: no such directory: {target}"
        if not target.is_dir():
            return f"Error: not a directory: {target}"
        entries: list[str] = []
        for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            if child.name in SKIP_DIRS:
                continue
            suffix = "/" if child.is_dir() else ""
            entries.append(f"{child.name}{suffix}")
            if len(entries) >= max_entries:
                entries.append(f"... (truncated at {max_entries} entries)")
                break
        return "\n".join(entries) if entries else "(empty directory)"

    @registry.register(
        "read_file",
        "Read a UTF-8 text file. Use start_line/max_lines for big files.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "start_line": {"type": "integer", "description": "1-based first line", "default": 1},
                "max_lines": {"type": "integer", "description": "Max lines to return", "default": 600},
            },
            "required": ["path"],
        },
        read_only=True,
    )
    def read_file(path: str, start_line: int = 1, max_lines: int = 600) -> str:
        target = resolve(path)
        if not target.is_file():
            return f"Error: no such file: {target}"
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"Error reading {target}: {exc}"
        lines = text.splitlines()
        start = max(1, int(start_line))
        window = lines[start - 1 : start - 1 + max(1, int(max_lines))]
        numbered = [f"{start + i:>5}| {line}" for i, line in enumerate(window)]
        body = "\n".join(numbered) if numbered else "(empty file)"
        if start + len(window) - 1 < len(lines):
            body += f"\n... ({len(lines)} lines total, continue at start_line={start + len(window)})"
        return truncate(body, MAX_OUTPUT)

    @registry.register(
        "write_file",
        "Create or overwrite a file with the given content. Creates parent folders.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "content": {"type": "string", "description": "Full file content"},
            },
            "required": ["path", "content"],
        },
        dangerous=True,
    )
    def write_file(path: str, content: str) -> str:
        target = resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} chars to {target}"

    @registry.register(
        "edit_file",
        "Replace an exact text snippet inside a file. Fails if old_text is missing or ambiguous.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "old_text": {"type": "string", "description": "Exact text to replace"},
                "new_text": {"type": "string", "description": "Replacement text"},
                "replace_all": {"type": "boolean", "description": "Replace every occurrence", "default": False},
            },
            "required": ["path", "old_text", "new_text"],
        },
        dangerous=True,
    )
    def edit_file(path: str, old_text: str, new_text: str, replace_all: bool = False) -> str:
        target = resolve(path)
        if not target.is_file():
            return f"Error: no such file: {target}"
        text = target.read_text(encoding="utf-8")
        count = text.count(old_text)
        if count == 0:
            return "Error: old_text not found in file. Read the file and copy the exact snippet."
        if count > 1 and not replace_all:
            return f"Error: old_text appears {count} times. Add more context or set replace_all=true."
        updated = text.replace(old_text, new_text) if replace_all else text.replace(old_text, new_text, 1)
        target.write_text(updated, encoding="utf-8")
        return f"Updated {target} ({count if replace_all else 1} replacement(s))"

    @registry.register(
        "search_files",
        "Find files by glob pattern, e.g. '*.py' or 'src/**/*.ts'.",
        {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern"},
                "path": {"type": "string", "description": "Directory to search", "default": "."},
                "max_results": {"type": "integer", "default": 200},
            },
            "required": ["pattern"],
        },
        read_only=True,
    )
    def search_files(pattern: str, path: str = ".", max_results: int = 200) -> str:
        root = resolve(path)
        if not root.is_dir():
            return f"Error: no such directory: {root}"
        matches: list[str] = []
        iterator = root.glob(pattern) if "**" in pattern else root.rglob(pattern)
        for candidate in iterator:
            if any(part in SKIP_DIRS for part in candidate.relative_to(root).parts):
                continue
            matches.append(str(candidate.relative_to(root)))
            if len(matches) >= max_results:
                matches.append(f"... (truncated at {max_results})")
                break
        return "\n".join(sorted(matches)) if matches else "No files matched."

    @registry.register(
        "grep",
        "Regex search inside text files. Returns path:line: text matches.",
        {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Python regular expression"},
                "path": {"type": "string", "description": "Directory or file", "default": "."},
                "include": {"type": "string", "description": "Glob filter, e.g. '*.py'", "default": "*"},
                "max_results": {"type": "integer", "default": 80},
                "ignore_case": {"type": "boolean", "default": True},
            },
            "required": ["pattern"],
        },
        read_only=True,
    )
    def grep(
        pattern: str,
        path: str = ".",
        include: str = "*",
        max_results: int = 80,
        ignore_case: bool = True,
    ) -> str:
        root = resolve(path)
        flags = re.IGNORECASE if ignore_case else 0
        try:
            regex = re.compile(pattern, flags)
        except re.error as exc:
            return f"Error: bad regex: {exc}"

        files: list[Path]
        if root.is_file():
            files = [root]
        elif root.is_dir():
            files = [
                p
                for p in root.rglob("*")
                if p.is_file()
                and fnmatch.fnmatch(p.name, include)
                and not any(part in SKIP_DIRS for part in p.relative_to(root).parts)
            ]
        else:
            return f"Error: no such path: {root}"

        hits: list[str] = []
        for file in files:
            try:
                if file.stat().st_size > 2_000_000:
                    continue
                content = file.read_text(encoding="utf-8", errors="strict")
            except (OSError, UnicodeDecodeError):
                continue
            for number, line in enumerate(content.splitlines(), 1):
                if regex.search(line):
                    shown = file.relative_to(root) if root.is_dir() else file
                    hits.append(f"{shown}:{number}: {line.strip()[:300]}")
                    if len(hits) >= max_results:
                        hits.append(f"... (truncated at {max_results})")
                        return "\n".join(hits)
        return "\n".join(hits) if hits else "No matches."

    # -- execution ----------------------------------------------------------

    @registry.register(
        "run_command",
        "Run the project's own shell tooling (git, make, npm/pnpm, uv, docker, test runners). "
        "Uses bash when available (best for npm/make/git), else the platform shell. "
        "For scripting or one-off logic prefer run_python.",
        {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command line"},
                "cwd": {"type": "string", "description": "Working directory", "default": "."},
                "timeout": {"type": "integer", "description": "Seconds before kill", "default": 120},
                "shell": {
                    "type": "string",
                    "enum": ["auto", "bash", "sh", "cmd"],
                    "description": (
                        "auto (default) prefers bash for npm/make/git; "
                        "bash forces Git Bash/POSIX bash; sh/cmd force the native shell"
                    ),
                    "default": "auto",
                },
            },
            "required": ["command"],
        },
        dangerous=True,
    )
    def run_command(
        command: str,
        cwd: str = ".",
        timeout: int = 120,
        shell: str | None = None,
    ) -> str:
        workdir = resolve(cwd)
        if not workdir.is_dir():
            return f"Error: no such directory: {workdir}"
        requested = (shell or "").strip().lower() or os.environ.get("AGENT_SHELL") or settings.shell
        try:
            chosen = resolve_shell(requested)
        except ShellError as exc:
            return f"Error: {exc}"
        argv = chosen.argv(command)
        try:
            completed = subprocess.run(
                argv if argv is not None else command,
                shell=argv is None,
                cwd=str(workdir),
                capture_output=True,
                text=True,
                timeout=max(1, int(timeout)),
                errors="replace",
            )
        except subprocess.TimeoutExpired as exc:
            partial = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            return f"Command timed out after {timeout}s ({chosen.label}).\n{truncate(partial, 4000)}"
        except OSError as exc:
            return f"Error starting command with {chosen.label}: {exc}"
        output = ""
        if completed.stdout:
            output += completed.stdout
        if completed.stderr:
            output += ("\n" if output else "") + "[stderr]\n" + completed.stderr
        output = output or "(no output)"
        return truncate(f"[exit {completed.returncode} · {chosen.label}]\n{output}", MAX_OUTPUT)

    @registry.register(
        "run_python",
        "Run a Python snippet in the workspace (preferred for scripting, file/JSON/CSV processing, HTTP, checks). Returns exit code, stdout and stderr.",
        {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python source to execute"},
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Extra argv values available via sys.argv[1:]",
                },
                "cwd": {"type": "string", "description": "Working directory", "default": "."},
                "timeout": {"type": "integer", "description": "Seconds before kill", "default": 120},
            },
            "required": ["code"],
        },
        dangerous=True,
        priority=10,
    )
    def run_python(
        code: str,
        args: list[str] | None = None,
        cwd: str = ".",
        timeout: int = 120,
    ) -> str:
        workdir = resolve(cwd)
        if not workdir.is_dir():
            return f"Error: no such directory: {workdir}"
        environment = dict(os.environ)
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUNBUFFERED"] = "1"
        argv = [sys.executable, "-c", code, *[str(arg) for arg in (args or [])]]
        try:
            completed = subprocess.run(
                argv,
                cwd=str(workdir),
                capture_output=True,
                text=True,
                errors="replace",
                timeout=max(1, int(timeout)),
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            partial = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            return f"Python timed out after {timeout}s.\n{truncate(partial, 4000)}"
        except OSError as exc:
            return f"Error starting Python: {exc}"
        output = ""
        if completed.stdout:
            output += completed.stdout
        if completed.stderr:
            output += ("\n" if output else "") + "[stderr]\n" + completed.stderr
        return truncate(f"[exit {completed.returncode}]\n{output or '(no output)'}", MAX_OUTPUT)

    # -- network ------------------------------------------------------------

    @registry.register(
        "http_request",
        "Perform an HTTP request and return status, selected headers and body text.",
        {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "method": {"type": "string", "default": "GET"},
                "headers": {"type": "object", "description": "Extra request headers", "default": {}},
                "body": {"type": "string", "description": "Raw request body", "default": ""},
                "timeout": {"type": "integer", "default": 30},
            },
            "required": ["url"],
        },
        read_only=True,
    )
    def http_request(
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: str = "",
        timeout: int = 30,
    ) -> str:
        data = body.encode("utf-8") if body else None
        request = urllib.request.Request(
            url,
            data=data,
            headers={str(k): str(v) for k, v in (headers or {}).items()},
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(request, timeout=max(1, int(timeout))) as response:
                raw = response.read(MAX_OUTPUT * 2)
                text = raw.decode("utf-8", "replace")
                return f"status: {response.status}\ncontent-type: {response.headers.get('content-type')}\n\n{truncate(text, MAX_OUTPUT)}"
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace") if exc.fp else str(exc.reason)
            return f"status: {exc.code} ({exc.reason})\n\n{truncate(detail, 8000)}"
        except (urllib.error.URLError, TimeoutError) as exc:
            return f"Error: request failed: {exc}"

    # -- project instructions ----------------------------------------------

    @registry.register(
        "read_agents_md",
        "Read the AGENTS.md project instructions that apply to a directory (nearest file wins, parents included).",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File or directory (default: workspace)", "default": "."}
            },
        },
        read_only=True,
    )
    def read_agents_md(path: str = ".") -> str:
        target = resolve(path)
        directory = target if target.is_dir() else target.parent
        instructions = discover(directory)
        if not instructions:
            return f"No AGENTS.md found in {directory} or its parents."
        return format_text(instructions)

    # -- misc ---------------------------------------------------------------

    @registry.register(
        "todo_write",
        "Record or update the task list for the current session (printed to the user).",
        {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "done"],
                            },
                        },
                        "required": ["title", "status"],
                    },
                }
            },
            "required": ["items"],
        },
        read_only=True,
    )
    def todo_write(items: list[dict[str, Any]]) -> str:
        marks = {"pending": "[ ]", "in_progress": "[~]", "done": "[x]"}
        return "\n".join(f"{marks.get(i.get('status', 'pending'), '[ ]')} {i.get('title', '')}" for i in items)

    def use_skill_impl(name: str) -> str:
        if skill_reader is None:
            return "Error: skills are not loaded."
        return skill_reader(name)

    registry.add(
        "use_skill",
        "Load the full instructions of a skill by name. Call this before doing a task the skill covers.",
        {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Skill name from the skills list"}},
            "required": ["name"],
        },
        use_skill_impl,
        read_only=True,
    )


def json_safe(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)
