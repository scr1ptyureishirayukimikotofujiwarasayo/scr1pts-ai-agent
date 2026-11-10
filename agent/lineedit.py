"""A small raw-mode line editor.

Gives the terminal a live slash-command palette (type `/` and the command list
appears below the prompt), cursor movement, history and Tab completion — with
no dependencies. Falls back to plain `input()` when stdin/stdout is not a TTY
(pipes, IDE runners, tests).

Keys: printable, Backspace, Delete, Left/Right, Home/End, Up/Down (history),
Tab (complete), Ctrl+C (cancel line), Ctrl+D (EOF), Enter (submit).
"""

from __future__ import annotations

import codecs
import os
import sys
from typing import Callable

PaletteFn = Callable[[str], list[str]]
CompleterFn = Callable[[str], list[str]]


class LineEditor:
    def __init__(
        self,
        palette: PaletteFn | None = None,
        completer: CompleterFn | None = None,
        max_palette: int = 10,
        history_limit: int = 200,
    ) -> None:
        self.palette = palette or (lambda _buffer: [])
        self.completer = completer
        self.max_palette = max_palette
        self.history_limit = history_limit
        self.history: list[str] = []

        self._prompt = ""
        self._buffer = ""
        self._cursor = 0
        self._shown = 0
        self._history_index = 0

    # -- public -------------------------------------------------------------

    def read(self, prompt: str) -> str:
        if not interactive():
            line = input(prompt)
            self._remember(line)
            return line

        self._prompt = prompt
        self._buffer = ""
        self._cursor = 0
        self._shown = 0
        self._history_index = len(self.history)
        sys.stdout.write(prompt)
        sys.stdout.flush()
        try:
            if os.name == "nt":
                return self._read_windows()
            return self._read_posix()
        finally:
            self._erase_palette()

    # -- platform readers ---------------------------------------------------

    def _read_windows(self) -> str:
        import msvcrt

        while True:
            key = msvcrt.getwch()
            if key == "\x03":
                raise KeyboardInterrupt
            if key == "\x1a":
                self._newline()
                raise EOFError
            if key in ("\r", "\n"):
                return self._finish()
            if key == "\x08":
                self._backspace()
            elif key == "\t":
                self._complete()
            elif key in ("\x00", "\xe0"):
                self._windows_special(msvcrt.getwch())
            elif key >= " ":
                self._insert(key)

    def _windows_special(self, code: str) -> None:
        actions = {
            "H": self._up,
            "P": self._down,
            "K": self._left,
            "M": self._right,
            "G": self._home,
            "O": self._end,
            "S": self._delete,
        }
        action = actions.get(code)
        if action:
            action()

    def _read_posix(self) -> str:
        import termios
        import tty

        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        tty.setcbreak(fd)
        try:
            while True:
                raw = os.read(fd, 1)
                if not raw:
                    if not self._buffer:
                        self._newline()
                        raise EOFError
                    continue
                if raw == b"\x1b":
                    self._posix_escape(fd)
                    continue
                for char in decoder.decode(raw):
                    if char in ("\r", "\n"):
                        return self._finish()
                    if char in ("\x7f", "\x08"):
                        self._backspace()
                    elif char == "\t":
                        self._complete()
                    elif char == "\x03":
                        raise KeyboardInterrupt
                    elif char >= " ":
                        self._insert(char)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)

    def _posix_escape(self, fd: int) -> None:
        second = _read_with_timeout(fd)
        if second not in (b"[", b"O"):
            return
        third = _read_with_timeout(fd)
        if not third:
            return
        actions = {
            b"A": self._up,
            b"B": self._down,
            b"C": self._right,
            b"D": self._left,
            b"H": self._home,
            b"F": self._end,
        }
        action = actions.get(third)
        if action:
            action()
            return
        if third.isdigit():
            # e.g. ESC [ 3 ~  (Delete)
            tail = _read_with_timeout(fd)
            while tail and tail != b"~":
                tail = _read_with_timeout(fd)
            if third == b"3":
                self._delete()

    # -- key handlers -------------------------------------------------------

    def _insert(self, text: str) -> None:
        self._buffer = self._buffer[: self._cursor] + text + self._buffer[self._cursor :]
        self._cursor += len(text)
        self._refresh()

    def _backspace(self) -> None:
        if self._cursor == 0:
            return
        self._buffer = self._buffer[: self._cursor - 1] + self._buffer[self._cursor :]
        self._cursor -= 1
        self._refresh()

    def _delete(self) -> None:
        if self._cursor >= len(self._buffer):
            return
        self._buffer = self._buffer[: self._cursor] + self._buffer[self._cursor + 1 :]
        self._refresh()

    def _left(self) -> None:
        if self._cursor > 0:
            self._cursor -= 1
            self._refresh()

    def _right(self) -> None:
        if self._cursor < len(self._buffer):
            self._cursor += 1
            self._refresh()

    def _home(self) -> None:
        self._cursor = 0
        self._refresh()

    def _end(self) -> None:
        self._cursor = len(self._buffer)
        self._refresh()

    def _up(self) -> None:
        if not self.history:
            return
        self._history_index = max(0, self._history_index - 1)
        self._load_history(self.history[self._history_index])

    def _down(self) -> None:
        if self._history_index >= len(self.history) - 1:
            self._history_index = len(self.history)
            self._load_history("")
        else:
            self._history_index += 1
            self._load_history(self.history[self._history_index])

    def _load_history(self, text: str) -> None:
        self._buffer = text
        self._cursor = len(text)
        self._refresh()

    def _complete(self) -> None:
        if self.completer is None or not self._buffer.startswith("/"):
            return
        prefix = self._buffer[1:]
        if " " in prefix:
            return
        candidates = [name for name in self.completer(prefix) if name.startswith(prefix)]
        if not candidates:
            return
        if len(candidates) == 1:
            self._buffer = f"/{candidates[0]} "
            self._cursor = len(self._buffer)
        else:
            common = os.path.commonprefix(candidates)
            if len(common) > len(prefix):
                self._buffer = f"/{common}"
                self._cursor = len(self._buffer)
        self._refresh()

    def _finish(self) -> str:
        self._erase_palette()
        sys.stdout.write(f"\r\x1b[2K{self._prompt}{self._buffer}\r\n")
        sys.stdout.flush()
        line = self._buffer
        self._remember(line)
        return line

    def _newline(self) -> None:
        sys.stdout.write("\r\n")
        sys.stdout.flush()

    def _remember(self, line: str) -> None:
        if line and (not self.history or self.history[-1] != line):
            self.history.append(line)
            if len(self.history) > self.history_limit:
                del self.history[: len(self.history) - self.history_limit]

    # -- rendering ----------------------------------------------------------

    def _refresh(self) -> None:
        out = sys.stdout.write
        self._erase_palette()
        out("\r\x1b[2K")
        out(self._prompt + self._buffer)
        if self._cursor < len(self._buffer):
            out(f"\x1b[{len(self._buffer) - self._cursor}D")

        lines: list[str] = []
        if self._buffer.startswith("/") and " " not in self._buffer:
            lines = self.palette(self._buffer)[: self.max_palette]
        if lines:
            for line in lines:
                out("\r\n\x1b[2K" + line)
            out(f"\x1b[{len(lines)}A")
            out("\r" + self._prompt + self._buffer)
            if self._cursor < len(self._buffer):
                out(f"\x1b[{len(self._buffer) - self._cursor}D")
            self._shown = len(lines)
        sys.stdout.flush()

    def _erase_palette(self) -> None:
        if not self._shown:
            return
        out = sys.stdout.write
        out(f"\x1b[{self._shown}B")
        for _ in range(self._shown):
            out("\r\x1b[2K\x1b[1A")
        out("\r")
        self._shown = 0
        sys.stdout.flush()


def interactive() -> bool:
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


def _read_with_timeout(fd: int, timeout: float = 0.05) -> bytes:
    import select

    try:
        ready, _, _ = select.select([fd], [], [], timeout)
    except (OSError, ValueError):
        return b""
    if not ready:
        return b""
    return os.read(fd, 1)
