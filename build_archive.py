"""Build a self-contained, thread-aware, Twitter-style HTML archive from the
TweetHoarder DB.

For every saved tweet (likes / bookmarks / your tweets / replies / reposts) it
renders the real conversation around it:
    ancestor chain (root -> ... -> parent, ANY author)  [context]
    the saved tweet                                       [focal, highlighted]
    immediate replies we captured                         [replies]

Media points at locally downloaded files (run download_media.py first); if a
file is missing it falls back to the live Twitter CDN URL.

Run from the repo dir:
    uv run python build_archive.py             # auto-detects your handle from the DB
    uv run python build_archive.py -u alice    # or pass the handle explicitly
Output: ~/twitter-archive/index.html
"""

import argparse
import html
import json
import re
import sqlite3
from pathlib import Path

from media_utils import extract_media, local_name

DB = Path.home() / ".local/share/tweethoarder/tweethoarder.db"
OUT = Path.home() / "twitter-archive"
MEDIA = OUT / "media"
REPLY_CAP = 25
COLLECTIONS = [
    ("tweet", "My Tweets"),
    ("reply", "Replies"),
    ("bookmark", "Bookmarks"),
    ("like", "Likes"),
    ("repost", "Reposts"),
]

BY_ID: dict[str, dict] = {}
CHILDREN: dict[str, list[dict]] = {}


def load() -> None:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    try:
        for r in con.execute("SELECT * FROM tweets"):
            BY_ID[r["id"]] = dict(r)
    finally:
        con.close()
    for t in BY_ID.values():
        p = t.get("in_reply_to_tweet_id")
        if p:
            CHILDREN.setdefault(p, []).append(t)


def src_for(url: str | None) -> str:
    if not url:
        return ""
    f = MEDIA / local_name(url)
    if f.exists() and f.stat().st_size > 0:
        return "media/" + local_name(url)
    return url  # fall back to live CDN


def linkify(text: str | None) -> str:
    t = html.escape(text or "")
    t = re.sub(r"(https?://[^\s]+)", r'<a href="\1" target="_blank" rel="noopener">\1</a>', t)
    t = re.sub(r"(^|\s)@(\w{1,15})", r'\1<a href="https://x.com/\2" target="_blank">@\2</a>', t)
    return t.replace("\n", "<br>")


def render_media(raw: str | None) -> str:
    parts = []
    for m in extract_media(raw):
        if m["video_url"]:
            poster = src_for(m["image_url"])
            parts.append(
                f'<video controls preload="none" poster="{poster}">'
                f'<source src="{src_for(m["video_url"])}" type="video/mp4"></video>'
            )
        elif m["image_url"]:
            parts.append(f'<img loading="lazy" src="{src_for(m["image_url"])}">')
    return f'<div class="media">{"".join(parts)}</div>' if parts else ""


def card(t: dict, role: str, with_quote: bool = True) -> str:
    user = html.escape(t.get("author_username") or "")
    name = html.escape(t.get("author_display_name") or user)
    when = (t.get("created_at") or "")[:16].replace("T", " ")
    avatar = src_for(t.get("author_avatar_url"))
    av = f'<img class="avatar" loading="lazy" src="{avatar}">' if avatar else ""
    quoted = ""
    qid = t.get("quoted_tweet_id")
    if with_quote and qid and qid in BY_ID:
        quoted = f'<div class="quoted">{card(BY_ID[qid], "quote", with_quote=False)}</div>'
    metrics = (
        f'{t.get("reply_count") or 0} 💬 &nbsp; {t.get("retweet_count") or 0} 🔁 &nbsp; '
        f'{t.get("like_count") or 0} ❤'
    )
    link = f'https://x.com/{user or "i"}/status/{t["id"]}'
    return (
        f'<div class="tw {role}">'
        f'<div class="head">{av}<span class="name">{name}</span>'
        f'<span class="user">@{user}</span><span class="dot">·</span>'
        f'<span class="date">{when}</span></div>'
        f'<div class="body">{linkify(t.get("text"))}</div>'
        f"{render_media(t.get('raw_json'))}{quoted}"
        f'<div class="metrics">{metrics}<a class="vx" href="{link}" target="_blank">View on X ↗</a></div>'
        f"</div>"
    )


def conversation_block(focal: dict) -> str:
    parts = []
    chain, cur, seen = [], focal, {focal["id"]}
    while (pid := cur.get("in_reply_to_tweet_id")) and pid in BY_ID and pid not in seen:
        parent = BY_ID[pid]
        chain.append(parent)
        seen.add(pid)
        cur = parent
    for a in reversed(chain):
        parts.append(card(a, "context"))
    if chain:
        parts.append('<div class="ctxnote">↑ context · saved tweet ↓</div>')
    parts.append(card(focal, "focal"))
    kids = sorted(CHILDREN.get(focal["id"], []), key=lambda x: x.get("created_at") or "")
    for k in kids[:REPLY_CAP]:
        if k["id"] != focal["id"]:
            parts.append(card(k, "reply"))
    return f'<div class="conv">{"".join(parts)}</div>'


