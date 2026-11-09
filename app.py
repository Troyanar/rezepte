"""Rezepte-Webseite: Rezeptsammlung, Einkaufszettel, KI-Entwürfe. Start: python app.py"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import re
import secrets
import sys
import time
from datetime import date

import httpx
from aiohttp import web

import ai
import angebote
import auth
import config
import db
import images

STATIC = config.BASE_DIR / "static"
IMAGE_URL = re.compile(r"^data:image/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+$")
JPEG_URL = re.compile(r"^data:image/jpeg;base64,([A-Za-z0-9+/=]+)$")
PHOTO_PATH = re.compile(r"^/fotos/[0-9a-f]{32}(?:_s)?\.jpg$")
MAX_PHOTO = 6 * 1024 * 1024  # pro Datei; der Browser verkleinert vorher auf 1600 px
MAX_ID = 2**63 - 1  # größte Zahl, die SQLite als ID kennt
log = logging.getLogger("rezepte")

store_key = web.AppKey("store", db.Store)
offers_key = web.AppKey("offers", angebote.Finder)
login_limit = auth.RateLimit(8, 15 * 60)       # pro IP
login_limit_all = auth.RateLimit(40, 15 * 60)  # alle zusammen, falls jemand viele IPs hat
ai_limit = auth.RateLimit(config.AI_LIMIT_PER_HOUR, 60 * 60)

SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' https://images.unsplash.com data: blob:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
        "connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'; object-src 'none'"
    ),
}


def fail(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


def store(request: web.Request) -> db.Store:
    return request.app[store_key]


async def body(request: web.Request) -> dict:
    try:
        data = await request.json()
    except ValueError:
        raise web.HTTPBadRequest(text="Ungültiges JSON") from None
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text="Ungültiges JSON")
    return data


def int_id(request: web.Request) -> int:
    try:
        number = int(request.match_info["id"])
    except ValueError:
        raise web.HTTPNotFound() from None
    if not 0 < number <= MAX_ID:
        raise web.HTTPNotFound()
    return number


def is_public(s: db.Store) -> bool:
    return s.setting("public", "1") == "1"


def is_shopping_public(s: db.Store) -> bool:
    return s.setting("public.shopping", "1") == "1"


def unsplash_key(s: db.Store) -> str:
    return s.setting("unsplash.key", config.UNSPLASH_KEY)


def ai_limit_per_hour(s: db.Store) -> int:
    return db.to_int(s.setting("ai.limit"), 1, 10_000) or config.AI_LIMIT_PER_HOUR


def default_servings(s: db.Store) -> int:
    """Für wie viele Personen meistens gekocht wird: KI-Vorschläge kommen so, das Formular bietet das Umrechnen an."""
    return db.to_int(s.setting("servings"), 1, 100) or 3


def is_https(request: web.Request) -> bool:
    return request.secure or request.headers.get("X-Forwarded-Proto") == "https"


def set_session_cookie(resp: web.StreamResponse, request: web.Request, token: str) -> None:
    resp.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_SECONDS, httponly=True, samesite="Lax",
                    secure=is_https(request))


def cross_site(request: web.Request) -> bool:
    """Formular von einer fremden Seite? (Browser schicken Sec-Fetch-Site mit.)"""
    return request.headers.get("Sec-Fetch-Site") == "cross-site"


# --- Anmeldung -------------------------------------------------------------------
OPEN = {"/", "/login", "/api/me"}
# Ohne Anmeldung nur lesen, und nur was in den Einstellungen freigegeben ist
READ_ONLY_RECIPES = re.compile(r"^/api/recipes(/\d+)?$")


def readable_without_login(request: web.Request, s: db.Store) -> bool:
    # Einzige Änderung ohne Anmeldung: den öffentlichen Zettel leeren (z. B. nach dem Einkauf)
    if request.method == "POST" and request.path == "/api/shopping/clear":
        return is_shopping_public(s)
    if request.method != "GET":
        return False
    if PHOTO_PATH.match(request.path):  # eigene Fotos: sichtbar, wo es auch die Rezepte sind
        return is_public(s) or is_shopping_public(s)
    if READ_ONLY_RECIPES.match(request.path):
        return is_public(s)
    if request.path == "/api/shopping":
        return is_shopping_public(s)
    return False


@web.middleware
async def security_headers(request: web.Request, handler):
    try:
        resp = await handler(request)
    except web.HTTPException as exc:
        exc.headers.update(SECURITY_HEADERS)
        raise
    resp.headers.update(SECURITY_HEADERS)
    return resp


@web.middleware
async def require_login(request: web.Request, handler):
    s = store(request)
    request["user"] = s.session_valid(request.cookies.get(auth.COOKIE), auth.SESSION_SECONDS)
    allowed = (request["user"] or request.path in OPEN or request.path.startswith("/static/")
               or readable_without_login(request, s))
    if allowed:
        # Schutz vor fremden Seiten, die Formulare an uns schicken: Schreibzugriffe nur als JSON
        if request.method not in ("GET", "HEAD") and request.path.startswith("/api/") \
                and request.content_type != "application/json":
            return fail(415, "Nur JSON")
        return await handler(request)
    if request.path.startswith("/api/"):
        return fail(401, "Bitte neu anmelden")
    raise web.HTTPFound("/login")


async def login_page(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(STATIC / "login.html", headers={"Cache-Control": "no-cache"})


async def login(request: web.Request) -> web.Response:
    if cross_site(request):
        raise web.HTTPForbidden()
    if (request.content_length or 0) > 4096:
        raise web.HTTPRequestEntityTooLarge(max_size=4096, actual_size=request.content_length)
    ip = auth.client_ip(request.remote, request.headers, config.TRUSTED_PROXIES)
    if login_limit.blocked(ip) or login_limit_all.blocked("all"):
        raise web.HTTPFound("/login?zu-viele")
    form = await request.post()
    # Passwort-Prüfung dauert absichtlich ~0,3 s – im Hintergrund, damit die Seite solange weiterläuft
    ok = await asyncio.to_thread(auth.verify_password, str(form.get("password", "")),
                                 store(request).setting("password_hash"))
    if not ok:
        login_limit.hit(ip)
        login_limit_all.hit("all")
        raise web.HTTPFound("/login?falsch")
    login_limit.reset(ip)
    token = secrets.token_urlsafe(32)
    store(request).add_session(token, auth.SESSION_SECONDS)
    resp = web.HTTPFound("/")
    set_session_cookie(resp, request, token)
    raise resp


async def logout(request: web.Request) -> web.Response:
    if cross_site(request):
        raise web.HTTPForbidden()
    store(request).end_session(request.cookies.get(auth.COOKIE))
    resp = web.HTTPFound("/login")
    resp.del_cookie(auth.COOKIE)
    raise resp


async def index(request: web.Request) -> web.StreamResponse:
    s = store(request)
    if not request["user"] and not is_public(s) and not is_shopping_public(s):
        raise web.HTTPFound("/login")
    resp = web.FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})
    if request["user"]:
        # Cookie bei jedem Besuch erneuern, sonst löscht der Browser es nach 60 Tagen trotz Nutzung
        set_session_cookie(resp, request, request.cookies[auth.COOKIE])
    return resp


async def me(request: web.Request) -> web.Response:
    s = store(request)
    return web.json_response({"user": request["user"], "public": is_public(s),
                              "public_shopping": is_shopping_public(s), "categories": db.CATEGORIES,
                              "servings": default_servings(s)})


# --- Rezepte ---------------------------------------------------------------------
async def list_recipes(request: web.Request) -> web.Response:
    return web.json_response(store(request).recipes())


async def get_recipe(request: web.Request) -> web.Response:
    recipe = store(request).recipe(int_id(request))
    return web.json_response(recipe) if recipe else fail(404, "Rezept nicht gefunden")


async def save_recipe(request: web.Request) -> web.Response:
    raw = await body(request)
    try:
        data = db.clean_recipe(raw)
    except ValueError as exc:
        return fail(400, str(exc))
    s = store(request)
    recipe_id = int_id(request) if "id" in request.match_info else None
    key = unsplash_key(s)
    # Neues Rezept ohne Foto: gleich eins suchen (ein Fehler dabei hält das Speichern nicht auf).
    # Wer im Formular "Kein Foto" gewählt hat, schickt no_image mit.
    if recipe_id is None and not data["image"] and not db.to_bool(raw.get("no_image")) and key:
        try:
            data["image"] = await images.first_match(key, data["title"], data["image_query"])
        except images.ImageError as exc:
            log.warning("Kein Foto für %s: %s", data["title"], exc)
    old = s.recipe(recipe_id) if recipe_id is not None else None
    recipe = s.save_recipe(data, recipe_id)
    if not recipe:
        return fail(404, "Rezept nicht gefunden")
    # Nur ein neu gewähltes Foto bei Unsplash als verwendet melden, nicht bei jedem Speichern
    if data["image"] and (not old or not old["image"] or old["image"]["large"] != data["image"]["large"]):
        await images.track(key, data["image"])
        prune_photos(s)  # ein ersetztes eigenes Foto wird nicht mehr gebraucht
    return web.json_response(recipe)


# --- Eigene Fotos -------------------------------------------------------------------
def _jpeg(data_url) -> bytes | None:
    match = JPEG_URL.match(data_url) if isinstance(data_url, str) else None
    if not match:
        return None
    try:
        raw = base64.b64decode(match.group(1), validate=True)
    except (binascii.Error, ValueError):
        return None
    return raw if raw.startswith(b"\xff\xd8\xff") and len(raw) <= MAX_PHOTO else None


async def upload_photo(request: web.Request) -> web.Response:
    """Eigenes Foto, vom Browser schon verkleinert und als JPEG geschickt (groß + klein)."""
    data = await body(request)
    large, small = _jpeg(data.get("large")), _jpeg(data.get("small"))
    if not large or not small:
        return fail(400, "Das Bild ließ sich nicht lesen, bitte ein anderes probieren")
    name = secrets.token_hex(16)
    config.PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    (config.PHOTO_DIR / f"{name}.jpg").write_bytes(large)
    (config.PHOTO_DIR / f"{name}_s.jpg").write_bytes(small)
    return web.json_response({"small": f"/fotos/{name}_s.jpg", "large": f"/fotos/{name}.jpg",
                              "by": "", "url": "", "download": ""})


async def photo(request: web.Request) -> web.StreamResponse:
    path = config.PHOTO_DIR / request.match_info["name"]
    if not path.is_file():
        raise web.HTTPNotFound()
    # Der Name ist zufällig und ändert sich nie: darf lange im Browser bleiben
    return web.FileResponse(path, headers={"Cache-Control": "private, max-age=31536000, immutable"})


def prune_photos(s: db.Store) -> None:
    """Eigene Fotos löschen, die kein Rezept mehr nutzt. Erst nach einer Stunde, damit ein gerade
    hochgeladenes Foto nicht verschwindet, bevor das Rezept gespeichert ist."""
    if not config.PHOTO_DIR.is_dir():
        return
    used = {r["image"][k].rsplit("/", 1)[-1] for r in s.recipes() if r["image"] for k in ("small", "large")}
    for path in config.PHOTO_DIR.glob("*.jpg"):
        try:
            if path.name not in used and time.time() - path.stat().st_mtime > 3600:
                path.unlink()
        except OSError as exc:
            log.warning("Foto %s nicht gelöscht: %s", path.name, exc)


async def search_images(request: web.Request) -> web.Response:
    query = request.query.get("q", "").strip()[:80]
    if not query:
        return fail(400, "Wonach suchen?")
    page = db.to_int(request.query.get("page"), 1, 50) or 1
    try:
        found = await images.search(unsplash_key(store(request)), query, page, english=request.query.get("en") == "1")
    except images.ImageError as exc:
        return fail(502, str(exc))
    return web.json_response(found)


async def fill_images(request: web.Request) -> web.Response:
    """Fotos für Rezepte ohne Bild. Höchstens 20 auf einmal: Unsplash erlaubt anfangs nur 50 Anfragen
    pro Stunde, und jedes Foto braucht zwei (Suche + Verwendung melden)."""
    s, key = store(request), unsplash_key(store(request))
    all_missing = [r for r in s.recipes() if not r["image"]]
    found, error = 0, ""
    try:
        for r in all_missing[:20]:
            image = await images.first_match(key, r["title"])
            if image:
                s.set_image(r["id"], image)
                await images.track(key, image)
                found += 1
    except images.ImageError as exc:
        if not found:
            return fail(502, str(exc))
        error = str(exc)  # ein Teil hat geklappt: das melden statt alles als Fehler
    return web.json_response({"found": found, "missing": len(all_missing) - found, "error": error})


async def delete_recipe(request: web.Request) -> web.Response:
    if not store(request).delete_recipe(int_id(request)):
        return fail(404, "Rezept nicht gefunden")
    prune_photos(store(request))
    return web.json_response({"ok": True})


async def recipe_to_cart(request: web.Request) -> web.Response:
    recipe = store(request).recipe(int_id(request))
    if not recipe:
        return fail(404, "Rezept nicht gefunden")
    servings = db.to_int((await body(request)).get("servings"), 1, 100)
    count = store(request).add_recipe_to_shopping(recipe, servings)
    return web.json_response({"added": count, "items": store(request).shopping()})


async def recipe_offers(request: web.Request) -> web.Response:
    """Angebote zu den Zutaten eines Rezepts. Nur angemeldet: jede Abfrage geht an Marktguru/kaufDA."""
    recipe = store(request).recipe(int_id(request))
    if not recipe:
        return fail(404, "Rezept nicht gefunden")
    names = [i["name"] for i in recipe["ingredients"]]
    return web.json_response(await request.app[offers_key].lookup(names))


async def export(request: web.Request) -> web.Response:
    data = json.dumps(store(request).recipes(), ensure_ascii=False, indent=2)
    name = f"rezepte-{date.today().isoformat()}.json"
    return web.Response(text=data, content_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})


# --- Einkaufszettel ----------------------------------------------------------------
async def list_shopping(request: web.Request) -> web.Response:
    return web.json_response(store(request).shopping())


async def shopping_offers(request: web.Request) -> web.Response:
    """Angebote zu allem, was noch offen auf dem Zettel steht; "items" hat zusätzlich die Zettel-ID."""
    open_items = [i for i in store(request).shopping() if not i["checked"]]
    found = await request.app[offers_key].lookup([i["name"] for i in open_items])
    for item, hit in zip(open_items, found["items"]):
        hit["id"] = item["id"]
    return web.json_response(found)


async def add_shopping(request: web.Request) -> web.Response:
    if not store(request).add_manual_item(await body(request)):
        return fail(400, "Was soll auf den Zettel?")
    return web.json_response(store(request).shopping())


async def update_shopping(request: web.Request) -> web.Response:
    store(request).set_checked(int_id(request), db.to_bool((await body(request)).get("checked")))
    return web.json_response(store(request).shopping())


async def delete_shopping(request: web.Request) -> web.Response:
    store(request).delete_item(int_id(request))
    return web.json_response(store(request).shopping())


async def clear_shopping(request: web.Request) -> web.Response:
    store(request).clear_shopping(db.to_bool((await body(request)).get("only_checked")))
    return web.json_response(store(request).shopping())


# --- KI ------------------------------------------------------------------------
async def ai_providers(request: web.Request) -> web.Response:
    s = store(request)
    providers = ai.available(s)
    default = ai.default_id(s)
    return web.json_response({"providers": providers,
                              "default": default if default in [p["id"] for p in providers] else None})


async def ai_draft(request: web.Request) -> web.Response:
    s = store(request)
    ai_limit.limit = ai_limit_per_hour(s)
    if ai_limit.blocked("all"):
        return fail(429, f"Mehr als {ai_limit.limit} KI-Anfragen in einer Stunde, bitte später nochmal")
    data = await body(request)
    kind = request.match_info["kind"]
    try:
        provider = ai.pick(s, str(data.get("provider") or ""))
        if kind == "vorschlag":
            wish = str(data.get("wish") or "").strip()[:1000]
            if not wish:
                return fail(400, "Was soll es geben?")
            ai_limit.hit("all")
            recipe = await ai.generate(provider, wish, default_servings(s))
        elif kind == "text":
            text = str(data.get("text") or "").strip()[:20000]
            if not text:
                return fail(400, "Kein Text eingefügt")
            ai_limit.hit("all")
            recipe = await ai.from_text(provider, text)
        else:
            raw_images = data.get("images") if isinstance(data.get("images"), list) else []
            pictures = [i for i in raw_images if isinstance(i, str) and IMAGE_URL.match(i)][:4]
            if not pictures:
                return fail(400, "Kein Bild erkannt")
            ai_limit.hit("all")
            recipe = await ai.from_images(provider, pictures, str(data.get("hint") or "")[:300])
    except ai.AIError as exc:
        return fail(502, str(exc))
    return web.json_response(recipe)


# --- Einstellungen ------------------------------------------------------------------
def offers_location(s: db.Store) -> angebote.Location:
    """Ort für die Angebote aus den Einstellungen; noch keiner gesetzt → Zweibrücken."""
    try:
        return angebote.Location(**json.loads(s.setting("offers.location")))
    except (ValueError, TypeError):
        return angebote.DEFAULT_LOCATION


def settings_view(s: db.Store) -> dict:
    providers = []
    for p in ai.providers(s).values():
        own_key = s.setting(f"ai.{p['id']}.key")
        providers.append({
            "id": p["id"], "label": p["label"], "model": p["model"],
            # Der Key selbst verlässt den Server nie, nur die letzten Zeichen zum Wiedererkennen
            "key_hint": p["key"][-4:] if p["key"] else "",
            "key_source": "seite" if own_key else ("env" if p["key"] else ""),
        })
    key = unsplash_key(s)
    unsplash = {"key_hint": key[-4:] if key else "",
                "key_source": "seite" if s.setting("unsplash.key") else ("env" if config.UNSPLASH_KEY else ""),
                "missing": sum(1 for r in s.recipes() if not r["image"])}
    return {"public": is_public(s), "public_shopping": is_shopping_public(s), "ai_default": ai.default_id(s),
            "ai_limit": ai_limit_per_hour(s), "providers": providers, "unsplash": unsplash,
            "servings": default_servings(s), "location": vars(offers_location(s))}


async def get_settings(request: web.Request) -> web.Response:
    return web.json_response(settings_view(store(request)))


async def save_settings(request: web.Request) -> web.Response:
    s, data = store(request), await body(request)
    # Erst alles prüfen, dann schreiben – sonst bliebe bei einem Fehler die Hälfte gespeichert
    changes: dict[str, str | None] = {}
    if "public" in data:
        changes["public"] = "1" if db.to_bool(data["public"]) else "0"
    if "public_shopping" in data:
        changes["public.shopping"] = "1" if db.to_bool(data["public_shopping"]) else "0"
    if isinstance(data.get("ai_default"), str) and data["ai_default"] in config.AI_PROVIDERS:
        changes["ai.default"] = data["ai_default"]
    if "ai_limit" in data:
        limit = db.to_int(data["ai_limit"], 1, 10_000)
        if limit is None:
            return fail(400, "Anfragen pro Stunde: eine Zahl von 1 bis 10000")
        changes["ai.limit"] = str(limit)
    if "servings" in data:
        servings = db.to_int(data["servings"], 1, 100)
        if servings is None:
            return fail(400, "Personen: eine Zahl von 1 bis 100")
        changes["servings"] = str(servings)
    location = None
    if str(data.get("location") or "").strip():
        try:
            location = await angebote.find_location(str(data["location"])[:100])
        except httpx.HTTPError:
            return fail(502, "Die Ortssuche ist gerade nicht erreichbar, bitte später nochmal")
        if not location:
            return fail(400, "Den Ort kenne ich nicht. Am sichersten klappt's mit der Postleitzahl.")
        changes["offers.location"] = json.dumps(vars(location), ensure_ascii=False)
    unsplash = str(data.get("unsplash_key") or "").strip()
    if unsplash:
        changes["unsplash.key"] = unsplash[:200]
    if db.to_bool(data.get("remove_unsplash_key")):
        changes["unsplash.key"] = None
    providers = data.get("providers") or {}
    if not isinstance(providers, dict):
        return fail(400, "Ungültige Anbieter-Daten")
    for pid, values in providers.items():
        if pid not in config.AI_PROVIDERS or not isinstance(values, dict):
            continue
        key = str(values.get("key") or "").strip()
        if key:
            changes[f"ai.{pid}.key"] = key[:300]
        if db.to_bool(values.get("remove_key")):
            changes[f"ai.{pid}.key"] = None
        if "model" in values:
            model = str(values["model"] or "").strip()[:120]
            # leer oder wie die Vorgabe → Vorgabe aus der .env nutzen
            changes[f"ai.{pid}.model"] = model if model and model != config.AI_PROVIDERS[pid]["model"] else None
    for key, value in changes.items():
        s.set_setting(key, value)
    if location:
        request.app[offers_key].set_location(location)
        request.app[offers_key].refresh_kaufda()
    return web.json_response(settings_view(s))


async def test_provider(request: web.Request) -> web.Response:
    pid = str((await body(request)).get("provider") or "")
    if pid == "unsplash":
        try:
            found = await images.search(unsplash_key(store(request)), "soup", english=True, per_page=1)
        except images.ImageError as exc:
            return fail(502, str(exc))
        return web.json_response({"answer": "Fotos gefunden" if found else "Key geht, aber nichts gefunden"})
    if pid not in config.AI_PROVIDERS:
        return fail(400, "Unbekannter Anbieter")
    try:
        answer = await ai.ping(ai.providers(store(request))[pid])
    except ai.AIError as exc:
        return fail(502, str(exc))
    return web.json_response({"answer": answer})


async def change_password(request: web.Request) -> web.Response:
    s, data = store(request), await body(request)
    ok = await asyncio.to_thread(auth.verify_password, str(data.get("current") or ""), s.setting("password_hash"))
    if not ok:
        return fail(400, "Das bisherige Passwort stimmt nicht")
    new = str(data.get("new") or "")
    if len(new) < auth.MIN_PASSWORD:
        return fail(400, f"Das neue Passwort braucht mindestens {auth.MIN_PASSWORD} Zeichen")
    s.set_setting("password_hash", await asyncio.to_thread(auth.hash_password, new))
    s.end_other_sessions(request.cookies.get(auth.COOKIE))  # andere Geräte müssen sich neu anmelden
    return web.json_response({"ok": True})


def make_app() -> web.Application:
    app = web.Application(middlewares=[security_headers, require_login], client_max_size=25 * 1024 * 1024)
    app[store_key] = db.Store(config.DB_PATH)
    prune_photos(app[store_key])  # Reste von abgebrochenen Formularen

    async def offers_ctx(app: web.Application):
        app[offers_key] = angebote.Finder(offers_location(app[store_key]), config.OFFERS_RETAILERS,
                                          config.OFFERS_KAUFDA_RETAILERS)
        app[offers_key].refresh_kaufda()
        yield
        await app[offers_key].close()
    app.cleanup_ctx.append(offers_ctx)
    app.add_routes([
        web.get("/", index),
        web.get("/login", login_page),
        web.post("/login", login),
        web.post("/logout", logout),
        web.get("/api/me", me),
        web.get("/api/recipes", list_recipes),
        web.post("/api/recipes", save_recipe),
        web.get("/api/recipes/{id}", get_recipe),
        web.put("/api/recipes/{id}", save_recipe),
        web.delete("/api/recipes/{id}", delete_recipe),
        web.post("/api/recipes/{id}/cart", recipe_to_cart),
        web.get("/api/recipes/{id}/angebote", recipe_offers),
        web.get("/api/export", export),
        web.get("/api/images", search_images),
        web.post("/api/images/fill", fill_images),
        web.post("/api/fotos", upload_photo),
        web.get(r"/fotos/{name:[0-9a-f]{32}(?:_s)?\.jpg}", photo),
        web.get("/api/shopping", list_shopping),
        web.get("/api/shopping/angebote", shopping_offers),
        web.post("/api/shopping", add_shopping),
        web.post("/api/shopping/clear", clear_shopping),
        web.patch("/api/shopping/{id}", update_shopping),
        web.delete("/api/shopping/{id}", delete_shopping),
        web.get("/api/ai", ai_providers),
        web.post("/api/ai/{kind:vorschlag|text|bild}", ai_draft),
        web.get("/api/settings", get_settings),
        web.put("/api/settings", save_settings),
        web.post("/api/settings/test", test_provider),
        web.post("/api/settings/password", change_password),
        web.static("/static", STATIC),
    ])
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not db.Store(config.DB_PATH).setting("password_hash"):
        sys.exit("Noch kein Passwort gesetzt. Bitte zuerst: python set_password.py")
    web.run_app(make_app(), host=config.HOST, port=config.PORT, print=lambda _: log.info(
        "Rezepte laufen auf http://%s:%s", "localhost" if config.HOST == "127.0.0.1" else config.HOST, config.PORT))
