"""Fetch the full body of X Articles (long-form posts) linked from saved tweets
into an `articles` table in the TweetHoarder SQLite DB, so they're searchable.

Timeline endpoints (likes/bookmarks/...) return at most an article's title and
preview text -- and lately not even that, just an x.com/i/article/ link. The
body only comes back from TweetDetail with fieldToggles
withArticlePlainText / withArticleRichContentState enabled.

Robust + resumable: skips tweets already in `articles`, per-call timeout.

Usage (from repo dir; auth same as TweetHoarder):
    uv run python fetch_articles.py [--refetch] [SELECTOR ...]

Selectors: like bookmark tweet reply repost (default: like bookmark)

Search afterwards, e.g.:
    sqlite3 ~/.local/share/tweethoarder/tweethoarder.db \
      "select tweet_id, title from articles where plain_text like '%public bank%'"
"""

import asyncio
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode


from tweethoarder.auth.cookies import resolve_cookies
from tweethoarder.client.base import TwitterClient
from tweethoarder.client.features import build_tweet_detail_features
from tweethoarder.client.timelines import TWITTER_API_BASE, fetch_single_page
from tweethoarder.config import get_config_dir
from tweethoarder.query_ids.healing import make_api_client
from tweethoarder.query_ids.store import QueryIdStore, get_query_id_with_fallback

DB = Path.home() / ".local/share/tweethoarder/tweethoarder.db"
DELAY_S = 0.8
PER_CALL_TIMEOUT = 30.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    tweet_id TEXT PRIMARY KEY,
    article_id TEXT,
    title TEXT,
    preview_text TEXT,
    plain_text TEXT,
    content_state_json TEXT,
    raw_json TEXT,
    fetched_at TEXT NOT NULL
)
"""


def article_tweet_ids(con: sqlite3.Connection, coll_types: list[str], refetch: bool) -> list[str]:
    ph = ",".join("?" for _ in coll_types)
    skip = (
        ""
        if refetch
        else "AND t.id NOT IN (SELECT tweet_id FROM articles WHERE plain_text IS NOT NULL)"
    )
    return [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT t.id FROM collections c JOIN tweets t ON t.id = c.tweet_id "
            f"WHERE c.collection_type IN ({ph}) "
            "AND (t.urls_json LIKE '%/i/article/%' OR t.raw_json LIKE '%\"article_results\"%' "
            "     OR t.raw_json LIKE '%x.com/i/article/%') "
            f"{skip} ORDER BY t.id DESC",
            coll_types,
        )
    ]


def build_url(query_id: str, tweet_id: str) -> str:
    variables = {
        "focalTweetId": tweet_id,
        "withCommunity": True,
        "withVoice": True,
        "withBirdwatchNotes": True,
        "includePromotedContent": False,
    }
    field_toggles = {
        "withArticlePlainText": True,
        "withArticleRichContentState": True,
        "withArticleSummaryText": True,
        "withAuxiliaryUserLabels": False,
        "withGrokAnalyze": False,
        "withDisallowedReplyControls": False,
    }
    params = urlencode(
        {
            "variables": json.dumps(variables, separators=(",", ":")),
            "features": json.dumps(build_tweet_detail_features(), separators=(",", ":")),
            "fieldToggles": json.dumps(field_toggles, separators=(",", ":")),
        }
    )
    return f"{TWITTER_API_BASE}/{query_id}/TweetDetail?{params}"


def find_article(node: Any, tweet_id: str) -> dict[str, Any] | None:
    """Return article_results.result of the tweet whose rest_id == tweet_id."""
    if isinstance(node, dict):
        if node.get("rest_id") == tweet_id and "article" in node:
            res = (node["article"].get("article_results") or {}).get("result")
            if res:
                return res
        for v in node.values():
            found = find_article(v, tweet_id)
            if found:
                return found
    elif isinstance(node, list):
        for v in node:
            found = find_article(v, tweet_id)
            if found:
                return found
    return None


def plain_text_of(art: dict[str, Any]) -> str | None:
    if art.get("plain_text"):
        return art["plain_text"]
    blocks = (art.get("content_state") or {}).get("blocks") or []
    text = "\n".join(b.get("text", "") for b in blocks)
    return text or None


async def main() -> int:
    args = sys.argv[1:]
    refetch = "--refetch" in args
    coll_types = [a for a in args if not a.startswith("--")] or ["like", "bookmark"]

    con = sqlite3.connect(DB)
    con.execute(SCHEMA)
    targets = article_tweet_ids(con, coll_types, refetch)
    print(f"selectors={coll_types}  targets={len(targets)}", flush=True)

    client = TwitterClient(cookies=resolve_cookies())
    query_id = get_query_id_with_fallback(
        QueryIdStore(get_config_dir() / "query-ids-cache.json"), "TweetDetail"
    )
    saved = errors = 0
    async with make_api_client(client.get_base_headers(), timeout=PER_CALL_TIMEOUT) as http:
        for i, tid in enumerate(targets, 1):
            try:
                resp = await asyncio.wait_for(
                    fetch_single_page(http, build_url(query_id, tid)), PER_CALL_TIMEOUT
                )
                # A quote tweet of an article carries the article on the quoted tweet.
                art = find_article(resp, tid)
                if not art:
                    row = con.execute(
                        "SELECT quoted_tweet_id FROM tweets WHERE id = ?", (tid,)
                    ).fetchone()
                    if row and row[0]:
                        art = find_article(resp, row[0])
                if not art:
                    print(f"[{i}/{len(targets)}] {tid} -> no article in response", flush=True)
                    errors += 1
                    continue
                body = plain_text_of(art)
                con.execute(
                    "INSERT OR REPLACE INTO articles VALUES (?,?,?,?,?,?,?,?)",
                    (
                        tid,
                        art.get("rest_id"),
                        art.get("title"),
                        art.get("preview_text"),
                        body,
                        json.dumps(art.get("content_state")) if art.get("content_state") else None,
                        json.dumps(art),
                        datetime.now(UTC).isoformat(),
                    ),
                )
                con.commit()
                saved += 1
                print(
                    f"[{i}/{len(targets)}] {tid} -> {art.get('title')!r} ({len(body or '')} chars)",
                    flush=True,
                )
            except Exception as e:
                errors += 1
                print(f"[{i}/{len(targets)}] {tid} -> ERROR {type(e).__name__}: {e}", flush=True)
            await asyncio.sleep(DELAY_S)
    con.close()
    print(f"DONE. articles saved: {saved}; errors: {errors}", flush=True)
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
