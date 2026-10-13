"""Fetch the full conversation (ancestors + downstream reply tree) for a chosen
set of focal tweets, into the TweetHoarder SQLite DB. Uses TweetDetail in
conversation mode, so tweets by every participant are kept.

Robust + resumable:
  - per-call timeout so one hung network read can't stall the whole batch
  - records each completed focal id in conversations_done.txt (next to the DB)
    and skips it on re-run (seed this file to avoid redoing work)

Usage (from repo dir, with TWITTER_AUTH_TOKEN / TWITTER_CT0 / TWITTER_TWID set):
    uv run python fetch_conversations.py SELECTOR [SELECTOR ...]

Selectors:
    has_parent          every interacted tweet that is itself a reply
                        (=> pulls the ancestor chain above it)
    tweet reply bookmark like repost
                        every tweet in that collection
                        (=> pulls the reply tree below it, and ancestors if any)

Example: uv run python fetch_conversations.py has_parent tweet reply bookmark
"""

import asyncio
import sqlite3
import sys
from pathlib import Path

from tweethoarder.cli.thread import fetch_thread_async

DB = Path.home() / ".local/share/tweethoarder/tweethoarder.db"
DONE_FILE = DB.with_name("conversations_done.txt")  # beside the DB, not the (maybe read-only) script
DELAY_S = 0.8
PER_CALL_TIMEOUT = 30.0


def load_done() -> set[str]:
    if DONE_FILE.exists():
        return {line.strip() for line in DONE_FILE.read_text().splitlines() if line.strip()}
    return set()


def focal_ids(selectors: list[str]) -> set[str]:
    coll_types = [s for s in selectors if s != "has_parent"]
    ids: set[str] = set()
    con = sqlite3.connect(DB)
    try:
        if coll_types:
            ph = ",".join("?" for _ in coll_types)
            ids.update(
                r[0]
                for r in con.execute(
                    "SELECT DISTINCT t.id FROM collections c JOIN tweets t ON t.id = c.tweet_id "
                    f"WHERE c.collection_type IN ({ph})",
                    coll_types,
                )
            )
        if "has_parent" in selectors:
            ids.update(
                r[0]
                for r in con.execute(
                    "SELECT DISTINCT t.id FROM collections c JOIN tweets t ON t.id = c.tweet_id "
                    "WHERE t.in_reply_to_tweet_id IS NOT NULL AND t.in_reply_to_tweet_id <> ''"
                )
            )
    finally:
        con.close()
    return ids


async def main() -> int:
    selectors = sys.argv[1:] or ["tweet", "reply"]
    done = load_done()
    targets = sorted(focal_ids(selectors) - done)
    print(
        f"selectors={selectors}  targets={len(targets)}  (skipping {len(done)} already done)",
        flush=True,
    )
    saved = 0
    errors = 0
    with DONE_FILE.open("a") as df:
        for i, tid in enumerate(targets, 1):
            try:
                result = await asyncio.wait_for(
                    fetch_thread_async(DB, tid, mode="conversation", limit=200),
                    timeout=PER_CALL_TIMEOUT,
                )
                n = result.get("tweet_count", 0)
                saved += n
                df.write(tid + "\n")
                df.flush()
                print(f"[{i}/{len(targets)}] {tid} -> +{n} (cum {saved})", flush=True)
            except Exception as e:  # noqa: BLE001 - keep going on individual failures
                errors += 1
                print(f"[{i}/{len(targets)}] {tid} ERR {type(e).__name__}: {e}", flush=True)
            await asyncio.sleep(DELAY_S)
    print(f"DONE. conversation tweets saved: {saved}; errors: {errors}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
