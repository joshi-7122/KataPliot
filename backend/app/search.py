"""Pluggable web search + safe page fetching."""
from __future__ import annotations

import asyncio
import html
import ipaddress
import logging
import re
import socket
from abc import ABC, abstractmethod
from urllib.parse import urlparse

import httpx

from .config import Settings

log = logging.getLogger("katapilot.search")


class SearchError(RuntimeError):
    pass


class SearchHit(dict):
    """{title, url, snippet}"""


class SearchProvider(ABC):
    name: str

    @abstractmethod
    async def search(self, query: str, n: int) -> list[SearchHit]:
        ...


class _HTTPSearch(SearchProvider):
    async def _request(self, method: str, url: str, **kw) -> dict:
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=20) as c:
                    r = await c.request(method, url, **kw)
                if r.status_code == 429 and attempt < 2:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                if r.status_code >= 400:
                    raise SearchError(f"{self.name} search failed: HTTP {r.status_code} {r.text[:200]}")
                return r.json()
            except httpx.HTTPError as e:
                if attempt == 2:
                    raise SearchError(f"{self.name} search network error: {e}") from e
                await asyncio.sleep(1.5)
        raise SearchError(f"{self.name} search rate-limited")


class TavilySearch(_HTTPSearch):
    name = "tavily"

    def __init__(self, key: str):
        self.key = key

    async def search(self, query, n):
        data = await self._request("POST", "https://api.tavily.com/search",
                                   json={"query": query, "max_results": n, "search_depth": "basic"},
                                   headers={"Authorization": f"Bearer {self.key}"})
        return [SearchHit(title=r.get("title", ""), url=r.get("url", ""), snippet=r.get("content", "")) for r in data.get("results", [])]


class BraveSearch(_HTTPSearch):
    name = "brave"

    def __init__(self, key: str):
        self.key = key

    async def search(self, query, n):
        data = await self._request("GET", "https://api.search.brave.com/res/v1/web/search",
                                   params={"q": query, "count": n},
                                   headers={"X-Subscription-Token": self.key, "Accept": "application/json"})
        return [SearchHit(title=r.get("title", ""), url=r.get("url", ""), snippet=_strip(r.get("description", "")))
                for r in data.get("web", {}).get("results", [])]


class SerperSearch(_HTTPSearch):
    name = "serper"

    def __init__(self, key: str):
        self.key = key

    async def search(self, query, n):
        data = await self._request("POST", "https://google.serper.dev/search", json={"q": query, "num": n},
                                   headers={"X-API-KEY": self.key})
        return [SearchHit(title=r.get("title", ""), url=r.get("link", ""), snippet=r.get("snippet", "")) for r in data.get("organic", [])]


def make_search(s: Settings) -> SearchProvider | None:
    if s.search_provider == "tavily" and s.tavily_api_key:
        return TavilySearch(s.tavily_api_key)
    if s.search_provider == "brave" and s.brave_api_key:
        return BraveSearch(s.brave_api_key)
    if s.search_provider == "serper" and s.serper_api_key:
        return SerperSearch(s.serper_api_key)
    return None


# ---------------------------------------------------------------- page fetching (SSRF-safe)

def _strip(s: str) -> str:
    return re.sub(r"<[^>]+>", "", html.unescape(s or ""))


def _is_public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript|svg|header|footer|nav)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h\d|pre|tr)>", "\n", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


async def fetch_page_text(url: str, max_chars: int = 12000) -> str:
    """Fetch a public web page as plain text. Refuses non-http(s) URLs and private/loopback hosts."""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise SearchError("unsupported URL")
    if not await asyncio.to_thread(_is_public_host, p.hostname):
        raise SearchError("refusing to fetch a non-public host")
    async with httpx.AsyncClient(timeout=15, follow_redirects=False,
                                 headers={"User-Agent": "Mozilla/5.0 (KataPilot research fetcher)"}) as c:
        r = await c.get(url)
        hops = 0
        while r.is_redirect and hops < 3:
            nxt = urlparse(str(r.next_request.url)) if r.next_request else None
            if not nxt or nxt.scheme not in ("http", "https") or not await asyncio.to_thread(_is_public_host, nxt.hostname or ""):
                raise SearchError("redirect to a non-public host blocked")
            r = await c.get(nxt.geturl())
            hops += 1
    if r.status_code >= 400:
        raise SearchError(f"HTTP {r.status_code}")
    if "html" not in r.headers.get("content-type", "") and "text" not in r.headers.get("content-type", ""):
        raise SearchError("not a text page")
    return html_to_text(r.text)[:max_chars]
