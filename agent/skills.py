"""Skills: markdown instruction packs the agent loads on demand.

Layout (either works):

    skills/
      my-skill/
        SKILL.md          <- frontmatter + instructions
        scripts/...       <- optional supporting files
      quick-note.md       <- single-file skill

Frontmatter is optional:

    ---
    name: pdf-report           # defaults to the folder/file name
    description: How to build PDF reports. Use when the user asks for a PDF.
    ---
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_SKILL_CHARS = 40_000


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split '--- key: value ---' frontmatter from the body. No PyYAML needed."""
    clean = text.lstrip("\ufeff")
    lines = clean.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, clean.strip()
    meta: dict[str, str] = {}
    for index in range(1, len(lines)):
        line = lines[index]
        if line.strip() == "---":
            return meta, "\n".join(lines[index + 1 :]).strip()
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip().lower()] = value.strip().strip("\"'")
    return meta, clean.strip()  # unterminated block: treat as plain markdown


@dataclass
class Skill:
    name: str
    description: str
    path: Path
    directory: Path

    def read(self) -> str:
        return self.path.read_text(encoding="utf-8", errors="replace")


class SkillLoader:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.skills: dict[str, Skill] = {}
        self._lower: dict[str, str] = {}
        self.errors: list[str] = []
        self.reload()

    def reload(self) -> None:
        self.skills = {}
        self._lower = {}
        self.errors = []
        if not self.root.is_dir():
            return
        for entry in sorted(self.root.iterdir()):
            if entry.name.startswith((".", "_")):
                continue
            if entry.is_dir():
                for candidate in ("SKILL.md", "skill.md", "README.md"):
                    file = entry / candidate
                    if file.is_file():
                        self._load(file, entry.name)
                        break
            elif entry.suffix.lower() == ".md":
                self._load(entry, entry.stem)

    def _load(self, file: Path, default_name: str) -> None:
        try:
            meta, body = parse_frontmatter(file.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            self.errors.append(f"{file}: {exc}")
            return
        name = (meta.get("name") or default_name).strip()
        description = (meta.get("description") or "").strip()
        if not description:
            description = _first_sentence(body) or f"Instructions in {file.name}"
        if name in self.skills:
            self.errors.append(f"duplicate skill name '{name}' at {file}")
            return
        self.skills[name] = Skill(
            name=name, description=description, path=file, directory=file.parent
        )
        self._lower[name.lower()] = name

    # -- API ----------------------------------------------------------------

    def get(self, name: str) -> Skill | None:
        key = name.strip()
        return self.skills.get(key) or self.skills.get(self._lower.get(key.lower(), ""))

    def read(self, name: str) -> str:
        skill = self.get(name)
        if skill is None:
            if not self.skills:
                return "Error: no skills are installed (skills/ folder is empty)."
            listing = ", ".join(sorted(self.skills))
            return f"Error: unknown skill '{name}'. Available: {listing}"
        body = skill.read()
        if len(body) > MAX_SKILL_CHARS:
            body = body[:MAX_SKILL_CHARS] + "\n... [skill truncated]"
        return (
            f"# Skill: {skill.name}\n"
            f"(files in this skill live in {skill.directory})\n\n"
            f"{body}"
        )

    def prompt_index(self) -> str:
        if not self.skills:
            return ""
        lines = [
            "## Installed skills",
            "Call the `use_skill` tool to load a skill's full instructions when a task matches:",
        ]
        for name in sorted(self.skills):
            self.skills[name].description = " ".join(self.skills[name].description.split())
            lines.append(f"- **{name}**: {self.skills[name].description}")
        return "\n".join(lines)

    def to_list(self) -> list[dict[str, Any]]:
        return [
            {"name": s.name, "description": s.description, "path": str(s.path)}
            for s in (self.skills[name] for name in sorted(self.skills))
        ]


def _first_sentence(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:300]
    return ""
