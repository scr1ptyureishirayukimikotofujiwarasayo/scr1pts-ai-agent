"""Shell selection for `run_command`.

`auto` prefers bash when it is installed (Git Bash on Windows, `bash` on POSIX)
because npm/make/git and most agent-written snippets expect a POSIX shell. When
bash is absent it falls back to the platform shell (cmd.exe on Windows, sh
elsewhere). `run_python` never uses any of this.

Overrides:
    AGENT_SHELL   default kind: auto | bash | sh | cmd
    AGENT_BASH    path to a specific bash binary
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

KINDS = ("auto", "bash", "sh", "cmd")


class ShellError(RuntimeError):
    """Raised when the requested shell is not available."""


@dataclass(frozen=True)
class Shell:
    kind: str  # "bash" | "sh" | "cmd"
    path: str = ""
    login: bool = False

    def argv(self, command: str) -> list[str] | None:
        """argv to execute `command`, or None meaning "use shell=True"."""
        if self.kind == "bash" and self.path:
            return [self.path, "-lc" if self.login else "-c", command]
        return None

    @property
    def label(self) -> str:
        return self.kind


def detect() -> Shell:
    """Best available shell: bash if present, else the platform default."""
    bash = _find_bash()
    if bash is not None:
        return bash
    return Shell("cmd" if os.name == "nt" else "sh")


def resolve(requested: str | None = None) -> Shell:
    kind = (requested or "auto").strip().lower()
    if kind not in KINDS:
        raise ShellError(f"unknown shell {requested!r}; use one of: {', '.join(KINDS)}")
    if kind == "auto":
        return detect()
    if kind == "bash":
        bash = _find_bash()
        if bash is None:
            raise ShellError(
                "bash was not found. On Windows install Git Bash (or set AGENT_BASH), "
                "on POSIX install bash; or use shell='cmd' / 'sh'."
            )
        return bash
    if kind == "sh":
        if os.name == "nt":
            raise ShellError(
                "'sh' is not available on Windows; use 'bash' (Git Bash) or 'cmd'."
            )
        return Shell("sh")
    if os.name != "nt":
        raise ShellError("'cmd' is Windows-only; use 'bash' or 'sh'.")
    return Shell("cmd")


def _find_bash() -> Shell | None:
    override = os.environ.get("AGENT_BASH", "").strip()
    if override:
        path = override if os.path.isfile(override) else shutil.which(override)
        if not path:
            raise ShellError(f"AGENT_BASH is set to {override!r} but no such executable exists")
        return Shell("bash", path, login=True)

    if os.name != "nt":
        found = shutil.which("bash")
        return Shell("bash", found, login=False) if found else None

    candidates: list[str] = []
    found = shutil.which("bash")
    if found:
        candidates.append(found)
    for base in (
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramW6432"),
        os.environ.get("LOCALAPPDATA"),
    ):
        if not base:
            continue
        candidates.append(os.path.join(base, "Git", "bin", "bash.exe"))
        candidates.append(os.path.join(base, "Programs", "Git", "bin", "bash.exe"))
    # Git Bash first (integrates Windows paths); any other bash is a fallback.
    candidates.sort(key=lambda path: 0 if "git" in path.lower() else 1)
    for candidate in candidates:
        if os.path.isfile(candidate):
            return Shell("bash", candidate, login=True)
    return None
