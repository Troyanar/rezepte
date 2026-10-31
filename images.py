"""Fotos zu Rezepten von Unsplash (kostenloser Key: unsplash.com/developers).

Unsplash verlangt, dass die Bilder direkt von dort geladen werden. Gespeichert wird nur die Adresse
samt Fotograf (Namensnennung ist Pflicht). Wird ein Foto tatsächlich verwendet, muss das Unsplash
über die "download_location" gemeldet werden – das macht track().
"""

from __future__ import annotations

import httpx

import db

API = "https://api.unsplash.com"
UTM = "utm_source=rezepte&utm_medium=referral"  # verlangt Unsplash bei den Links zum Fotografen


class ImageError(Exception):
    pass


def _headers(key: str) -> dict:
    return {"Authorization": f"Client-ID {key}", "Accept-Version": "v1"}


def _with_utm(url: str) -> str:
    return f"{url}{'&' if '?' in url else '?'}{UTM}" if url else ""


async def _get(key: str, url: str, params: dict | None = None) -> httpx.Response:
    if not key:
        raise ImageError("Kein Unsplash-Key eingetragen (Einstellungen → Bilder)")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=params, headers=_headers(key))
    except httpx.HTTPError as exc:
        raise ImageError(f"Unsplash nicht erreichbar ({type(exc).__name__})") from None
    # Unsplash meldet ein erreichtes Limit mit 403, einen falschen Key mit 401
    if resp.status_code == 429 or (resp.status_code == 403 and resp.headers.get("X-Ratelimit-Remaining") == "0"):
        raise ImageError("Unsplash: Stundenlimit erreicht, später nochmal")
    if resp.status_code in (401, 403):
        raise ImageError("Unsplash-Key ungültig")
    if resp.status_code >= 400:
        raise ImageError(f"Unsplash meldet Fehler {resp.status_code}")
    return resp


async def search(key: str, query: str, page: int = 1, english: bool = False, per_page: int = 8) -> list[dict]:
    params = {"query": query, "per_page": per_page, "page": page, "orientation": "landscape",
              "content_filter": "high", "lang": "en" if english else "de"}
    resp = await _get(key, f"{API}/search/photos", params)
    try:
        raw = resp.json().get("results") or []
    except (ValueError, AttributeError):
        raise ImageError("Unsplash: unerwartete Antwort") from None
    photos = []
    for p in raw if isinstance(raw, list) else []:
        if not isinstance(p, dict):
            continue
        urls = p.get("urls") if isinstance(p.get("urls"), dict) else {}
        links = p.get("links") if isinstance(p.get("links"), dict) else {}
        user = p.get("user") if isinstance(p.get("user"), dict) else {}
        user_links = user.get("links") if isinstance(user.get("links"), dict) else {}
        image = db.clean_image({"small": urls.get("small"), "large": urls.get("regular"), "by": user.get("name"),
                                "url": _with_utm(str(user_links.get("html") or "")),
                                "download": links.get("download_location")})
        if image:
            photos.append(image)
    return photos


async def first_match(key: str, title: str, query: str = "") -> dict | None:
    """Bestes Foto für ein Rezept: erst der englische Suchbegriff der KI, sonst der deutsche Titel."""
    tries = [(query, True)] if query else []
    tries.append((title, False))
    for q, english in tries:
        photos = await search(key, q, english=english, per_page=1)
        if photos:
            return photos[0]
    return None


async def track(key: str, image: dict | None) -> None:
    """Unsplash melden, dass das Foto verwendet wird (Pflicht laut API-Regeln). Fehler sind egal."""
    if not key or not image or not image.get("download"):
        return
    try:
        await _get(key, image["download"])
    except ImageError:
        pass
