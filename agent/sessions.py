"""Session storage: one JSON file per conversation, newest first."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def slug(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in name.strip()).strip("-")


@dataclass
class SessionInfo:
    name: str
    path: Path
    created: str
    updated: str
    message_count: int
    provider: str
    model: str
    preview: str


class SessionStore:
    """Reads/writes `sessions/*.json`. Filenames are the session names."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # -- paths --------------------------------------------------------------

    def path(self, name: str) -> Path:
        return self.root / f"{slug(name)}.json"

    def exists(self, name: str) -> bool:
        return self.path(name).is_file()

    # -- listing ------------------------------------------------------------

    def list(self) -> list[SessionInfo]:
        entries: list[SessionInfo] = []
        for file in self.root.glob("*.json"):
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            updated = str(data.get("updated") or data.get("created") or "")
            if not updated:
                updated = time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(file.stat().st_mtime)
                )
            messages = data.get("messages") or []
            entries.append(
                SessionInfo(
                    name=file.stem,
                    path=file,
                    created=str(data.get("created") or updated),
                    updated=updated,
                    message_count=len(messages),
                    provider=str(data.get("provider") or ""),
                    model=str(data.get("model") or ""),
                    preview=_preview(messages),
                )
            )
        entries.sort(key=lambda info: info.path.stat().st_mtime, reverse=True)
        return entries

    # -- read / write -------------------------------------------------------

    def load(self, name: str) -> dict[str, Any]:
        file = self.path(name)
        data = json.loads(file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{file} does not contain a session object")
        return data

    def save(self, name: str, payload: dict[str, Any]) -> Path:
        file = self.path(name)
        body = dict(payload)
        body["name"] = file.stem
        body["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        body.setdefault("created", body["updated"])
        file.parent.mkdir(parents=True, exist_ok=True)
        tmp = file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, file)
        return file

    def delete(self, name: str) -> bool:
        file = self.path(name)
        if not file.is_file():
            return False
        file.unlink()
        return True


def _preview(messages: list[Any], limit: int = 64) -> str:
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "user":
            text = " ".join(str(message.get("content") or "").split())
            if text:
                return text[:limit] + ("..." if len(text) > limit else "")
    return ""
