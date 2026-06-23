"""Download every image, video poster, low-res video, GIF, and avatar referenced
by tweets in the TweetHoarder DB into a local media/ folder, so the archive is
self-contained and survives X deleting media. Skips files already downloaded,
so it's safe to re-run after more tweets are synced.

Run from the repo dir:  uv run python download_media.py
Output: ~/twitter-archive/media/<sha1>.<ext>
"""

import asyncio
import sqlite3
from pathlib import Path

import httpx

from media_utils import all_media_urls, local_name

DB = Path.home() / ".local/share/tweethoarder/tweethoarder.db"
OUT = Path.home() / "twitter-archive"
MEDIA = OUT / "media"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)
CONCURRENCY = 8


def collect_urls() -> set[str]:
    con = sqlite3.connect(DB)
    urls: set[str] = set()
    try:
        for raw, avatar in con.execute("SELECT raw_json, author_avatar_url FROM tweets"):
            urls.update(all_media_urls(raw))
            if avatar:
                urls.add(avatar)
    finally:
        con.close()
    return urls


async def fetch(client: httpx.AsyncClient, sem: asyncio.Semaphore, url: str, c: dict) -> None:
    dest = MEDIA / local_name(url)
    if dest.exists() and dest.stat().st_size > 0:
        c["skip"] += 1
        return
    async with sem:
        try:
            r = await client.get(url)
            if r.status_code == 200 and r.content:
                dest.write_bytes(r.content)
                c["ok"] += 1
                c["bytes"] += len(r.content)
            else:
                c["fail"] += 1
        except Exception:
            c["fail"] += 1
    done = c["ok"] + c["fail"] + c["skip"]
    if done % 200 == 0:
        print(f"  {done}/{c['total']}  ok={c['ok']} fail={c['fail']} skip={c['skip']} "
              f"{c['bytes']/1e6:.0f}MB", flush=True)


async def main() -> None:
    MEDIA.mkdir(parents=True, exist_ok=True)
    urls = sorted(collect_urls())
    c = {"ok": 0, "fail": 0, "skip": 0, "bytes": 0, "total": len(urls)}
    print(f"media urls to fetch: {len(urls)}", flush=True)
    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(
        headers={"User-Agent": UA, "Referer": "https://x.com/"},
        follow_redirects=True,
        timeout=60,
    ) as client:
        await asyncio.gather(*(fetch(client, sem, u, c) for u in urls))
    print(f"DONE ok={c['ok']} fail={c['fail']} skip={c['skip']} size={c['bytes']/1e6:.1f}MB", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
