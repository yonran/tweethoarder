"""Cookie resolution flow with fallbacks."""

import os
import tomllib
from pathlib import Path

from tweethoarder.auth.chrome import (
    chrome_encryption_key,
    extract_chrome_cookies,
    find_chrome_cookies_db,
)
from tweethoarder.auth.firefox import extract_firefox_cookies, find_firefox_cookies_db
from tweethoarder.config import get_config_dir


class CookieError(Exception):
    """Raised when cookie resolution fails."""


def resolve_cookies(home_dir: Path | None = None) -> dict[str, str]:
    """Resolve cookies using priority-based fallback chain."""
    auth_token = os.environ.get("TWITTER_AUTH_TOKEN")
    ct0 = os.environ.get("TWITTER_CT0")
    twid = os.environ.get("TWITTER_TWID")

    if auth_token and ct0:
        result = {"auth_token": auth_token, "ct0": ct0}
        if twid:
            result["twid"] = twid
        return result

    config_path = get_config_dir() / "config.toml"
    auth_data: dict[str, object] = {}
    if config_path.exists():
        with config_path.open("rb") as f:
            auth_data = tomllib.load(f).get("auth", {})
        auth_token = auth_data.get("auth_token")
        ct0 = auth_data.get("ct0")
        twid = auth_data.get("twid")
        if isinstance(auth_token, str) and isinstance(ct0, str) and auth_token and ct0:
            result = {"auth_token": auth_token, "ct0": ct0}
            if isinstance(twid, str) and twid:
                result["twid"] = twid
            return result

    if home_dir is None:
        home_dir = Path.home()
    sources = auth_data.get("cookie_sources", ["firefox", "chrome"])
    chrome_profile = auth_data.get("chrome_profile")
    for source in sources if isinstance(sources, list) else []:
        if source == "firefox":
            cookies = _firefox_cookies(home_dir)
        elif source == "chrome":
            cookies = _chrome_cookies(
                home_dir, chrome_profile if isinstance(chrome_profile, str) else None
            )
        else:
            raise CookieError(f"Unknown cookie source: {source!r}")
        if cookies.get("auth_token") and cookies.get("ct0"):
            return {k: cookies[k] for k in ("auth_token", "ct0", "twid") if k in cookies}

    raise CookieError("No Twitter cookies found")


def _firefox_cookies(home_dir: Path) -> dict[str, str]:
    db = find_firefox_cookies_db(home_dir)
    return extract_firefox_cookies(db) if db else {}


def _chrome_cookies(home_dir: Path, profile: str | None) -> dict[str, str]:
    db = find_chrome_cookies_db(home_dir, profile)
    if not db:
        return {}
    cookies = extract_chrome_cookies(db)
    if cookies.get("auth_token") and cookies.get("ct0"):
        return cookies
    # Values are encrypted (always on macOS): only now touch the keyring.
    key = chrome_encryption_key()
    return extract_chrome_cookies(db, key) if key else cookies
