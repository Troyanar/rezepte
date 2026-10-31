"""Passwort-Hash und Begrenzungen. Das gemeinsame Passwort wird mit `python set_password.py`
oder auf der Seite unter Einstellungen gesetzt und nur als Hash in der Datenbank gespeichert."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import secrets
import time

ITERATIONS = 600_000
SESSION_SECONDS = 60 * 24 * 60 * 60  # 60 Tage, verlängert sich bei jeder Nutzung
COOKIE = "rezepte_session"
MIN_PASSWORD = 8


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    b64 = lambda b: base64.b64encode(b).decode()
    return f"pbkdf2_sha256:{ITERATIONS}:{b64(salt)}:{b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = stored.split(":")
        if algo != "pbkdf2_sha256":
            return False
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iterations))
        return hmac.compare_digest(check, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


class RateLimit:
    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, list[float]] = {}

    def _recent(self, key: str) -> list[float]:
        now = time.monotonic()
        hits = [t for t in self._hits.get(key, []) if t > now - self.window]
        if hits:
            self._hits[key] = hits
        else:
            self._hits.pop(key, None)  # damit das Verzeichnis nicht endlos wächst
        return hits

    def blocked(self, key: str) -> bool:
        return len(self._recent(key)) >= self.limit

    def hit(self, key: str) -> None:
        self._hits[key] = self._recent(key) + [time.monotonic()]

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)


def _trusted(remote: str, trusted_proxies: set[str]) -> bool:
    """Einzelne Adressen oder ganze Netze ("172.18.0.0/16", z. B. das Docker-Netz des Proxy Managers)."""
    if remote in trusted_proxies:
        return True
    try:
        ip = ipaddress.ip_address(remote)
    except ValueError:
        return False
    for entry in trusted_proxies:
        if "/" in entry:
            try:
                if ip in ipaddress.ip_network(entry, strict=False):
                    return True
            except ValueError:
                continue
    return False


def client_ip(remote: str | None, headers, trusted_proxies: set[str]) -> str:
    """Echte Besucher-IP. Den Proxy-Headern wird nur geglaubt, wenn die Anfrage von einem
    eingetragenen Proxy kommt – sonst könnte jeder im WLAN eine falsche IP vorgeben."""
    remote = remote or "?"
    if _trusted(remote, trusted_proxies):
        forwarded = headers.get("X-Real-IP") or headers.get("X-Forwarded-For", "").split(",")[0].strip()
        if forwarded:
            return forwarded
    return remote
