#!/bin/bash
# Nightly incremental archive sync (run by the `tweethoarder-sync` launchd agent
# in yondesktop home.nix). Auth = cookies auto-extracted from Chrome.
# Steps keep going on failure so e.g. a TweetDetail rate-limit stall in step 2
# doesn't block the media download / viewer rebuild; exit status = any failure.
set -uo pipefail
cd "$(dirname "$0")"
UV="${UV:-uv}"
rc=0
step() {
  echo "== $* ($(date))"
  "$@" || { echo "!! failed ($?): $*"; rc=1; }
}
step "$UV" run --frozen tweethoarder sync --likes --bookmarks --tweets --reposts --replies
step "$UV" run --frozen python fetch_conversations.py has_parent tweet reply bookmark
step "$UV" run --frozen python fetch_articles.py like bookmark
step "$UV" run --frozen python download_media.py
step "$UV" run --frozen python build_archive.py
# Consistent DB snapshot next to the viewer, so nas-backup (rsync of
# ~/twitter-archive) never copies a half-written live DB. tmp+mv = atomic.
DB="$HOME/.local/share/tweethoarder/tweethoarder.db"
SNAP="$HOME/twitter-archive/tweethoarder.db"
step /usr/bin/sqlite3 "$DB" ".backup '$SNAP.tmp'"
step mv -f "$SNAP.tmp" "$SNAP"
echo "== done rc=$rc ($(date))"
exit $rc
