"""Shared media helpers for the local archive: extract media URLs from a tweet's
raw_json, and map any media URL to a deterministic local filename so the
downloader and the HTML builder always agree on paths.
"""

import hashlib
import json
import os
from typing import Any

_IMG_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
_VID_EXTS = {".mp4"}


def _unwrap(d: dict[str, Any]) -> dict[str, Any]:
    # TweetWithVisibilityResults wraps the real tweet under "tweet"
    if isinstance(d, dict) and isinstance(d.get("tweet"), dict):
        return d["tweet"]
    return d


def extract_media(raw_json: str | None) -> list[dict[str, Any]]:
    """Normalized media for a tweet. Each item:
    {type, image_url (photo or video poster), video_url (lowest-bitrate mp4 or None)}.
    """
    if not raw_json:
        return []
    try:
        d = _unwrap(json.loads(raw_json))
    except Exception:
        return []
    leg = d.get("legacy") or {}
    media = (leg.get("extended_entities") or leg.get("entities") or {}).get("media") or []
    out: list[dict[str, Any]] = []
    for m in media:
        mtype = m.get("type")
        image_url = m.get("media_url_https")
        video_url = None
        if mtype in ("video", "animated_gif"):
            variants = [
                v
                for v in (m.get("video_info") or {}).get("variants", [])
                if v.get("content_type") == "video/mp4" and v.get("url")
            ]
            if variants:
                variants.sort(key=lambda v: v.get("bitrate") or 0)  # smallest first
                video_url = variants[0]["url"]
        if image_url or video_url:
            out.append({"type": mtype, "image_url": image_url, "video_url": video_url})
    return out


def all_media_urls(raw_json: str | None) -> list[str]:
    urls: list[str] = []
    for m in extract_media(raw_json):
        if m["image_url"]:
            urls.append(m["image_url"])
        if m["video_url"]:
            urls.append(m["video_url"])
    return urls


def local_name(url: str) -> str:
    """Deterministic, collision-free local filename for a media URL."""
    base = url.split("?")[0]
    ext = os.path.splitext(base)[1].lower()
    if ext not in _IMG_EXTS | _VID_EXTS:
        ext = ".bin"
    return hashlib.sha1(url.encode()).hexdigest()[:20] + ext
