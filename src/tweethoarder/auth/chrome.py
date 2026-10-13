"""Chrome cookie extraction for TweetHoarder."""

import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

X_HOSTS = (".x.com", "x.com", ".twitter.com", "twitter.com")

# Since Chrome 130 (cookie DB meta version 24) the plaintext of an encrypted
# cookie starts with SHA256(host_key), which must be stripped.
DOMAIN_HASH_DB_VERSION = 24


def extract_chrome_cookies(db_path: Path, key: bytes | None = None) -> dict[str, str]:
    """Extract auth_token, ct0, and twid cookies for x.com from a Chrome Cookies DB.

    Plaintext values are used as-is; encrypted ones are decrypted with `key`
    (skipped if no key). The DB is copied first because a running Chrome
    holds a lock on it.
    """
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "Cookies"
        shutil.copyfile(db_path, copy)
        with sqlite3.connect(copy) as conn:
            try:
                row = conn.execute("SELECT value FROM meta WHERE key = 'version'").fetchone()
                db_version = int(row[0]) if row else 0
            except sqlite3.OperationalError:
                db_version = 0
            placeholders = ",".join("?" for _ in X_HOSTS)
            rows = conn.execute(
                "SELECT host_key, name, value, encrypted_value FROM cookies "
                f"WHERE name IN ('auth_token', 'ct0', 'twid') AND host_key IN ({placeholders})",
                X_HOSTS,
            ).fetchall()

    cookies: dict[str, str] = {}
    # x.com first so it wins over any leftover twitter.com cookies.
    for host_key, name, value, encrypted_value in sorted(rows, key=lambda r: "twitter" in r[0]):
        if name in cookies:
            continue
        if not value and encrypted_value and key:
            value = decrypt_chrome_cookie(
                encrypted_value, key, has_domain_hash=db_version >= DOMAIN_HASH_DB_VERSION
            )
        if value:
            cookies[name] = value
    return cookies


def decrypt_chrome_cookie(encrypted_value: bytes, key: bytes, has_domain_hash: bool = False) -> str:
    """Decrypt a Chrome cookie value using AES-128-CBC."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if encrypted_value[:3] != b"v10":
        return ""

    ciphertext = encrypted_value[3:]
    iv = b" " * 16  # Chrome uses space padding for IV

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded_plaintext = decryptor.update(ciphertext) + decryptor.finalize()

    # Remove PKCS7 padding
    padding_length = padded_plaintext[-1]
    plaintext = padded_plaintext[:-padding_length]
    if has_domain_hash:
        plaintext = plaintext[32:]

    return plaintext.decode("utf-8")


def _derive_key(password: bytes, iterations: int) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(algorithm=hashes.SHA1(), length=16, salt=b"saltysalt", iterations=iterations)
    return kdf.derive(password)


def get_chrome_encryption_key_macos() -> bytes | None:
    """Get Chrome's cookie key from the macOS login Keychain.

    Uses /usr/bin/security (not the Security framework from Python) so the
    Keychain's "Always Allow" grant is tied to a stable binary path rather than
    to whichever Python interpreter runs this; it then works unattended.
    """
    try:
        password = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-w", "-s", "Chrome Safe Storage"],
            capture_output=True,
            check=True,
            timeout=60,
        ).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None
    return _derive_key(password, iterations=1003) if password else None


def get_chrome_encryption_key() -> bytes | None:
    """Get Chrome encryption key from GNOME keyring."""
    import secretstorage

    connection = secretstorage.dbus_init()
    collection = secretstorage.get_default_collection(connection)

    # Search for Chrome Safe Storage item
    items = list(collection.search_items({"application": "chrome"}))
    if not items:
        return None

    password = items[0].get_secret()
    return _derive_key(password, iterations=1)


CHROME_USER_DATA_DIRS = [
    Path(".config") / "google-chrome",  # Linux
    Path("Library") / "Application Support" / "Google" / "Chrome",  # macOS
]


def find_chrome_cookies_db(home_dir: Path, profile: str | None = None) -> Path | None:
    """Find the Chrome Cookies database file for a profile ("Default" if None)."""
    profile_name = profile or "Default"
    for user_data_dir in CHROME_USER_DATA_DIRS:
        cookies_path = home_dir / user_data_dir / profile_name / "Cookies"
        if cookies_path.exists():
            return cookies_path
    return None


def chrome_encryption_key() -> bytes | None:
    """The platform's Chrome cookie key, or None if unavailable."""
    if sys.platform == "darwin":
        return get_chrome_encryption_key_macos()
    try:
        return get_chrome_encryption_key()
    except Exception:
        return None
