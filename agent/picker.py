"""Interactive model picker: arrow keys, type-to-filter, Enter to choose.

Falls back to a numbered prompt when stdin is not a TTY (pipes, tests, IDE
runners), so scripted use still works.
"""

from __future__ import annotations

import codecs
import os
import sys
from typing import Mapping, Sequence

from .lineedit import interactive
from .util import bold, cyan, dim

MAX_VISIBLE = 10


def select(
    title: str,
    options: Sequence[str],
    current: str | None = None,
    max_visible: int = MAX_VISIBLE,
    details: Mapping[str, str] | None = None,
) -> str | None:
    """Let the user choose one of `options`. Returns None when cancelled.

    `details` maps an option to a short annotation shown dimmed next to it.
    """
    unique = list(dict.fromkeys(options))
    if not unique:
        print(dim("no models available"))
        return None
    extra = dict(details or {})
    if not interactive():
        return _select_plain(title, unique, current, extra)
    return _select_interactive(title, unique, current, max_visible, extra)


# ---------------------------------------------------------------------------
# interactive
# ---------------------------------------------------------------------------


def _select_interactive(
    title: str,
    options: list[str],
    current: str | None,
    max_visible: int,
    details: dict[str, str],
) -> str | None:
    try:
        initial = options.index(current) if current in options else 0
    except ValueError:
        initial = 0

    query = ""
    selected = initial
    drawn = 0
    terminal = _Terminal()
    with terminal:
        while True:
            lines, filtered = _compose(
                title, options, current, query, selected, max_visible, details
            )
            if filtered:
                selected = max(0, min(selected, len(filtered) - 1))
            drawn = _draw(lines, drawn)

            key = terminal.read_key()
            if key == "enter":
                _clear(drawn)
                chosen = filtered[selected] if filtered and selected < len(filtered) else None
                if chosen:
                    print(bold("model: ") + chosen)
                return chosen
            if key in ("escape", "ctrl-c", "eof"):
                _clear(drawn)
                print(dim("cancelled"))
                return None
            if key == "up":
                selected = max(0, selected - 1)
            elif key == "down":
                selected = min(max(0, len(filtered) - 1), selected + 1)
            elif key == "home":
                selected = 0
            elif key == "end":
                selected = max(0, len(filtered) - 1)
            elif key == "backspace":
                if query:
                    query = query[:-1]
                    selected = 0
            elif key == "delete":
                continue
            elif key == "tab":
                continue
            elif len(key) == 1 and key.isprintable():
                query += key
                selected = 0