def collection_focal_ids(con: sqlite3.Connection, ctype: str) -> list[str]:
    rows = con.execute(
        "SELECT t.id FROM collections c JOIN tweets t ON t.id=c.tweet_id "
        "WHERE c.collection_type=? ORDER BY t.created_at DESC",
        (ctype,),
    ).fetchall()
    return [r[0] for r in rows]


CSS = """
:root{--bg:#000;--card:#16181c;--bd:#2f3336;--tx:#e7e9ea;--mut:#71767b;--acc:#1d9bf0;--foc:#1d9bf022;}
body.light{--bg:#fff;--card:#f7f9f9;--bd:#cfd9de;--tx:#0f1419;--mut:#536471;--foc:#1d9bf014;}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.4 -apple-system,Segoe UI,Roboto,sans-serif}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--bd);padding:10px 16px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;z-index:9}
header h1{font-size:18px;margin:0 12px 0 0}
.tab{padding:6px 12px;border:1px solid var(--bd);border-radius:999px;cursor:pointer;color:var(--tx);background:transparent}
.tab.active{background:var(--acc);border-color:var(--acc);color:#fff}
#theme{margin-left:auto;cursor:pointer;border:1px solid var(--bd);border-radius:999px;padding:6px 12px;background:transparent;color:var(--tx)}
main{max-width:620px;margin:0 auto;padding:8px}
section{display:none}section.active{display:block}
.conv{border:1px solid var(--bd);border-radius:14px;margin:14px 0;overflow:hidden}
.tw{padding:12px 14px;border-bottom:1px solid var(--bd)}
.tw:last-child{border-bottom:0}
.tw.context{background:transparent;opacity:.85}
.tw.context .body{color:var(--mut)}
.tw.focal{background:var(--foc);border-left:3px solid var(--acc)}
.tw.reply{padding-left:30px;border-left:2px solid var(--bd)}
.tw.quote{border:1px solid var(--bd);border-radius:12px;margin-top:8px;padding:10px}
.ctxnote{font-size:11px;color:var(--mut);text-align:center;padding:3px;background:var(--card)}
.head{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.avatar{width:36px;height:36px;border-radius:50%;object-fit:cover}
.name{font-weight:700}.user,.dot,.date{color:var(--mut)}
.body{margin:6px 0;white-space:normal;overflow-wrap:anywhere}
.body a{color:var(--acc);text-decoration:none}
.media{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
.media img,.media video{max-width:100%;border-radius:12px;border:1px solid var(--bd);max-height:520px}
.quoted{margin-top:6px}
.metrics{color:var(--mut);font-size:13px;margin-top:8px;display:flex;align-items:center}
.vx{margin-left:auto;color:var(--acc);text-decoration:none}
"""

JS = """
function show(t){document.querySelectorAll('section').forEach(s=>s.classList.toggle('active',s.id==='s-'+t));
document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('active',b.dataset.t===t));location.hash=t;}
document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>show(b.dataset.t));
document.getElementById('theme').onclick=()=>document.body.classList.toggle('light');
show((location.hash||'#bookmark').slice(1));
"""


def detect_handle(con: sqlite3.Connection) -> str | None:
    """Best-guess the archive owner's @handle: the most frequent author among
    your own posts (the 'tweet'/'reply' collections)."""
    row = con.execute(
        "SELECT t.author_username FROM collections c JOIN tweets t ON t.id=c.tweet_id "
        "WHERE c.collection_type IN ('tweet','reply') AND t.author_username <> '' "
        "GROUP BY t.author_username ORDER BY COUNT(*) DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def main(handle: str | None = None) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    load()
    con = sqlite3.connect(DB)
    tabs, sections = [], []
    try:
        if handle is None:
            handle = detect_handle(con)
        for ctype, label in COLLECTIONS:
            ids = collection_focal_ids(con, ctype)
            if not ids:
                continue
            tabs.append(f'<button class="tab" data-t="{ctype}">{label} ({len(ids)})</button>')
            blocks = [conversation_block(BY_ID[i]) for i in ids if i in BY_ID]
            sections.append(f'<section id="s-{ctype}"><h2 style="padding:0 8px">{label}</h2>{"".join(blocks)}</section>')
    finally:
        con.close()
    who = f" — @{html.escape(handle.lstrip('@'))}" if handle else ""
    doc = (
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>Twitter archive{who}</title><style>{CSS}</style></head><body>"
        f"<header><h1>🗃️ Twitter archive{who}</h1>{''.join(tabs)}"
        f"<button id='theme'>◐ theme</button></header>"
        f"<main>{''.join(sections)}</main><script>{JS}</script></body></html>"
    )
    index = OUT / "index.html"
    index.write_text(doc)
    size_mb = index.stat().st_size / 1e6
    print(f"wrote {index} ({size_mb:.1f} MB); handle={handle or '(unknown)'}; tweets in DB: {len(BY_ID)}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build the offline Twitter-style archive viewer.")
    ap.add_argument("-u", "--user", help="X handle for the page title (default: auto-detect from your own posts)")
    main(ap.parse_args().user)
