"""Tests for the macOS Keychain cookie store."""

import subprocess
from typing import Any

import pytest
from pytest import MonkeyPatch


def _fake_run(results: dict[str, tuple[int, str]], calls: list[dict[str, Any]]) -> Any:
    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"args": args, **kwargs})
        if args[1] in ("-i", "delete-generic-password"):
            return subprocess.CompletedProcess(args, 0, "", "")
        code, out = results.get(args[args.index("-a") + 1], (44, ""))
        return subprocess.CompletedProcess(args, code, out, "" if code in (0, 44) else "boom")

    return run


def test_read_keychain_cookies_skips_missing_items(monkeypatch: MonkeyPatch) -> None:
    """Missing items (exit 44) are skipped; found values lose the trailing newline."""
    from tweethoarder.auth import keychain

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(keychain.sys, "platform", "darwin")
    monkeypatch.setattr(
        keychain.subprocess,
        "run",
        _fake_run({"auth_token": (0, "tok\n"), "ct0": (0, "csrf\n")}, calls),
    )

    assert keychain.read_keychain_cookies() == {"auth_token": "tok", "ct0": "csrf"}
    assert calls[0]["args"][:4] == [
        "/usr/bin/security",
        "find-generic-password",
        "-s",
        "tweethoarder",
    ]


def test_read_keychain_cookies_raises_on_other_errors(monkeypatch: MonkeyPatch) -> None:
    """A locked keychain or denied access is an error, not 'no cookies'."""
    from tweethoarder.auth import keychain

    monkeypatch.setattr(keychain.sys, "platform", "darwin")
    monkeypatch.setattr(keychain.subprocess, "run", _fake_run({"auth_token": (51, "")}, []))

    with pytest.raises(keychain.KeychainError, match="exit 51"):
        keychain.read_keychain_cookies()


def test_read_keychain_cookies_is_empty_off_macos(monkeypatch: MonkeyPatch) -> None:
    from tweethoarder.auth import keychain

    monkeypatch.setattr(keychain.sys, "platform", "linux")
    assert keychain.read_keychain_cookies() == {}


def test_store_keychain_cookies_passes_values_on_stdin(monkeypatch: MonkeyPatch) -> None:
    """Secrets go to `security -i` on stdin, never in argv."""
    from tweethoarder.auth import keychain

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(keychain.sys, "platform", "darwin")
    monkeypatch.setattr(keychain.subprocess, "run", _fake_run({}, calls))

    keychain.store_keychain_cookies({"auth_token": "SECRET1", "ct0": "SECRET2"})

    *deletes, call = calls
    assert [d["args"][-1] for d in deletes] == ["auth_token", "ct0"]
    assert call["args"] == ["/usr/bin/security", "-i"]
    assert all("SECRET" not in " ".join(c["args"]) for c in calls)
    assert '-a auth_token -l "tweethoarder auth_token" -w "SECRET1"' in call["input"]
    assert "-a twid" not in call["input"]


def test_store_keychain_cookies_rejects_unquotable_values(monkeypatch: MonkeyPatch) -> None:
    from tweethoarder.auth import keychain

    monkeypatch.setattr(keychain.sys, "platform", "darwin")
    with pytest.raises(keychain.KeychainError):
        keychain.store_keychain_cookies({"auth_token": 'a"b', "ct0": "c"})


def test_resolve_cookies_uses_keychain_source(monkeypatch: MonkeyPatch, tmp_path: Any) -> None:
    """The default source list tries the keychain first."""
    from tweethoarder.auth import cookies

    monkeypatch.delenv("TWITTER_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TWITTER_CT0", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(cookies, "read_keychain_cookies", lambda: {"auth_token": "kt", "ct0": "kc"})

    assert cookies.resolve_cookies(home_dir=tmp_path) == {"auth_token": "kt", "ct0": "kc"}