def _compose(
    title: str,
    options: list[str],
    current: str | None,
    query: str,
    selected: int,
    max_visible: int,
    details: Mapping[str, str] | None = None,
) -> tuple[list[str], list[str]]:
    filtered = _filter(options, query)
    lines = [
        bold(title) + dim(f"  ({len(filtered)}/{len(options)})"),
        dim("↑/↓ select · Enter confirm · type to filter · Esc cancel"),
    ]
    if query:
        lines.append(dim("filter: ") + query)
    if not filtered:
        lines.append(dim("  no match"))
        return lines, filtered

    extra = details or {}
    width = min(52, max((len(name) for name in filtered), default=0))
    window = min(max(1, max_visible), len(filtered))
    start = max(0, min(selected - window // 2, len(filtered) - window))
    for index in range(start, start + window):
        name = filtered[index]
        pointer = cyan("❯ ") if index == selected else "  "
        star = " *" if name == current else ""
        line = f"{pointer}{name}{star}"
        suffix = extra.get(name)
        if suffix:
            line += " " * max(2, width + 4 - len(name) - len(star)) + dim(suffix)
        lines.append(bold(line) if index == selected else line)
    if start > 0 or start + window < len(filtered):
        lines.append(dim(f"  ... {start + 1}-{start + window} of {len(filtered)}"))
    return lines, filtered


def _filter(options: list[str], query: str) -> list[str]:
    if not query:
        return list(options)
    needle = query.lower()
    return [name for name in options if needle in name.lower()]


# ---------------------------------------------------------------------------
# plain (non-TTY)
# ---------------------------------------------------------------------------


def _select_plain(
    title: str,
    options: list[str],
    current: str | None,
    details: Mapping[str, str] | None = None,
) -> str | None:
    extra = details or {}
    print(bold(title))
    limit = 40
    for index, name in enumerate(options[:limit], 1):
        marker = "*" if name == current else " "
        suffix = extra.get(name)
        print(f"{marker}{index:>3}) {name}" + (f"  {suffix}" if suffix else ""))
    if len(options) > limit:
        print(dim(f"  ... {len(options) - limit} more (type an id, or use /model <id>)"))
    try:
        answer = input(dim("model number or id [Enter keeps current]: ")).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if not answer:
        return current
    if answer.isdigit():
        index = int(answer) - 1
        if 0 <= index < len(options):
            return options[index]
        print(dim("invalid number"))
        return None
    for name in options:
        if name.lower() == answer.lower():
            return name
    return answer  # allow ids that are not listed


# ---------------------------------------------------------------------------
# terminal plumbing
# ---------------------------------------------------------------------------


def _draw(lines: list[str], previous: int) -> int:
    out = sys.stdout.write
    if previous:
        out(f"\x1b[{previous - 1}A")
    for index, line in enumerate(lines):
        out("\r\x1b[2K" + line)
        if index < len(lines) - 1:
            out("\n")
    sys.stdout.flush()
    return len(lines)


def _clear(lines: int) -> None:
    if not lines:
        return
    out = sys.stdout.write
    out(f"\x1b[{lines - 1}A")
    for index in range(lines):
        out("\r\x1b[2K")
        if index < lines - 1:
            out("\n")
    out("\r")
    sys.stdout.flush()


class _Terminal:
    def __init__(self) -> None:
        self._fd = -1
        self._saved: list | None = None
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._pending = b""

    def __enter__(self) -> "_Terminal":
        if os.name != "nt":
            import termios
            import tty

            self._fd = sys.stdin.fileno()
            self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._saved is not None:
            import termios

            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
            self._saved = None

    def read_key(self) -> str:
        return self._windows_key() if os.name == "nt" else self._posix_key()

    def _windows_key(self) -> str:
        import msvcrt

        char = msvcrt.getwch()
        if char in ("\r", "\n"):
            return "enter"
        if char == "\x1b":
            return "escape"
        if char == "\x03":
            return "ctrl-c"
        if char == "\x1a":
            return "eof"
        if char == "\x08":
            return "backspace"
        if char == "\t":
            return "tab"
        if char in ("\x00", "\xe0"):
            code = msvcrt.getwch()
            return {
                "H": "up", "P": "down", "K": "left", "M": "right",
                "G": "home", "O": "end", "S": "delete",
            }.get(code, "")
        return char if char >= " " else ""

    def _posix_key(self) -> str:
        if self._pending:
            raw, self._pending = self._pending, b""
        else:
            raw = os.read(self._fd, 1)
        if not raw:
            return "eof"
        if raw == b"\x1b":
            second = _read_byte(self._fd)
            if second not in (b"[", b"O"):
                if second:
                    self._pending = second  # keep a fast-typed char for the next read
                return "escape"
            third = _read_byte(self._fd)
            if not third:
                return "escape"
            mapping = {
                b"A": "up", b"B": "down", b"C": "right", b"D": "left",
                b"H": "home", b"F": "end",
            }
            if third in mapping:
                return mapping[third]
            if third.isdigit():
                tail = third
                while tail and tail != b"~":
                    tail = _read_byte(self._fd)
                return {b"1": "home", b"3": "delete", b"4": "end", b"7": "home", b"8": "end"}.get(
                    third, ""
                )
            return ""
        if raw in (b"\r", b"\n"):
            return "enter"
        if raw in (b"\x7f", b"\x08"):
            return "backspace"
        if raw == b"\x03":
            return "ctrl-c"
        if raw == b"\x04":
            return "eof"
        if raw == b"\t":
            return "tab"
        for char in self._decoder.decode(raw):
            if char >= " ":
                return char
        return ""


def _read_byte(fd: int, timeout: float = 0.05) -> bytes:
    import select

    try:
        ready, _, _ = select.select([fd], [], [], timeout)
    except (OSError, ValueError):
        return b""
    if not ready:
        return b""
    return os.read(fd, 1)
