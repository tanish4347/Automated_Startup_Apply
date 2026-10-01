"""Carry a site's session-only login cookies into automated runs of the same browser profile.

Some sites (Internshala) issue their login cookies (sessionToken, persistentSession, PHPSESSID)
without an expiry: Chrome keeps them on disk while the window is open and deletes them the next
time the profile starts, so a login made in `autoapply browser-login` is gone by the time the
automated browser opens the profile. This reads the candidate's own session cookies from the
profile's cookie store before Chrome discards them, keeps a copy next to the profile
(data/browser_profiles/<platform>.session.json, mode 600, git-ignored with data/), and adds them
back to the automated context. Nothing is forged: these are the cookies the site issued at login.
When the site expires the session, log in again.

Linux Chrome cookie store: encrypted_value "v10" + AES-128-CBC, key PBKDF2-SHA1("peanuts",
"saltysalt", 1 iteration, 16 bytes), IV of 16 spaces (the basic password store, which
browser-login selects). Chrome 130+ prefixes the plaintext with SHA-256(host_key).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from autoapply.logging import get_logger

log = get_logger(__name__)


def _key() -> bytes:
    return hashlib.pbkdf2_hmac("sha1", b"peanuts", b"saltysalt", 1, 16)


def _decrypt(encrypted: bytes, host: str) -> str | None:
    if not encrypted.startswith(b"v10"):
        return None  # v11 = OS keyring: not ours to read
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    dec = Cipher(algorithms.AES(_key()), modes.CBC(b" " * 16)).decryptor()
    raw = dec.update(encrypted[3:]) + dec.finalize()
    raw = raw[: -raw[-1]]  # PKCS#7 padding
    if raw[:32] == hashlib.sha256(host.encode()).digest():
        raw = raw[32:]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def read_session_cookies(profile_dir: Path) -> list[dict[str, Any]]:
    """Session-only (no expiry) cookies in the profile, as Playwright cookie dicts."""
    db = profile_dir / "Default" / "Cookies"
    if not db.exists():
        return []
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "Cookies"
        shutil.copy2(db, copy)   # the live file may be locked
        con = sqlite3.connect(copy)
        rows = con.execute("SELECT host_key, name, value, encrypted_value, path, is_secure, is_httponly, samesite "
                           "FROM cookies WHERE is_persistent = 0").fetchall()
        con.close()
    out = []
    for host, name, value, enc, path, secure, httponly, samesite in rows:
        val = value or (_decrypt(enc, host) if enc else None)
        if val is None:
            continue
        out.append({"name": name, "value": val, "domain": host, "path": path or "/", "secure": bool(secure),
                    "httpOnly": bool(httponly), "sameSite": {0: "None", 1: "Lax", 2: "Strict"}.get(samesite, "Lax")})
    return out


def sidecar(profile_dir: Path) -> Path:
    return profile_dir.parent / f"{profile_dir.name}.session.json"


def carry_over(profile_dir: Path) -> list[dict[str, Any]]:
    """Cookies to add to the automated context: fresh from the profile if the last login left
    some there (and refresh the saved copy), else the saved copy."""
    path = sidecar(profile_dir)
    cookies = read_session_cookies(profile_dir)
    if cookies:
        path.write_text(json.dumps(cookies))
        os.chmod(path, 0o600)
        return cookies
    return json.loads(path.read_text()) if path.exists() else []
