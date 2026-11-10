"""Interactive-TTY test for the slash-command palette (POSIX only).

Spawns the real REPL in a pseudo-terminal and drives the line editor with
keystrokes: `/` shows the command list, `/sw` filters to /switch, Tab completes
it, Ctrl+C cancels the line without quitting, the model picker opens with the
fetched catalogue and navigates with the arrow keys, `/quit` exits cleanly.

    python tests/editor_pty_test.py
"""

from __future__ import annotations

import os
import re
import select
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

MODELS = ("fake-model-a", "fake-model-b", "fake-model-c")


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        raise SystemExit(1)

SERVER_CODE = """
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CATALOG = {
    "lmstudio": {
        "id": "lmstudio",
        "name": "LM Studio",
        "models": {
            name: {
                "id": name,
                "limit": {"context": 128000, "output": 8192},
                "cost": {"input": 1, "output": 2},
                "tool_call": True,
            }
            for name in %r
        },
    }
}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        if self.path == "/api.json":
            payload = CATALOG
        else:
            payload = {"object": "list", "data": [{"id": name} for name in %r]}
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
print(server.server_address[1], flush=True)
server.serve_forever()
""" % (MODELS, MODELS)


class Pty:
    def __init__(self) -> None:
        self.fd = -1
        self.pid = -1
        self.buffer = ""

    def start(self) -> None:
        pid, fd = os.forkpty()
        if pid == 0:
            os.chdir(ROOT)
            os.environ["TERM"] = "xterm-256color"
            os.execv(sys.executable, [sys.executable, "run.py", "--provider", "lmstudio"])
        self.pid, self.fd = pid, fd

    def read(self, timeout: float = 5.0) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            ready, _, _ = select.select([self.fd], [], [], 0.1)
            if not ready:
                continue
            try:
                chunk = os.read(self.fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            self.buffer += chunk.decode("utf-8", "replace")
        return ANSI.sub("", self.buffer)

    def expect(self, needle: str, timeout: float = 5.0) -> bool:
        """Wait until `needle` appears anywhere in the session output."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if needle in ANSI.sub("", self.buffer):
                return True
            ready, _, _ = select.select([self.fd], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(self.fd, 65536)
                except OSError:
                    return False
                if not chunk:
                    return False
                self.buffer += chunk.decode("utf-8", "replace")
        return needle in ANSI.sub("", self.buffer)

    def expect_new(self, needle: str, start: int, timeout: float = 5.0) -> bool:
        """Wait for text that appears after byte offset `start` (ignores history)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if needle in ANSI.sub("", self.buffer[start:]):
                return True
            ready, _, _ = select.select([self.fd], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(self.fd, 65536)
                except OSError:
                    return False
                if not chunk:
                    return False
                self.buffer += chunk.decode("utf-8", "replace")
        return needle in ANSI.sub("", self.buffer[start:])

    def mark(self) -> int:
        return len(self.buffer)

    def send(self, data: str) -> None:
        os.write(self.fd, data.encode())


def main() -> None:
    if os.name == "nt":
        print("skipped: needs POSIX pty (run tests/smoke_test.py on Windows)")
        return

    # External process (not a thread) so forkpty stays safe.
    model_server = subprocess.Popen(
        [sys.executable, "-c", SERVER_CODE],
        stdout=subprocess.PIPE,
        text=True,
    )
    port = int(model_server.stdout.readline().strip())
    os.environ["LMSTUDIO_BASE_URL"] = f"http://127.0.0.1:{port}/v1"
    os.environ["LMSTUDIO_API_KEY"] = "pty-key"
    os.environ["MODELS_DEV_URL"] = f"http://127.0.0.1:{port}/api.json"
    os.environ["MODELS_DEV_CACHE"] = str(Path(tempfile.mkdtemp(prefix="modelsdev-")) / "c.json")
    os.environ.pop("GITHUB_TOKEN", None)  # keep the pty test hermetic (no npx spawn)

    pty = Pty()
    pty.start()
    check("banner", pty.expect("python ai agent"))
    check("prompt", pty.expect("> "))

    time.sleep(0.4)  # let the line editor enter raw mode
    pty.send("/")
    check("typing / shows the command list", pty.expect("/switch") and pty.expect("/keys"))
    check("palette hint", pty.expect("Tab completes"))
    check("palette lists every command", pty.expect("/quit"))

    pty.send("zz")
    check("filter with no matches", pty.expect("no command matches /zz"))

    pty.send("\x7f\x7f")  # backspace twice
    pty.send("qu")
    pty.send("\t")  # Tab completes to "/quit "
    check("Tab completes", pty.expect("/quit "))
    check("palette hidden after completion", pty.expect("> /quit "))

    pty.send("\x03")  # Ctrl+C cancels the line, must not quit
    check("Ctrl+C keeps the session", pty.expect("cancelled"))
    check("prompt is back", pty.expect("> "))

    # -- model picker: fetch -> arrows -> Enter ---------------------------
    mark = pty.mark()
    pty.send("/model\r")
    check("picker lists fetched models", pty.expect_new("fake-model-a", mark))
    check("picker highlights the first option", pty.expect_new("❯ fake-model-a", mark))
    check("picker shows models.dev metadata", pty.expect_new("128k ctx", mark))

    mark = pty.mark()
    pty.send("\x1b[B")  # Down arrow
    pty.send("\r")
    check("picker selects with arrows + Enter", pty.expect_new("model -> fake-model-b", mark))

    mark = pty.mark()
    pty.send("/model\r")
    check("picker reopens with the catalogue", pty.expect_new("fake-model-c", mark))
    check("picker shows the new current model", pty.expect_new("*", mark))

    mark = pty.mark()
    pty.send("\x1b")  # Esc cancels
    check("Esc cancels the picker", pty.expect_new("cancelled", mark))
    time.sleep(0.2)  # let the line editor take over before typing again

    pty.send("/quit\r")
    time.sleep(0.3)
    _, status = os.waitpid(pty.pid, 0)
    check("clean exit", os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, str(status))
    model_server.terminate()
    model_server.wait(timeout=5)
    print("\nPalette + model picker test passed.")


if __name__ == "__main__":
    main()
