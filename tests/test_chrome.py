"""Tests for Chrome cookie extraction."""

import sqlite3
from pathlib import Path


def _create_test_chrome_cookies_db(db_path: Path, cookies: list[tuple[str, str, str]]) -> None:
    """Create a test Chrome cookies database with given cookies (unencrypted)."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE cookies (
            creation_utc INTEGER NOT NULL,
            host_key TEXT NOT NULL,
            top_frame_site_key TEXT NOT NULL,
            name TEXT NOT NULL,
            value TEXT NOT NULL,
            encrypted_value BLOB NOT NULL,
            path TEXT NOT NULL,
            expires_utc INTEGER NOT NULL,
            is_secure INTEGER NOT NULL,
            is_httponly INTEGER NOT NULL,
            last_access_utc INTEGER NOT NULL,
            has_expires INTEGER NOT NULL,
            is_persistent INTEGER NOT NULL,
            priority INTEGER NOT NULL,
            samesite INTEGER NOT NULL,
            source_scheme INTEGER NOT NULL,
            source_port INTEGER NOT NULL,
            last_update_utc INTEGER NOT NULL,
            source_type INTEGER NOT NULL,
            has_cross_site_ancestor INTEGER NOT NULL
        )
    """)
    for name, value, host in cookies:
        conn.execute(
            """INSERT INTO cookies (
                creation_utc, host_key, top_frame_site_key, name, value, encrypted_value,
                path, expires_utc, is_secure, is_httponly, last_access_utc, has_expires,
                is_persistent, priority, samesite, source_scheme, source_port,
                last_update_utc, source_type, has_cross_site_ancestor
            ) VALUES (?, ?, '', ?, ?, ?, '/', 0, 1, 1, 0, 1, 1, 1, 0, 2, 443, 0, 0, 0)""",
            (0, host, name, value, b""),
        )
    conn.commit()
    conn.close()


def test_extract_chrome_cookies_is_importable() -> None:
    """extract_chrome_cookies function should be importable."""
    from tweethoarder.auth.chrome import extract_chrome_cookies

    assert callable(extract_chrome_cookies)


def test_find_chrome_cookies_db_is_importable() -> None:
    """find_chrome_cookies_db function should be importable."""
    from tweethoarder.auth.chrome import find_chrome_cookies_db

    assert callable(find_chrome_cookies_db)


def test_find_chrome_cookies_db_finds_default_profile(tmp_path: Path) -> None:
    """Should find Cookies file in Default profile."""
    from tweethoarder.auth.chrome import find_chrome_cookies_db

    chrome_dir = tmp_path / ".config" / "google-chrome" / "Default"
    chrome_dir.mkdir(parents=True)
    cookies_file = chrome_dir / "Cookies"
    cookies_file.touch()

    result = find_chrome_cookies_db(tmp_path)

    assert result == cookies_file


def test_find_chrome_cookies_db_finds_named_profile(tmp_path: Path) -> None:
    """Should find Cookies file in named profile when specified."""
    from tweethoarder.auth.chrome import find_chrome_cookies_db

    chrome_dir = tmp_path / ".config" / "google-chrome" / "Profile 2"
    chrome_dir.mkdir(parents=True)
    cookies_file = chrome_dir / "Cookies"
    cookies_file.touch()

    result = find_chrome_cookies_db(tmp_path, profile="Profile 2")

    assert result == cookies_file


def test_extract_chrome_cookies_returns_unencrypted_values(tmp_path: Path) -> None:
    """Should extract cookies when values are unencrypted."""
    from tweethoarder.auth.chrome import extract_chrome_cookies

    db_path = tmp_path / "Cookies"
    _create_test_chrome_cookies_db(
        db_path,
        [
            ("auth_token", "test_auth_token_value", ".x.com"),
            ("ct0", "test_ct0_value", ".x.com"),
            ("twid", "test_twid_value", ".x.com"),
        ],
    )

    cookies = extract_chrome_cookies(db_path)

    assert cookies["auth_token"] == "test_auth_token_value"
    assert cookies["ct0"] == "test_ct0_value"
    assert cookies["twid"] == "test_twid_value"


def test_decrypt_chrome_cookie_is_importable() -> None:
    """decrypt_chrome_cookie function should be importable."""
    from tweethoarder.auth.chrome import decrypt_chrome_cookie

    assert callable(decrypt_chrome_cookie)


