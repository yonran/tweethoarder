"""Self-healing GraphQL query IDs.

X rotates the query ID in /i/api/graphql/<queryId>/<Operation> URLs. Old IDs
keep working for a while after the web client moves on, then start returning
404. This transport catches that 404, re-scrapes current IDs from X's JS
bundles (once per process), and retries the request with the new ID. The new
ID is persisted only if the retry succeeds, so a rotated ID that also needs
different features/variables can't poison the cache.

Every API client should be created with `make_api_client` so all operations
(not just the ones with bespoke refresh callbacks) heal the same way.
"""

import asyncio
import os
import re
from typing import Any

import httpx
from loguru import logger

from tweethoarder.config import get_config_dir

from .scraper import refresh_query_ids
from .store import QueryIdStore

GRAPHQL_PATH = re.compile(r"^/i/api/graphql/(?P<query_id>[^/]+)/(?P<operation>[^/?]+)$")


def _cookies_from_header(cookie_header: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in cookie_header.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep:
            cookies[name] = value
    return cookies


def _env_proxy() -> str | None:
    """Proxy from the environment (a custom transport bypasses httpx's own lookup)."""
    for var in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        if os.environ.get(var):
            return os.environ[var]
    return None


class QueryIdHealingTransport(httpx.AsyncBaseTransport):
    """Retry 404'd GraphQL requests once with a freshly scraped query ID."""

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport | None = None,
        store: QueryIdStore | None = None,
    ) -> None:
        self._inner = inner or httpx.AsyncHTTPTransport(proxy=_env_proxy())
        self._store = store or QueryIdStore(get_config_dir() / "query-ids-cache.json")
        self._lock = asyncio.Lock()
        self._scraped: dict[str, str] | None = None  # one scrape per process

    async def _scrape(self, request: httpx.Request) -> dict[str, str]:
        async with self._lock:
            if self._scraped is None:
                cookies = _cookies_from_header(request.headers.get("cookie", ""))
                try:
                    async with httpx.AsyncClient() as client:
                        self._scraped = await refresh_query_ids(client, cookies=cookies)
                except Exception as e:
                    logger.warning(f"query ID refresh failed: {e!r}")
                    self._scraped = {}
            return self._scraped

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        match = GRAPHQL_PATH.match(request.url.path)
        if response.status_code != 404 or not match:
            return response

        operation, old_id = match["operation"], match["query_id"]
        new_id = (await self._scrape(request)).get(operation)
        if not new_id or new_id == old_id:
            logger.warning(f"{operation}: 404 with query ID {old_id}; no newer ID found")
            return response

        await response.aclose()
        retry = httpx.Request(
            request.method,
            request.url.copy_with(path=f"/i/api/graphql/{new_id}/{operation}"),
            headers=request.headers,
            content=request.content,
            extensions=request.extensions,
        )
        retry_response = await self._inner.handle_async_request(retry)
        if retry_response.status_code < 400:
            logger.info(f"{operation}: query ID {old_id} -> {new_id}")
            self._store.save({operation: new_id})
        else:
            logger.warning(
                f"{operation}: retry with {new_id} got HTTP {retry_response.status_code}"
            )
        return retry_response

    async def aclose(self) -> None:
        await self._inner.aclose()


def make_api_client(headers: dict[str, str], **kwargs: Any) -> httpx.AsyncClient:
    """Return an httpx client for X's GraphQL API with self-healing query IDs."""
    return httpx.AsyncClient(headers=headers, transport=QueryIdHealingTransport(), **kwargs)
