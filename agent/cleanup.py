"""Remove local secrets and session state before sharing the harness.

Used by `clean.bat` (Windows), `clean.sh` (Linux/macOS/WSL) and
`python run.py --clean`. It only touches generated/private artefacts:

    reset  .env            -> all keys blank, template from .env.example
    delete .env backups    -> .env.bak, .env.local, .env.save, ...
    reset  config.json     -> clean defaults (workspace, providers, prompts)
    reset  mcp.json        -> {"mcpServers": {}}
    delete sessions/*.json -> conversations
    delete .cache/, __pycache__/, .pytest_cache/, .mypy_cache/, .ruff_cache/
    delete *.log
    keep   code, tests, skills/, plugins/, AGENTS.md, .env.example

Everything is confirm-gated unless `--yes` is passed.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .config import ROOT, save_config_file

ENV_BACKUP_GLOBS = (
    ".env.bak",
    ".env.backup",
    ".env.old",
    ".env.orig",
    ".env.save",
    ".env.local",
    ".env.production",
    ".env.dev",
    ".env.development",
)
CACHE_DIRS = (".cache", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".hypothesis")

CLEAN_CONFIG: dict[str, object] = {
    "provider": "openrouter",
    "model": "",
    "temperature": 0.7,
    "max_tokens": 4096,
    "max_steps": 32,
    "compact_keep_turns": 4,
    "stream": True,
    "timeout": 180,
    "confirm_shell": True,
    "auto_save": True,
    "show_tool_results": True,
    "show_usage": True,
    "verbose": False,
    "mode": "build",
    "shell": "auto",
    "workspace": ".",
    "skills_dir": "skills",
    "plugins_dir": "plugins",
    "sessions_dir": "sessions",
    "env_file": ".env",
    "mcp_config": "mcp.json",
    "mcp_enabled": True,
    "mcp_timeout": 30,
    "system_prompt": "",
    "providers": {},
}


@dataclass
class Plan:
    env: Path | None = None
    env_backups: list[Path] = field(default_factory=list)
    config: Path | None = None
    mcp: Path | None = None
    sessions: list[Path] = field(default_factory=list)
    caches: list[Path] = field(default_factory=list)
    logs: list[Path] = field(default_factory=list)


@dataclass
class CleanupReport:
    reset: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.reset or self.removed)


def scan(root: Path = ROOT, keep_config: bool = False, keep_mcp: bool = False) -> Plan:
    root = Path(root).resolve()
    plan = Plan()

    if (root / ".env").exists() or (root / ".env.example").exists():
        plan.env = root / ".env"
    plan.env_backups = [path for name in ENV_BACKUP_GLOBS if (path := root / name).is_file()]
    plan.config = None if keep_config else root / "config.json"
    plan.mcp = None if keep_mcp else root / "mcp.json"

    sessions = root / "sessions"
    if sessions.is_dir():
        plan.sessions = sorted(
            path
            for path in sessions.iterdir()
            if path.is_file() and path.name != ".gitkeep"
        )

    for name in CACHE_DIRS:
        candidate = root / name
        if candidate.is_dir():
            plan.caches.append(candidate)
    plan.caches.extend(sorted(root.rglob("__pycache__")))
    plan.logs = sorted(root.glob("*.log"))
    return plan


def preview(root: Path = ROOT, keep_config: bool = False, keep_mcp: bool = False) -> list[str]:
    plan = scan(root, keep_config, keep_mcp)
    lines: list[str] = []
    if plan.env:
        lines.append("reset  .env                    (all API keys blank, from .env.example)")
    for path in plan.env_backups:
        lines.append(f"delete {_rel(path, root)}")
    if plan.config and plan.config.exists():
        lines.append("reset  config.json             (workspace, prompts, provider overrides)")
    if plan.mcp and plan.mcp.exists():
        lines.append("reset  mcp.json                (no MCP servers, no tokens)")
    if plan.sessions:
        lines.append(f"delete sessions/               ({len(plan.sessions)} file(s))")
    for path in plan.caches:
        lines.append(f"delete {_rel(path, root)}/")
    if plan.logs:
        lines.append(f"delete *.log                   ({len(plan.logs)} file(s))")
    if not lines:
        lines.append("nothing to clean")
    return lines


def clean(
    root: Path = ROOT,
    keep_config: bool = False,
    keep_mcp: bool = False,
    dry_run: bool = False,
) -> CleanupReport:
    root = Path(root).resolve()
    plan = scan(root, keep_config, keep_mcp)
    report = CleanupReport(dry_run=dry_run)

    if plan.env:
        if not dry_run:
            plan.env.write_text(_blank_env(root / ".env.example"), encoding="utf-8")
        report.reset.append(".env (keys blank)")

    for path in plan.env_backups:
        _remove(path, root, report)

    if plan.config:
        if not dry_run:
            save_config_file(plan.config, dict(CLEAN_CONFIG))
        report.reset.append("config.json (defaults)")

    if plan.mcp:
        if not dry_run:
            plan.mcp.write_text(
                json.dumps({"mcpServers": {}}, indent=2) + "\n", encoding="utf-8"
            )
        report.reset.append("mcp.json (no servers)")

    sessions_dir = root / "sessions"
    if plan.sessions:
        for path in plan.sessions:
            _remove(path, root, report)
    if not dry_run:
        sessions_dir.mkdir(parents=True, exist_ok=True)
        keep_file = sessions_dir / ".gitkeep"
        if not keep_file.exists():
            keep_file.write_text("", encoding="utf-8")

    for path in plan.caches:
        _remove(path, root, report)
    for path in plan.logs:
        _remove(path, root, report)

    # python bytecode that survived (standalone .pyc outside __pycache__)
    for stale in sorted(root.rglob("*.pyc")):
        _remove(stale, root, report)

    if (root / "plugins").is_dir() and any((root / "plugins").glob("*.py")):
        report.warnings.append("plugins/ was left alone - review your own plugins for secrets")
    if (root / "AGENTS.md").exists():
        report.warnings.append("AGENTS.md was left alone (project instructions)")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="clean",
        description="Remove API keys, sessions, caches and reset configs before sharing.",
    )
    parser.add_argument("--root", default=str(ROOT), help="project directory to clean")
    parser.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    parser.add_argument("--dry-run", action="store_true", help="show what would change")
    parser.add_argument("--keep-config", action="store_true", help="leave config.json alone")
    parser.add_argument("--keep-mcp", action="store_true", help="leave mcp.json alone")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    lines = preview(root, keep_config=args.keep_config, keep_mcp=args.keep_mcp)
    print(f"Cleanup for {root}")
    for line in lines:
        print(f"  - {line}")
    print("  kept: code, tests, skills/, plugins/, AGENTS.md, .env.example")

    if args.dry_run:
        print("\nDry run: nothing changed.")
        return 0
    if not args.yes:
        try:
            answer = input("\nProceed? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 1
        if answer not in ("y", "yes"):
            print("Aborted; nothing changed.")
            return 1

    report = clean(
        root,
        keep_config=args.keep_config,
        keep_mcp=args.keep_mcp,
        dry_run=False,
    )
    for item in report.reset:
        print(f"  reset  {item}")
    for item in report.removed:
        print(f"  removed {item}")
    for warning in report.warnings:
        print(f"  note   {warning}")
    print("\nClean. Set keys again with /keys or .env when you need them.")
    return 0


def _blank_env(example: Path) -> str:
    """Blank every KEY=value in .env.example, keeping comments and key names."""
    if example.is_file():
        text = example.read_text(encoding="utf-8", errors="replace")
    else:
        text = "# API keys for the python ai agent.\n"
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key.startswith("export "):
                key = key[7:].strip()
            lines.append(f"{key}=")
        else:
            lines.append(line)
    return "\n".join(lines).rstrip() + "\n"


def _remove(path: Path, root: Path, report: CleanupReport) -> None:
    if not path.exists():
        return
    label = _rel(path, root)
    if report.dry_run:
        report.removed.append(label)
        return
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError as exc:
        report.warnings.append(f"could not remove {label}: {exc}")
        return
    report.removed.append(label)


def _rel(path: Path, root: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
