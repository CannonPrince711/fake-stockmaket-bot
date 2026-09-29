"""Accounts: people sign up with a username and password, and each account keeps its own profiles.

Everything lives under <data dir>/accounts/:
  users.json        usernames with salted password hashes (never the passwords themselves)
  session.key       random secret used to sign login cookies, so logins survive restarts
  <username>/       that account's profiles, one JSON file each
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import threading
import time
from pathlib import Path

from .portfolio import TradeError
from .profiles import Profiles

USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,29}$")
MIN_PASSWORD = 8
ITERATIONS = 240_000
SESSION_DAYS = 30


class Accounts:
    def __init__(self, data_dir: Path, allow_signups: bool = True):
        self.data_dir = Path(data_dir)
        self.dir = self.data_dir / "accounts"
        self.allow_signups = allow_signups
        self.lock = threading.Lock()
        self._key = None

    # ---- users ----

    def _users(self) -> dict:
        path = self.dir / "users.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def _save_users(self, users: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / "users.json.tmp"
        tmp.write_text(json.dumps(users, indent=2))
        os.replace(tmp, self.dir / "users.json")

    def count(self) -> int:
        return len(self._users())

    def signups_open(self) -> bool:
        """Sign-ups can be turned off, but the very first account can always be made."""
        return self.allow_signups or self.count() == 0

    def create(self, username: str, password: str) -> str:
        username = check_username(username)
        if len(password or "") < MIN_PASSWORD:
            raise TradeError(f"Passwords need at least {MIN_PASSWORD} characters")
        with self.lock:
            users = self._users()
            if not self.allow_signups and users:
                raise TradeError("Sign-ups are turned off on this site")
            if username.lower() in users:
                raise TradeError(f"The username '{username}' is taken")
            salt = secrets.token_bytes(16)
            users[username.lower()] = {
                "name": username, "salt": salt.hex(), "iterations": ITERATIONS,
                "hash": _hash(password, salt, ITERATIONS).hex(), "created": int(time.time()),
            }
            first = len(users) == 1
            self._save_users(users)
            if first:
                self._adopt_existing_profiles(username)
        return username

    def check_password(self, username: str, password: str) -> str:
        """The account's stored username if the password is right."""
        user = self._users().get((username or "").strip().lower())
        if user is None:
            _hash(password or "", b"x" * 16, ITERATIONS)  # take the same time either way
            raise TradeError("Wrong username or password")
        given = _hash(password or "", bytes.fromhex(user["salt"]), user["iterations"])
        if not hmac.compare_digest(given.hex(), user["hash"]):
            raise TradeError("Wrong username or password")
        return user["name"]

    def name_of(self, username: str) -> str | None:
        user = self._users().get(username.lower())
        return user["name"] if user else None

    def profiles(self, username: str) -> Profiles:
        return Profiles(self.dir / username.lower())

    def _adopt_existing_profiles(self, username: str) -> None:
        """Profiles made before accounts existed move into the first account, so nothing is lost."""
        target = self.dir / username.lower()
        target.mkdir(parents=True, exist_ok=True)
        for path in self.data_dir.glob("*.json"):
            if not (target / path.name).exists():
                shutil.move(str(path), target / path.name)

    # ---- login cookies ----

    def _secret(self) -> bytes:
        if self._key is None:
            path = self.dir / "session.key"
            with self.lock:
                if not path.exists():
                    self.dir.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(secrets.token_bytes(32))
                    try:
                        path.chmod(0o600)
                    except OSError:
                        pass
                self._key = path.read_bytes()
        return self._key

    def _sign(self, payload: str, user: dict) -> str:
        # Mixing in the password hash means changing a password logs out old sessions.
        msg = f"{payload}|{user['hash']}".encode()
        return hmac.new(self._secret(), msg, hashlib.sha256).hexdigest()

    def make_token(self, username: str) -> str:
        user = self._users()[username.lower()]
        payload = f"{username.lower()}|{int(time.time()) + SESSION_DAYS * 86400}"
        token = f"{payload}|{self._sign(payload, user)}"
        return base64.urlsafe_b64encode(token.encode()).decode().rstrip("=")

    def user_for_token(self, token: str | None) -> str | None:
        """The username a login cookie belongs to, or None if it's missing, forged or expired."""
        if not token:
            return None
        try:
            raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
            name, expires, sig = raw.split("|")
            if int(expires) < time.time():
                return None
        except (ValueError, UnicodeDecodeError):
            return None
        user = self._users().get(name)
        if user is None or not hmac.compare_digest(sig, self._sign(f"{name}|{expires}", user)):
            return None
        return user["name"]


class LoginLimiter:
    """Slow down password guessing: too many failures for one username locks it briefly."""

    def __init__(self, max_failures: int = 10, window: float = 300):
        self.max_failures, self.window = max_failures, window
        self.failures: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    def blocked(self, who: str) -> bool:
        with self.lock:
            recent = [t for t in self.failures.get(who, []) if time.time() - t < self.window]
            self.failures[who] = recent
            return len(recent) >= self.max_failures

    def failed(self, who: str) -> None:
        with self.lock:
            self.failures.setdefault(who, []).append(time.time())

    def succeeded(self, who: str) -> None:
        with self.lock:
            self.failures.pop(who, None)


def _hash(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)


def check_username(name: str) -> str:
    name = (name or "").strip()
    if not USERNAME_RE.match(name):
        raise TradeError("Usernames are 3 to 30 letters, numbers, dashes or underscores")
    return name
