"""models.dev catalogue: context window, pricing and capability metadata.

The catalogue (https://models.dev/api.json) is a public JSON document keyed by
provider id, each holding a `models` map. It is used to *annotate* model picks
with context size, price and capabilities — never as the source of truth for
which models your key can actually use (that is `GET /models`).

Cached on disk (24 h) so the picker stays fast and works offline. Overrides:

    MODELS_DEV_URL    alternate catalogue URL (tests, mirrors)
    MODELS_DEV_CACHE  alternate cache file
    MODELS_DEV_TTL    cache lifetime in seconds
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .config import ROOT

CATALOG_URL = "https://models.dev/api.json"
DEFAULT_TTL = 24 * 3600
DEFAULT_CACHE = ROOT / ".cache" / "models_dev.json"
TIMEOUT = 8.0

# our provider name -> models.dev provider id(s), first match wins
PROVIDER_ALIASES: dict[str, tuple[str, ...]] = {
    "openrouter": ("openrouter",),
    "opencode-go": ("opencode-go", "opencode"),
    "opencode": ("opencode",),
    "openai": ("openai",),
    "anthropic": ("anthropic",),
    "google": ("google",),
    "groq": ("groq",),
    "deepseek": ("deepseek",),
    "mistral": ("mistral",),
    "together": ("togetherai",),
    "fireworks": ("fireworks-ai",),
    "xai": ("xai",),
    "cerebras": ("cerebras",),
    "perplexity": ("perplexity",),
    "moonshot": ("moonshotai",),
    "zhipu": ("zhipuai", "zai"),
    "dashscope": ("alibaba",),
    "novita": ("novita-ai", "novita"),
    "nebius": ("nebius",),
    "hyperbolic": ("hyperbolic",),
    "github": ("github-models",),
    "venice": ("venice",),
    "lmstudio": ("lmstudio",),
    "ollama": ("ollama", "ollama-cloud"),
}


def catalog_url() -> str:
    return os.environ.get("MODELS_DEV_URL", CATALOG_URL)


def cache_path() -> Path:
    override = os.environ.get("MODELS_DEV_CACHE")
    if override:
        return Path(override).expanduser()
    return DEFAULT_CACHE


def ttl_seconds() -> float:
    try:
        return float(os.environ.get("MODELS_DEV_TTL", DEFAULT_TTL))
    except ValueError:
        return float(DEFAULT_TTL)


def load_catalog(
    url: str | None = None,
    cache_file: Path | None = None,
    max_age: float | None = None,
    force: bool = False,
    timeout: float = TIMEOUT,
    allow_network: bool = True,
) -> tuple[dict[str, Any], str]:
    """Return (catalog, note). `note` is empty on success, else a warning."""
    path = Path(cache_file) if cache_file else cache_path()
    cached = _read_cache(path)
    age_limit = ttl_seconds() if max_age is None else max_age
    if cached and not force and (time.time() - cached["fetched_at"]) < age_limit:
        return cached["data"], ""
    if not allow_network:
        if cached:
            return cached["data"], "using cached models.dev catalogue"
        return {}, "models.dev catalogue not loaded"

    target = url or catalog_url()
    try:
        data = _download(target, timeout)
    except (OSError, ValueError, urllib.error.URLError, TimeoutError) as exc:
        if cached:
            return cached["data"], f"using cached models.dev catalogue ({exc})"
        return {}, f"cannot load models.dev catalogue: {exc}"

    _write_cache(path, data)
    return data, ""


def models_for(catalog: dict[str, Any], provider_name: str) -> dict[str, dict[str, Any]]:
    """Model id -> metadata for one provider, following provider aliases."""
    if not catalog:
        return {}
    providers = catalog.get("providers") if isinstance(catalog.get("providers"), dict) else catalog
    if not isinstance(providers, dict):
        return {}

    candidates = list(PROVIDER_ALIASES.get(provider_name.lower(), ())) + [provider_name]
    lowered = {str(key).lower(): value for key, value in providers.items()}
    for candidate in candidates:
        entry = providers.get(candidate) or lowered.get(candidate.lower())
        if not isinstance(entry, dict):
            continue
        models = entry.get("models")
        if isinstance(models, dict):
            return {
                str(model_id): meta
                for model_id, meta in models.items()
                if isinstance(meta, dict)
            }
    return {}


def lookup(entries: dict[str, dict[str, Any]], model: str) -> dict[str, Any] | None:
    if model in entries:
        return entries[model]
    lowered = {key.lower(): value for key, value in entries.items()}
    return lowered.get(model.lower())


def describe(meta: dict[str, Any]) -> str:
    """One-line annotation for the picker: `200k ctx · $3/$15 1M · tools`."""
    parts: list[str] = []
    limit = meta.get("limit") if isinstance(meta.get("limit"), dict) else {}
    context = _compact_number(limit.get("context"))
    if context:
        parts.append(f"{context} ctx")
    cost = meta.get("cost") if isinstance(meta.get("cost"), dict) else {}
    price_in, price_out = cost.get("input"), cost.get("output")
    if price_in is not None and price_out is not None:
        parts.append(f"${_price(price_in)}/${_price(price_out)} 1M")
    elif price_in is not None:
        parts.append(f"${_price(price_in)} in 1M")
    if meta.get("tool_call") is True:
        parts.append("tools")
    elif meta.get("tool_call") is False:
        parts.append("no tools")
    if meta.get("reasoning") is True:
        parts.append("reasoning")
    if meta.get("attachment") is True:
        parts.append("files")
    return " · ".join(parts)


def info(meta: dict[str, Any]) -> list[tuple[str, str]]:
    """Human-readable key/value pairs for /model info."""
    limit = meta.get("limit") if isinstance(meta.get("limit"), dict) else {}
    cost = meta.get("cost") if isinstance(meta.get("cost"), dict) else {}
    rows: list[tuple[str, str]] = []
    for label, key in (("name", "name"), ("family", "family"), ("knowledge", "knowledge")):
        value = meta.get(key)
        if value:
            rows.append((label, str(value)))
    if limit.get("context"):
        rows.append(("context", _compact_number(limit["context"]) or ""))
    if limit.get("output"):
        rows.append(("max output", _compact_number(limit["output"]) or ""))
    if cost:
        prices = []
        for key in ("input", "output", "cache_read", "cache_write"):
            if cost.get(key) is not None:
                prices.append(f"{key}=${_price(cost[key])}")
        if prices:
            rows.append(("cost / 1M", "  ".join(prices)))
    for label, key in (
        ("tool calls", "tool_call"),
        ("reasoning", "reasoning"),
        ("attachments", "attachment"),
        ("open weights", "open_weights"),
    ):
        if meta.get(key) is not None:
            rows.append((label, "yes" if meta[key] else "no"))
    modalities = meta.get("modalities") if isinstance(meta.get("modalities"), dict) else {}
    if modalities:
        inputs = modalities.get("input") or []
        outputs = modalities.get("output") or []
        if inputs or outputs:
            rows.append(("modalities", f"{'/'.join(inputs)} -> {'/'.join(outputs)}"))
    for label, key in (("released", "release_date"), ("updated", "last_updated")):
        if meta.get(key):
            rows.append((label, str(meta[key])))
    return [(label, value) for label, value in rows if value]


def _compact_number(value: Any) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return ""
    if number >= 1_000_000:
        return f"{number / 1_000_000:g}M"
    if number >= 1_000:
        return f"{number / 1_000:g}k"
    return str(number)


def _price(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:g}"


def _download(url: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"accept": "application/json", "user-agent": "python-ai-agent"},
    )
    with urllib.request.urlopen(request, timeout=max(1.0, timeout)) as response:
        raw = response.read()
    data = json.loads(raw.decode("utf-8", "replace"))
    if not isinstance(data, dict):
        raise ValueError("catalogue is not a JSON object")
    return data


def _read_cache(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    fetched_at = payload.get("fetched_at")
    if not isinstance(data, dict) or not isinstance(fetched_at, (int, float)):
        return None
    return {"data": data, "fetched_at": float(fetched_at)}


def _write_cache(path: Path, data: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps({"fetched_at": time.time(), "data": data}, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError:
        pass  # caching is best-effort
