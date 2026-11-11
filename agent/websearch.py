"""Web search (SearXNG or DuckDuckGo) and readable page fetching.

No API keys required:

- **SearXNG** — set `SEARXNG_URL` (or `searxng_url` in config.json) to a running
  instance with `json` enabled in `search.formats`. Preferred when configured.
- **DuckDuckGo** — HTML endpoint, used as the zero-config fallback. No key, no
  tracking, but it is scraping and can rate-limit.

`web_fetch` pulls a page and converts HTML to readable text for the model.

Overrides (also used by tests):
    SEARXNG_URL       SearXNG base URL (e.g. http://localhost:8080)
    DUCKDUCKGO_URL    alternate HTML endpoint
"""

from __future__ import annotations

import html
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

from .config import Settings
from .util import VERSION, truncate

MAX_RESULTS = 10
MAX_OUTPUT = 12_000
FETCH_BYTES = 3_000_000
# Search engines and normal websites reject bot-style UAs; use a current
# browser string for page fetching/search, while API calls keep the agent UA.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

DDG_SEARCH_URL = "https://html.duckduckgo.com/html/"
DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"
SEARXNG_TIMEOUT = 15


class WebSearchError(RuntimeError):
    """Raised when search/fetch fails with an actionable message."""


def web_search(
    query: str,
    settings: Settings,
    max_results: int | None = None,
    backend: str | None = None,
) -> str:
    query = (query or "").strip()
    if not query:
        raise WebSearchError("query must not be empty")

    requested = (backend or "").strip().lower()
    choice = (
        requested
        if requested and requested != "auto"
        else (settings.web_search_backend or "auto").strip().lower()
    )
    if choice not in ("auto", "duckduckgo", "searxng"):
        raise WebSearchError("backend must be auto, duckduckgo or searxng")

    searxng = (settings.searxng_url or os.environ.get("SEARXNG_URL", "")).strip()
    if choice == "searxng" and not searxng:
        raise WebSearchError(
            "SearXNG selected but no URL is configured; set searxng_url "
            "(/settings searxng_url http://localhost:8080) or SEARXNG_URL"
        )
    if choice == "auto":
        choice = "searxng" if searxng else "duckduckgo"

    limit = int(max_results or settings.web_search_results or 5)
    limit = max(1, min(MAX_RESULTS, limit))

    if choice == "searxng":
        results = _search_searxng(query, searxng, limit)
    else:
        results = _search_duckduckgo(query, limit)

    lines = [f"Web search · {query} · {choice} · {len(results)} result(s)"]
    if not results:
        lines.append("No results.")
    for index, item in enumerate(results, 1):
        lines.append(f"{index}. {item['title']}")
        lines.append(f"   {item['url']}")
        snippet = " ".join(item.get("snippet", "").split())
        if snippet:
            lines.append(f"   {snippet}")
    return truncate("\n".join(lines), MAX_OUTPUT)


