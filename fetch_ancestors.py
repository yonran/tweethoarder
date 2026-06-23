"""Fetch the full conversation (including ancestor posts) for every tweet you
interacted with (likes / bookmarks / replies / your own tweets) that is itself a
reply, and store the results in the TweetHoarder SQLite DB.

Ancestors only exist for tweets that are replies, so we target only those
(`in_reply_to_tweet_id` set). One TweetDetail call per focal tweet returns the
whole ancestor chain up to the root. Uses conversation mode so ancestors written
by *other* people are kept (thread mode would drop them).

Run from the repo dir with TWITTER_AUTH_TOKEN / TWITTER_CT0 / TWITTER_TWID set:
    uv run python fetch_ancestors.py
"""

import asyncio
import sqlite3
import sys
from pathlib import Path

from tweethoarder.cli.thread import fetch_thread_async

DB = Path.home() / ".local/share/tweethoarder/tweethoarder.db"
DELAY_S = 0.8  # polite delay between TweetDetail calls


def interacted_replies() -> list[str]:
    con = sqlite3.connect(DB)
    try:
        rows = con.execute(
            "SELECT DISTINCT t.id "
            "FROM collections c JOIN tweets t ON t.id = c.tweet_id "
            "WHERE t.in_reply_to_tweet_id IS NOT NULL AND t.in_reply_to_tweet_id <> ''"
        ).fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]


async def main() -> None:
    ids = interacted_replies()
    print(f"targets: {len(ids)} interacted replies", flush=True)
    saved = 0
    errors = 0
    for i, tid in enumerate(ids, 1):
        try:
            result = await fetch_thread_async(DB, tid, mode="conversation", limit=200)
            n = result.get("tweet_count", 0)
            saved += n
            print(f"[{i}/{len(ids)}] {tid} -> +{n} (cum {saved})", flush=True)
        except Exception as e:  # noqa: BLE001 - keep going on individual failures
            errors += 1
            print(f"[{i}/{len(ids)}] {tid} ERROR {type(e).__name__}: {e}", flush=True)
        await asyncio.sleep(DELAY_S)
    print(f"DONE. conversation tweets saved: {saved}; errors: {errors}", flush=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
