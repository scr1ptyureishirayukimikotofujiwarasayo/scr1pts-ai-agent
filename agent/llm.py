"""HTTP client for OpenAI-compatible chat completion endpoints."""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import Settings

DeltaFn = Callable[[str], None]


class LLMError(RuntimeError):
    """Raised when the model request fails."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_arguments: str = "{}"
    error: str = ""

    def to_message(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.raw_arguments or "{}"},
        }


@dataclass
class Completion:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def message(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [call.to_message() for call in self.tool_calls]
        return msg


class Client:
    """Talks to any OpenAI-compatible /chat/completions endpoint."""

    def __init__(self, settings: Settings, retries: int = 3) -> None:
        self.settings = settings
        self.retries = retries
        self._stream_usage_off = False

    # -- public API ---------------------------------------------------------

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_delta: DeltaFn | None = None,
        stream: bool | None = None,
    ) -> Completion:
        provider = self.settings.provider
        use_stream = self.settings.stream if stream is None else stream
        # Streaming only makes sense when the caller can consume deltas.
        if on_delta is None:
            use_stream = False

        body: dict[str, Any] = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
            "stream": use_stream,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if use_stream and not self._stream_usage_off:
            body["stream_options"] = {"include_usage": True}

        payload = json.dumps(body).encode("utf-8")
        headers = build_headers(
            provider, accept="text/event-stream" if use_stream else "application/json"
        )

        last_error = ""
        for attempt in range(1, self.retries + 1):
            request = urllib.request.Request(
                provider.endpoint, data=payload, headers=headers, method="POST"
            )
            try:
                with urllib.request.urlopen(request, timeout=self.settings.timeout) as response:
                    if use_stream:
                        return self._read_stream(response, on_delta)
                    return self._read_json(response)
            except urllib.error.HTTPError as exc:
                detail = _read_error_body(exc)
                last_error = f"HTTP {exc.code} from {provider.name}: {detail}"
                if (
                    exc.code == 400
                    and "stream_options" in detail.lower()
                    and "stream_options" in body
                ):
                    # provider rejects usage reporting; retry without it once
                    body.pop("stream_options", None)
                    self._stream_usage_off = True
                    payload = json.dumps(body).encode("utf-8")
                    continue
                if exc.code in (408, 409, 429) or exc.code >= 500:
                    if attempt < self.retries:
                        time.sleep(min(2 ** (attempt - 1), 8))
                        continue
                raise LLMError(last_error) from exc
            except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
                last_error = f"Network error talking to {provider.name}: {exc}"
                if attempt < self.retries:
                    time.sleep(min(2 ** (attempt - 1), 8))
                    continue
                raise LLMError(last_error) from exc

        raise LLMError(last_error or "Request failed")

    # -- response parsing ---------------------------------------------------

    def _read_json(self, response: Any) -> Completion:
        raw = response.read().decode("utf-8", "replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Invalid JSON response: {raw[:400]}") from exc
        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"Model returned no choices: {json.dumps(data)[:400]}")
        choice = choices[0]
        message = choice.get("message") or {}
        completion = Completion(
            content=_as_text(message.get("content")),
            finish_reason=str(choice.get("finish_reason") or ""),
            usage=data.get("usage") or {},
        )
        for call in message.get("tool_calls") or []:
            completion.tool_calls.append(_parse_tool_call(call))
        return completion

    def _read_stream(self, response: Any, on_delta: DeltaFn) -> Completion:
        completion = Completion()
        partial: dict[int, dict[str, str]] = {}

        for raw_line in response:
            line = raw_line.decode("utf-8", "replace").strip()
            if not line or line.startswith(":") or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            if chunk.get("usage"):
                completion.usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                piece = _as_text(delta.get("content"))
                if piece:
                    completion.content += piece
                    on_delta(piece)
                if choice.get("finish_reason"):
                    completion.finish_reason = str(choice["finish_reason"])
                for call in delta.get("tool_calls") or []:
                    index = int(call.get("index") or 0)
                    slot = partial.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    if call.get("id"):
                        slot["id"] = str(call["id"])
                    fn = call.get("function") or {}
                    if fn.get("name"):
                        slot["name"] = str(fn["name"])
                    if fn.get("arguments"):
                        slot["arguments"] += str(fn["arguments"])

        for index in sorted(partial):
            slot = partial[index]
            if not slot["name"]:
                continue
            completion.tool_calls.append(
                _parse_tool_call(
                    {
                        "id": slot["id"] or f"call_{index}",
                        "function": {"name": slot["name"], "arguments": slot["arguments"] or "{}"},
                    }
                )
            )
        return completion


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def build_headers(provider: Any, accept: str = "application/json") -> dict[str, str]:
    """Auth + provider headers used for every request (chat and /models)."""
    headers = {"Content-Type": "application/json", "Accept": accept}
    if provider.api_key:
        headers["Authorization"] = f"Bearer {provider.api_key}"
        if provider.name == "anthropic":
            headers["x-api-key"] = provider.api_key
            headers["anthropic-version"] = "2023-06-01"
    for key, value in provider.headers.items():
        if value:
            headers[key] = value
    return headers


def _as_text(value: Any) -> str:
    """Normalise content that may be a string or a list of content parts."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for part in value:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
                elif isinstance(text, dict) and isinstance(text.get("value"), str):
                    parts.append(text["value"])
        return "".join(parts)
    return str(value)


def _parse_tool_call(call: dict[str, Any]) -> ToolCall:
    fn = call.get("function") or {}
    raw = fn.get("arguments")
    if isinstance(raw, dict):
        raw = json.dumps(raw)
    raw = raw if isinstance(raw, str) and raw.strip() else "{}"
    arguments: dict[str, Any] = {}
    error = ""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            arguments = parsed
        else:
            error = "tool arguments must be a JSON object"
    except json.JSONDecodeError as exc:
        error = f"invalid JSON arguments: {exc}"
    return ToolCall(
        id=str(call.get("id") or "call_0"),
        name=str(fn.get("name") or ""),
        arguments=arguments,
        raw_arguments=raw,
        error=error,
    )


def _read_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - best effort only
        return str(exc.reason)
    try:
        data = json.loads(body)
        error = data.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)
        if error:
            return str(error)
        if data.get("message"):
            return str(data["message"])
    except json.JSONDecodeError:
        pass
    return body[:600].strip() or str(exc.reason)