def web_fetch(url: str, max_chars: int = 8_000, timeout: int = 20) -> str:
    url = (url or "").strip()
    if not url:
        raise WebSearchError("url must not be empty")
    if not url.lower().startswith(("http://", "https://")):
        raise WebSearchError("url must start with http:// or https://")

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
            "Accept-Language": "en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=max(1, int(timeout))) as response:
            content_type = (response.headers.get("content-type") or "").lower()
            raw = response.read(FETCH_BYTES)
            final_url = response.geturl()
    except urllib.error.HTTPError as exc:
        raise WebSearchError(f"HTTP {exc.code} fetching {url}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise WebSearchError(f"cannot reach {url}: {exc}") from exc

    charset = "utf-8"
    match = re.search(r"charset=([\w-]+)", content_type)
    if match:
        charset = match.group(1)
    try:
        text = raw.decode(charset, "replace")
    except LookupError:
        text = raw.decode("utf-8", "replace")

    if "html" in content_type or text.lstrip().lower().startswith("<!doctype html") or "<html" in text[:500].lower():
        title, body = _html_to_text(text)
    else:
        title, body = "", text

    limit = max(500, min(40_000, int(max_chars)))
    header = f"Fetched: {final_url}"
    if title.strip():
        header += f"\nTitle: {' '.join(title.split())}"
    body = body.strip() or "(no readable text)"
    return truncate(f"{header}\n\n{body}", limit + len(header) + 8)


# ---------------------------------------------------------------------------
# SearXNG
# ---------------------------------------------------------------------------


def _search_searxng(query: str, base_url: str, limit: int) -> list[dict[str, str]]:
    base = base_url.strip().rstrip("/")
    if not base.endswith("/search"):
        base += "/search"
    params = urllib.parse.urlencode(
        {"q": query, "format": "json", "safesearch": "0", "categories": "general"}
    )
    url = f"{base}?{params}"
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=SEARXNG_TIMEOUT) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise WebSearchError(
            f"SearXNG answered HTTP {exc.code} at {base} "
            "(is JSON enabled? search.formats must include json)"
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise WebSearchError(f"cannot reach SearXNG at {base}: {exc}") from exc

    try:
        import json

        payload = json.loads(raw)
    except ValueError as exc:
        raise WebSearchError(
            f"SearXNG returned non-JSON; enable json in search.formats ({base})"
        ) from exc

    results: list[dict[str, str]] = []
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        link = str(item.get("url") or "").strip()
        if not link:
            continue
        results.append(
            {
                "title": str(item.get("title") or link),
                "url": link,
                "snippet": str(item.get("content") or ""),
            }
        )
        if len(results) >= limit:
            break
    return results


# ---------------------------------------------------------------------------
# DuckDuckGo
# ---------------------------------------------------------------------------


def _search_duckduckgo(query: str, limit: int) -> list[dict[str, str]]:
    """Try the full HTML endpoint, then the Lite layout. Best-effort by design."""
    override = os.environ.get("DUCKDUCKGO_URL", "").strip()
    attempts = [override] if override else [DDG_SEARCH_URL, DDG_LITE_URL]

    last_error: WebSearchError | None = None
    for base in attempts:
        try:
            markup = _fetch_ddg(query, base)
        except WebSearchError as exc:
            last_error = exc
            continue
        # Layouts differ (html vs lite); try both parsers on whatever came back.
        results = _parse_ddg_html(markup) or _parse_ddg_lite(markup)
        if results:
            return results[:limit]

    if last_error is not None:
        raise last_error
    raise WebSearchError(
        "DuckDuckGo returned no parseable results; configure SearXNG via searxng_url"
    )


def _fetch_ddg(query: str, base: str) -> str:
    url = base + ("&" if "?" in base else "?") + urllib.parse.urlencode({"q": query})
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://duckduckgo.com/",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=SEARXNG_TIMEOUT) as response:
            raw = response.read().decode("utf-8", "replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        if exc.code in (202, 403, 429):
            raise WebSearchError(
                "DuckDuckGo blocked the request (rate limit / bot check); "
                "configure SearXNG with searxng_url for reliable search"
            ) from exc
        raise WebSearchError(f"DuckDuckGo answered HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise WebSearchError(f"cannot reach DuckDuckGo: {exc}") from exc

    lowered = raw.lower()
    if status == 202 or "unusual traffic" in lowered or "anomaly.js" in lowered:
        raise WebSearchError(
            "DuckDuckGo returned a bot challenge; configure SearXNG via searxng_url"
        )
    return raw


class _ResultParser(HTMLParser):
    """Extract (title, url, snippet) from DDG html/lite result markup."""

    def __init__(self, title_class: str, snippet_class: str) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._title_class = title_class
        self._snippet_class = snippet_class
        self._mode = ""
        self._buffer: list[str] = []
        self._href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = attributes.get("class") or ""
        if tag == "a" and self._title_class in classes:
            self._mode = "title"
            self._buffer = []
            self._href = str(attributes.get("href") or "")
        elif self._snippet_class in classes:
            self._mode = "snippet"
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._mode:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._mode == "title" and tag == "a":
            title = " ".join("".join(self._buffer).split())
            if title:
                self.results.append(
                    {"title": title, "url": _decode_ddg_url(self._href), "snippet": ""}
                )
            self._mode = ""
        elif self._mode == "snippet" and tag in ("a", "td", "div"):
            snippet = " ".join("".join(self._buffer).split())
            if self.results and snippet:
                self.results[-1]["snippet"] = snippet
            self._mode = ""


def _parse_ddg_html(markup: str) -> list[dict[str, str]]:
    return _parse_results(markup, "result__a", "result__snippet")


def _parse_ddg_lite(markup: str) -> list[dict[str, str]]:
    return _parse_results(markup, "result-link", "result-snippet")


def _parse_results(
    markup: str, title_class: str, snippet_class: str
) -> list[dict[str, str]]:
    parser = _ResultParser(title_class, snippet_class)
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed HTML should not crash the agent
        pass
    return parser.results


def _decode_ddg_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlsplit(href)
    if "uddg=" in parsed.query:
        target = urllib.parse.parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return target
    return href


# ---------------------------------------------------------------------------
# HTML -> readable text
# ---------------------------------------------------------------------------

_BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "header", "footer", "pre", "blockquote", "table",
}
_SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "iframe"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title:
            self.title_parts.append(data)
        else:
            self.parts.append(data)

    def text(self) -> tuple[str, str]:
        title = " ".join("".join(self.title_parts).split())
        raw = html.unescape("".join(self.parts))
        raw = re.sub(r"[ \t\f\v]+", " ", raw)
        raw = re.sub(r"\n\s*\n+", "\n\n", raw)
        return title, raw.strip()


def _html_to_text(markup: str) -> tuple[str, str]:
    extractor = _TextExtractor()
    try:
        extractor.feed(markup)
        extractor.close()
    except Exception:  # noqa: BLE001
        return "", markup
    return extractor.text()
