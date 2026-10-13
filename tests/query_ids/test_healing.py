"""Tests for self-healing GraphQL query IDs."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import httpx
import pytest

if TYPE_CHECKING:
    from tweethoarder.query_ids.healing import QueryIdHealingTransport
    from tweethoarder.query_ids.store import QueryIdStore


def _transport(
    tmp_path: Path, handler: Callable[[httpx.Request], httpx.Response]
) -> tuple[QueryIdHealingTransport, QueryIdStore]:
    from tweethoarder.query_ids.healing import QueryIdHealingTransport
    from tweethoarder.query_ids.store import QueryIdStore

    store = QueryIdStore(tmp_path / "cache.json")
    store.save({"Likes": "keep_me"})
    return QueryIdHealingTransport(inner=httpx.MockTransport(handler), store=store), store


@pytest.mark.asyncio
async def test_404_retries_with_scraped_id_and_persists_it(tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if "/old_id/" in request.url.path:
            return httpx.Response(404)
        return httpx.Response(200, json={"ok": True})

    transport, store = _transport(tmp_path, handler)
    scrape = AsyncMock(return_value={"TweetDetail": "new_id"})
    with patch("tweethoarder.query_ids.healing.refresh_query_ids", scrape):
        async with httpx.AsyncClient(
            transport=transport, headers={"cookie": "ct0=a; auth_token=b"}
        ) as c:
            r = await c.get("https://x.com/i/api/graphql/old_id/TweetDetail?variables=%7B%7D")

    assert r.status_code == 200
    assert seen == ["/i/api/graphql/old_id/TweetDetail", "/i/api/graphql/new_id/TweetDetail"]
    assert scrape.await_args.kwargs["cookies"] == {"ct0": "a", "auth_token": "b"}
    assert store.get_query_id("TweetDetail") == "new_id"
    assert store.get_query_id("Likes") == "keep_me"  # save merges


@pytest.mark.asyncio
async def test_failed_retry_is_not_persisted(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404 if "/old_id/" in request.url.path else 400)

    transport, store = _transport(tmp_path, handler)
    with patch(
        "tweethoarder.query_ids.healing.refresh_query_ids",
        AsyncMock(return_value={"UserTweets": "new_id"}),
    ):
        async with httpx.AsyncClient(transport=transport) as c:
            r = await c.get("https://x.com/i/api/graphql/old_id/UserTweets")

    assert r.status_code == 400
    assert store.get_query_id("UserTweets") is None


@pytest.mark.asyncio
async def test_scrapes_once_and_ignores_non_graphql_404s(tmp_path: Path) -> None:
    transport, _ = _transport(tmp_path, lambda request: httpx.Response(404))
    scrape = AsyncMock(return_value={})
    with patch("tweethoarder.query_ids.healing.refresh_query_ids", scrape):
        async with httpx.AsyncClient(transport=transport) as c:
            assert (await c.get("https://x.com/i/api/graphql/a/Likes")).status_code == 404
            assert (await c.get("https://x.com/i/api/graphql/b/Bookmarks")).status_code == 404
            assert (await c.get("https://pbs.twimg.com/media/x.jpg")).status_code == 404

    assert scrape.await_count == 1
