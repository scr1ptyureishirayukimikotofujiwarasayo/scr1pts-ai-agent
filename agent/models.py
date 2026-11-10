"""Fetch the model catalogue from a provider's OpenAI-compatible /models endpoint."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any

from .config import Provider
from .llm import build_headers


class ModelListError(RuntimeError):
    """Raised when the model list cannot be fetched (bad key, no endpoint, ...)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def models_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/models"


def fetch_models(provider: Provider, timeout: float = 20.0) -> list[str]:
    """Return sorted model ids. Raises ModelListError with an actionable message."""
    if provider.requires_key and not provider.api_key:
        raise ModelListError(
            f"no API key for '{provider.name}' · /keys set {provider.name} <key>"
        )
    url = models_url(provider.base_url)
    request = urllib.request.Request(url, headers=build_headers(provider), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=max(1.0, float(timeout))) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise ModelListError("API key rejected (HTTP 401)", 401) from exc
        if exc.code in (403, 404, 405):
            raise ModelListError(
                f"catalogue unavailable (HTTP {exc.code}); the key may still work "
                "— use /model <id> or /model list",
                exc.code,
            ) from exc
        raise ModelListError(f"HTTP {exc.code} from {url}", exc.code) from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        raise ModelListError(f"cannot reach {url}: {exc}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ModelListError(f"invalid JSON from {url}") from exc

    models = extract_model_ids(payload)
    if not models:
        raise ModelListError("provider returned no models")
    return models


def extract_model_ids(payload: Any) -> list[str]:
    """Handle {data:[{id}]}, [..], {models:[{name}]} and plain string lists."""
    if isinstance(payload, dict):
        items = (
            payload.get("data")
            or payload.get("models")
            or payload.get("result")
            or []
        )
    elif isinstance(payload, list):
        items = payload
    else:
        return []
    if not isinstance(items, list):
        return []

    found: set[str] = set()
    for item in items:
        if isinstance(item, str):
            found.add(item.strip())
        elif isinstance(item, dict):
            for key in ("id", "name", "model"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    found.add(value.strip())
                    break
    return sorted(name for name in found if name)
