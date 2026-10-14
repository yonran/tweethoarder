"""X session cookies in their own macOS Keychain item.

Reading Chrome's cookies needs the "Chrome Safe Storage" key, which decrypts
every cookie in the profile. For unattended runs, store just the X cookies
under service "tweethoarder" instead (one generic password per cookie name).
Items are created by /usr/bin/security, which makes it the item's trusted app,
so that same binary reads them back without a prompt while the login keychain
is unlocked.
"""

import subprocess
import sys

SERVICE = "tweethoarder"
COOKIE_NAMES = ("auth_token", "ct0", "twid")
SECURITY = "/usr/bin/security"
ITEM_NOT_FOUND = 44  # errSecItemNotFound, as security(1)'s exit status


class KeychainError(Exception):
    """Raised when the keychain can't be read or written."""


def read_keychain_cookies() -> dict[str, str]:
    """Return the X cookies stored in the keychain (empty off macOS)."""
    if sys.platform != "darwin":
        return {}
    cookies: dict[str, str] = {}
    for name in COOKIE_NAMES:
        proc = subprocess.run(
            [SECURITY, "find-generic-password", "-s", SERVICE, "-a", name, "-w"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode == ITEM_NOT_FOUND:
            continue
        if proc.returncode != 0:
            raise KeychainError(
                f"security find-generic-password -s {SERVICE} -a {name} failed "
                f"(exit {proc.returncode}): {proc.stderr.strip()}"
            )
        cookies[name] = proc.stdout.rstrip("\n")
    return cookies


def _quote(value: str) -> str:
    if any(c in value for c in '"\\\n\r'):
        raise KeychainError("cookie value contains characters security -i can't quote")
    return f'"{value}"'


def store_keychain_cookies(cookies: dict[str, str]) -> None:
    """Replace the keychain items for the given cookies.

    Delete-then-add rather than `add -U`: updating changes the item's access
    list, which macOS refuses without a prompt. Values go to `security -i` on
    stdin so they never appear in argv (`ps`).
    """
    if sys.platform != "darwin":
        raise KeychainError("the keychain cookie store is macOS-only")
    names = [name for name in COOKIE_NAMES if cookies.get(name)]
    for name in names:
        proc = subprocess.run(
            [SECURITY, "delete-generic-password", "-s", SERVICE, "-a", name],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode not in (0, ITEM_NOT_FOUND):
            raise KeychainError(
                f"security delete-generic-password -a {name} failed: {proc.stderr.strip()}"
            )
    commands = "".join(
        f"add-generic-password -s {SERVICE} -a {name} -l {_quote(f'{SERVICE} {name}')}"
        f" -w {_quote(cookies[name])}\n"
        for name in names
    )
    proc = subprocess.run(
        [SECURITY, "-i"], input=commands, capture_output=True, text=True, timeout=60
    )
    # security -i exits 0 even when a command fails; errors go to stderr.
    if proc.returncode != 0 or proc.stderr.strip():
        raise KeychainError(f"security add-generic-password failed: {proc.stderr.strip()}")