def test_decrypt_chrome_cookie_decrypts_v10_value() -> None:
    """Should decrypt v10 encrypted cookie value."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    from tweethoarder.auth.chrome import decrypt_chrome_cookie

    # Create test encrypted value using known key
    key = b"0" * 16  # 16-byte key for AES-128
    iv = b" " * 16  # 16-byte IV (Chrome uses space padding)
    plaintext = b"test_cookie_value"

    # Pad to AES block size
    padding_length = 16 - (len(plaintext) % 16)
    padded_plaintext = plaintext + bytes([padding_length] * padding_length)

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded_plaintext) + encryptor.finalize()

    # Chrome v10 format: b"v10" + ciphertext
    encrypted_value = b"v10" + ciphertext

    result = decrypt_chrome_cookie(encrypted_value, key)

    assert result == "test_cookie_value"


def test_get_chrome_encryption_key_is_importable() -> None:
    """get_chrome_encryption_key function should be importable."""
    from tweethoarder.auth.chrome import get_chrome_encryption_key

    assert callable(get_chrome_encryption_key)


def test_get_chrome_encryption_key_returns_derived_key() -> None:
    """Should derive key from GNOME keyring password using PBKDF2."""
    from unittest.mock import MagicMock, patch

    from tweethoarder.auth.chrome import get_chrome_encryption_key

    # Mock secretstorage to return a known password
    mock_item = MagicMock()
    mock_item.get_secret.return_value = b"test_password"

    mock_collection = MagicMock()
    mock_collection.search_items.return_value = [mock_item]

    mock_connection = MagicMock()

    with (
        patch("secretstorage.dbus_init", return_value=mock_connection),
        patch("secretstorage.get_default_collection", return_value=mock_collection),
    ):
        key = get_chrome_encryption_key()

    # Key should be 16 bytes (AES-128)
    assert key is not None
    assert len(key) == 16


def _encrypt_v10(plaintext: bytes, key: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    pad = 16 - len(plaintext) % 16
    encryptor = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).encryptor()
    return b"v10" + encryptor.update(plaintext + bytes([pad] * pad)) + encryptor.finalize()


def test_decrypt_chrome_cookie_strips_domain_hash() -> None:
    """Chrome 130+ (DB version 24) prefixes SHA256(host_key) to the plaintext."""
    import hashlib

    from tweethoarder.auth.chrome import decrypt_chrome_cookie

    key = b"k" * 16
    encrypted = _encrypt_v10(hashlib.sha256(b".x.com").digest() + b"tok", key)

    assert decrypt_chrome_cookie(encrypted, key, has_domain_hash=True) == "tok"


def test_extract_chrome_cookies_decrypts_encrypted_x_cookies(tmp_path: Path) -> None:
    """Encrypted values (macOS) are decrypted; other hosts are ignored."""
    import hashlib

    from tweethoarder.auth.chrome import extract_chrome_cookies

    key = b"k" * 16
    db_path = tmp_path / "Cookies"
    _create_test_chrome_cookies_db(db_path, [("ct0", "other-site", ".example.com")])
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE meta (key TEXT, value TEXT)")
    conn.execute("INSERT INTO meta VALUES ('version', '24')")
    for name, value in [("auth_token", b"secret_auth"), ("ct0", b"secret_ct0")]:
        conn.execute(
            "INSERT INTO cookies SELECT creation_utc, '.x.com', top_frame_site_key, ?, '', ?, "
            "path, expires_utc, is_secure, is_httponly, last_access_utc, has_expires, "
            "is_persistent, priority, samesite, source_scheme, source_port, last_update_utc, "
            "source_type, has_cross_site_ancestor FROM cookies LIMIT 1",
            (name, _encrypt_v10(hashlib.sha256(b".x.com").digest() + value, key)),
        )
    conn.commit()
    conn.close()

    assert extract_chrome_cookies(db_path) == {}
    assert extract_chrome_cookies(db_path, key) == {
        "auth_token": "secret_auth",
        "ct0": "secret_ct0",
    }


def test_find_chrome_cookies_db_finds_macos_profile(tmp_path: Path) -> None:
    """Should find Cookies in the macOS Chrome user data dir."""
    from tweethoarder.auth.chrome import find_chrome_cookies_db

    profile_dir = tmp_path / "Library" / "Application Support" / "Google" / "Chrome" / "Profile 1"
    profile_dir.mkdir(parents=True)
    (profile_dir / "Cookies").touch()

    assert find_chrome_cookies_db(tmp_path, profile="Profile 1") == profile_dir / "Cookies"


def test_get_chrome_encryption_key_macos_uses_security_cli() -> None:
    """Key comes from `security find-generic-password` with 1003 PBKDF2 iterations."""
    import subprocess
    from unittest.mock import patch

    from tweethoarder.auth.chrome import _derive_key, get_chrome_encryption_key_macos

    done = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"pw\n")
    with patch("subprocess.run", return_value=done) as run:
        key = get_chrome_encryption_key_macos()

    assert run.call_args.args[0][0] == "/usr/bin/security"
    assert key == _derive_key(b"pw", iterations=1003)
