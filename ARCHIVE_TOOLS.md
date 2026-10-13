# Thread-context + offline-viewer tools

Extra scripts layered on top of TweetHoarder to do two things its built-in
`sync` / `export html` don't:

1. **Capture full conversation context** for the tweets you saved — the
   **ancestor chain above** a reply (any author) and the **reply tree below**
   your own tweets/bookmarks. TweetHoarder's HTML export only renders an
   author's *self-reply* thread, so cross-author parents are missing.
2. **Render a self-contained, Twitter-style offline archive** with all media
   **downloaded locally**, so nothing breaks when X deletes media or blocks
   hot-linking.

All of these read the same TweetHoarder DB
(`~/.local/share/tweethoarder/tweethoarder.db`) and need the same auth as
TweetHoarder.

## Auth

Set your X session cookies (DevTools → Application → Cookies on x.com), same as
TweetHoarder:

```bash
export TWITTER_AUTH_TOKEN="<auth_token cookie>"
export TWITTER_CT0="<ct0 cookie>"
export TWITTER_TWID="u%3D<your numeric user id>"   # raw twid cookie value
```

`auth_token` is long-lived (weeks/months while logged in); `ct0` rotates more
often. If calls start returning **401/403**, re-grab both. Rate-limit
**timeouts are not an auth problem** (see below).

## Typical workflow

```bash
# 1. Sync your collections (TweetHoarder itself)
uv run tweethoarder sync tweets --all
uv run tweethoarder sync reposts --all
uv run tweethoarder sync replies --all
uv run tweethoarder sync likes --all
uv run tweethoarder sync bookmarks --all

# 2. Fetch conversation context (ancestors + downstream). Resumable.
uv run python fetch_conversations.py has_parent tweet reply bookmark

# 2b. Fetch full text of X Articles (long-form posts) into the `articles` table.
#     Timelines only return a link/title; the body needs TweetDetail. Resumable.
uv run python fetch_articles.py like bookmark

# 3. Download all media locally (images, posters, low-res videos, avatars)
uv run python download_media.py

# 4. Build the offline viewer  ->  ~/twitter-archive/index.html
uv run python build_archive.py
```

> **Order matters:** always run `download_media.py` to completion **before**
> `build_archive.py`. The builder points at a local file if it exists, else
> falls back to the live CDN URL — so building mid-download bakes in CDN URLs
> for not-yet-downloaded media.

## Scripts

| File | Purpose |
|------|---------|
| `media_utils.py` | Shared: extract media (image/poster/lowest-bitrate mp4) from a tweet's `raw_json`; map any media URL to a deterministic local filename. Imported by the downloader and builder so they agree on paths. |
| `fetch_conversations.py` | Fetch the full conversation (ancestors + reply tree) for a set of focal tweets via X's `TweetDetail` endpoint in **conversation mode**. Resumable + per-call timeout. |
| `fetch_articles.py` | Fetch the full body of X Articles linked from saved tweets (incl. quote tweets of an article) into an `articles` table (`title`, `plain_text`, Draft.js `content_state_json`) via `TweetDetail` with the article field toggles. Skips already-fetched (`--refetch` to redo). Search: `select tweet_id, title from articles where plain_text like '%...%'`. |
| `fetch_ancestors.py` | Older/narrower variant: conversation-fetch every *interacted reply* (to pull ancestors). Superseded by `fetch_conversations.py has_parent`; kept for reference. |
| `download_media.py` | Download every referenced image, video poster, low-res mp4, and author avatar into `~/twitter-archive/media/`. Skips existing files (safe to re-run). |
| `build_archive.py` | Render `~/twitter-archive/index.html`: per-collection tabs, each saved tweet shown inside its real ancestor chain + replies, media served from local files (CDN fallback). |

### `fetch_conversations.py` selectors

```bash
uv run python fetch_conversations.py SELECTOR [SELECTOR ...]
```

- `has_parent` — every interacted tweet that is itself a reply (→ pulls ancestors)
- `tweet` `reply` `bookmark` `like` `repost` — every tweet in that collection
  (→ pulls the reply tree below it)

Progress is checkpointed to `conversations_done.txt`; re-running skips completed
focal tweets, so an interrupted/throttled run just resumes.

## Gotchas

- **Conversation vs thread mode:** these scripts use `mode="conversation"` so
  ancestors written by *other* people are kept. TweetHoarder's default `thread`
  mode keeps only the focal author's self-chain and would drop them.
- **Rate limits:** X throttles `TweetDetail` — after ~150 calls in a window it
  starts *stalling every request* (the per-call 30s timeout fires). This is not
  an auth failure. Stop, wait ~30 min, and re-run; the checkpoint resumes.
- **Video size:** full-res variants can be ~150 MB each, so the downloader takes
  the **lowest-bitrate** mp4 (~1–3 MB). A full archive of media is ~1–2 GB.
- **Likes downstream** is intentionally not part of the default run (1.5k+
  conversations = big rate-limit risk). Use `fetch_conversations.py like` in
  throttled chunks if you want it.

## Output

- DB: `~/.local/share/tweethoarder/tweethoarder.db`
- Media: `~/twitter-archive/media/` (`<sha1>.<ext>`)
- Viewer: `~/twitter-archive/index.html` (open with `file://`, fully offline)

To browse the DB directly: `uvx --from datasette datasette ~/.local/share/tweethoarder/tweethoarder.db`
