"""Small shared helpers (ANSI colours, truncation, path/version info)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

VERSION = "0.1.0"

_COLOUR = True


def enable_ansi() -> None:
    """Turn on VT escape processing for Windows consoles."""
    global _COLOUR
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:  # noqa: BLE001 - colour is cosmetic
        _COLOUR = False


def enable_utf8() -> None:
    """Make redirected/Windows output safe for block characters and arrows."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def colour(text: str, code: str) -> str:
    if not _COLOUR or not sys.stdout.isatty():
        return text
    return f"\x1b[{code}m{text}\x1b[0m"


def dim(text: str) -> str:
    return colour(text, "2")


def bold(text: str) -> str:
    return colour(text, "1")


def cyan(text: str) -> str:
    return colour(text, "36")


def green(text: str) -> str:
    return colour(text, "32")


def yellow(text: str) -> str:
    return colour(text, "33")


def red(text: str) -> str:
    return colour(text, "31")


def truncate(text: str, limit: int, note: str = "") -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    suffix = note or f"\n... [truncated {omitted} chars]"
    return text[:limit] + suffix


def display_path(path: Path) -> str:
    try:
        return str(path)
    except Exception:  # noqa: BLE001
        return repr(path)
