"""Supermarkt-Angebote zu Zutaten (Marktguru für Lidl, Aldi, Rewe …, kaufDA für Globus).

Die beiden Abfragen sind aus dem Angebote-Bot (Documents\\Claude\\angebote-bot\\sources) übernommen.
Beide Quellen sind inoffiziell und können sich jederzeit ändern; ihre AGB untersagen das automatische
Auslesen. Darum nur privat nutzen, und jede Suche wird eine Stunde lang zwischengespeichert.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

log = logging.getLogger("rezepte.angebote")
logging.getLogger("httpx").setLevel(logging.WARNING)  # sonst steht jede Prospektseite im Log

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
CACHE_SECONDS = 60 * 60
PER_INGREDIENT = 3  # so viele Angebote je Zutat


@dataclass
class Offer:
    id: str
    retailer: str
    title: str
    description: str | None = None
    price: float | None = None
    old_price: float | None = None
    unit_price: str | None = None  # z. B. "4,99 €/kg"
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    loyalty_card: bool = False

    def view(self) -> dict:
        discount = None
        if self.price and self.old_price and self.old_price > self.price:
            discount = round((1 - self.price / self.old_price) * 100)
        return {
            "retailer": self.retailer, "title": self.title, "price": self.price, "old_price": self.old_price,
            "unit_price": self.unit_price, "discount": discount, "card": self.loyalty_card,
            "from": self.valid_from.isoformat() if self.valid_from else None,
            "to": self.valid_to.isoformat() if self.valid_to else None,
        }


def format_euro(value: float | None) -> str:
    return "–" if value is None else f"{value:.2f} €".replace(".", ",")


def retailer_allowed(name: str, allowed: list[str]) -> bool:
    """Teilstring ohne Groß/Klein: "Globus" passt auf "GLOBUS", "Netto" auf "Netto Marken-Discount"."""
    name = name.lower()
    return any(a.lower() in name for a in allowed)


# --- Marktguru -----------------------------------------------------------------------
MG_HOMEPAGE = "https://www.marktguru.de/"
MG_SEARCH = "https://api.marktguru.de/api/v1/offers/search"
_MG_CONFIG = re.compile(r'<script type="application/json"[^>]*>(.*?)</script>', re.S)
NO_BRAND = "thisisnobrand123"  # Platzhalter, den Marktguru bei Obst, Gemüse usw. als Marke führt


class MarktguruClient:
    def __init__(self, http: httpx.AsyncClient):
        self.http = http
        self._keys: dict[str, str] | None = None

    async def _load_keys(self) -> dict[str, str]:
        """Die API-Keys stehen im JSON-Block der Startseite; bei einem 401 werden sie neu geholt."""
        resp = await self.http.get(MG_HOMEPAGE, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        for block in _MG_CONFIG.findall(resp.text):
            try:
                config = json.loads(block).get("config", {})
            except (json.JSONDecodeError, AttributeError):
                continue
            if "apiKey" in config and "clientKey" in config:
                return {"x-apikey": config["apiKey"], "x-clientkey": config["clientKey"]}
        raise RuntimeError("Marktguru-API-Keys nicht auf der Startseite gefunden")

    async def _get(self, params: dict) -> dict:
        for attempt in range(2):
            if self._keys is None:
                self._keys = await self._load_keys()
            resp = await self.http.get(MG_SEARCH, params=params,
                                       headers={**self._keys, "User-Agent": USER_AGENT, "Accept": "application/json"})
            if resp.status_code == 401 and attempt == 0:
                self._keys = None
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError("unreachable")

    async def search(self, query: str, zip_code: str) -> list[Offer]:
        # Eine Seite reicht: Marktguru sortiert nach Treffergüte, für eine Zutat zählen nur die ersten
        data = await self._get({"as": "web", "q": query, "zipCode": zip_code, "limit": 200, "offset": 0})
        return [o for o in map(_mg_offer, data.get("results", [])) if o]


def _iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _mg_offer(raw: dict) -> Offer | None:
    advertisers = raw.get("advertisers") or []
    if not advertisers:
        return None
    product = (raw.get("product") or {}).get("name") or ""
    brand = (raw.get("brand") or {}).get("name") or ""
    if brand == NO_BRAND:
        brand = ""
    title = f"{brand} {product}".strip() if brand and brand.lower() not in product.lower() else product
    ref, unit = raw.get("referencePrice"), (raw.get("unit") or {}).get("shortName")
    dates = raw.get("validityDates") or [{}]
    return Offer(
        id=f"mg:{raw['id']}",
        retailer=advertisers[0].get("name", "?"),
        title=title or "Angebot",
        description=raw.get("description"),
        price=raw.get("price"),
        old_price=raw.get("oldPrice"),
        unit_price=f"{format_euro(ref)}/{unit}" if ref and unit else None,
        valid_from=_iso(dates[0].get("from")),
        valid_to=_iso(dates[-1].get("to")),
        loyalty_card=bool(raw.get("requiresLoyalityMembership")),
    )


# --- kaufDA (Globus) ---------------------------------------------------------------------
KD_SECTORS = ["Supermarkt/p-c11"]
KD_OFFERS = "https://content-viewer-be.kaufda.de/v1/premiumPanel/offers"
KD_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "*/*",
    "Bonial-Api-Consumer": "web-content-viewer-fe",
    "delivery_channel": "dest.kaufda",
    "user_platform_category": "desktop.web.browser",
    "user_platform_os": "windows",
    "Origin": "https://www.kaufda.de",
    "Referer": "https://www.kaufda.de/",
}
_KD_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


class KaufdaClient:
    """kaufDA kann nicht suchen: es werden alle Produkte der Prospekte geladen und hier verglichen."""

    def __init__(self, http: httpx.AsyncClient, city: str, lat: float, lng: float, retailers: list[str]):
        self.http, self.city, self.lat, self.lng, self.retailers = http, city, lat, lng, retailers

    async def _brochures(self) -> list[dict]:
        found: dict[str, dict] = {}
        for sector in KD_SECTORS:
            resp = await self.http.get(f"https://www.kaufda.de/{self.city}/{sector}", headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            match = _KD_NEXT_DATA.search(resp.text)
            if not match:
                continue
            info = json.loads(match.group(1))["props"]["pageProps"].get("pageInformation", {})
            for group in (info.get("brochures") or {}).values():
                for b in group if isinstance(group, list) else []:
                    publisher = (b.get("publisher") or {}).get("name", "")
                    if b.get("contentId") and retailer_allowed(publisher, self.retailers):
                        found[b["contentId"]] = b
            await asyncio.sleep(2)  # Crawl-delay laut robots.txt
        return list(found.values())

    async def _brochure_offers(self, brochure: dict) -> list[Offer]:
        offers: list[Offer] = []
        page = 0
        while True:
            params = {"brochureId": brochure["contentId"], "page": page, "size": 200, "partner": "kaufda_web",
                      "brochureKey": "", "lat": self.lat, "lng": self.lng}
            resp = await self.http.get(KD_OFFERS, params=params, headers=KD_HEADERS)
            resp.raise_for_status()
            data = resp.json()
            offers += [o for o in (_kd_offer(item.get("content") or {}) for item in data.get("contents", [])) if o]
            page += 1
            if page >= (data.get("page") or {}).get("totalPages", 0):
                return offers
            await asyncio.sleep(1)

    async def all_offers(self) -> list[Offer]:
        offers: dict[str, Offer] = {}
        for brochure in await self._brochures():
            for o in await self._brochure_offers(brochure):
                offers[o.id] = o
        return list(offers.values())


def _kd_offer(c: dict) -> Offer | None:
    if c.get("type") != "offer" or not c.get("products"):
        return None
    product = c["products"][0]
    deals = {d.get("type"): d for d in c.get("deals") or []}
    sale, regular = deals.get("SALES_PRICE") or {}, deals.get("REGULAR_PRICE") or {}
    description = " ".join(p.get("paragraph", "") for p in product.get("description") or []).strip()
    validity = ((c.get("publicationProfiles") or [{}])[0]).get("validity") or {}
    unit_price = sale.get("priceByBaseUnit") or None
    if unit_price:  # "1 l = 1.75" -> "1,75 €/l"
        m = re.match(r"\s*1?\s*([^=]+?)\s*=\s*([\d.]+)", unit_price)
        unit_price = f"{format_euro(float(m.group(2)))}/{m.group(1)}" if m else unit_price
    parse = lambda v: datetime.strptime(v, "%Y-%m-%dT%H:%M:%S.%f%z") if v else None  # noqa: E731
    return Offer(
        id=f"kd:{c['id']}",
        retailer=(c.get("publisher") or {}).get("name", "?"),
        title=product.get("name") or "Angebot",
        description=description or None,
        price=sale.get("max"),
        old_price=regular.get("max"),
        unit_price=unit_price,
        valid_from=parse(validity.get("startDate")),
        valid_to=parse(validity.get("endDate")),
    )


# --- Ort (Einstellungen) --------------------------------------------------------------------
NOMINATIM = "https://nominatim.openstreetmap.org"
NOMINATIM_HEADERS = {"User-Agent": "rezepte-privat/1.0", "Accept-Language": "de"}


@dataclass
class Location:
    zip_code: str
    city: str
    lat: float
    lng: float

    @property
    def kaufda_city(self) -> str:
        """kaufDA schreibt Städte ohne Umlaute und mit Bindestrichen: "Frankfurt am Main" → "Frankfurt-am-Main"."""
        slug = self.city
        for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue"), ("ß", "ss")):
            slug = slug.replace(a, b)
        return re.sub(r"[^A-Za-z0-9]+", "-", slug).strip("-")


DEFAULT_LOCATION = Location("66482", "Zweibrücken", 49.2497, 7.3606)


async def find_location(query: str) -> Location | None:
    """Postleitzahl oder Ortsname → Ort mit PLZ und Koordinaten (OpenStreetMap, nur Deutschland)."""
    query = query.strip()
    params = {"countrycodes": "de", "format": "jsonv2", "addressdetails": 1, "limit": 1}
    params |= {"postalcode": query} if re.fullmatch(r"\d{5}", query) else {"q": query}
    async with httpx.AsyncClient(timeout=15, headers=NOMINATIM_HEADERS) as http:
        found = (await http.get(f"{NOMINATIM}/search", params=params)).raise_for_status().json()
        if not found:
            return None
        hit = found[0]
        address = hit.get("address") or {}
        zip_code = query if query.isdigit() else address.get("postcode", "")
        if not re.fullmatch(r"\d{5}", zip_code):
            # Große Städte haben keine eigene PLZ: die vom Stadtzentrum nehmen
            back = (await http.get(f"{NOMINATIM}/reverse", params={
                "lat": hit["lat"], "lon": hit["lon"], "format": "jsonv2", "zoom": 18})).raise_for_status().json()
            zip_code = ((back.get("address") or {}).get("postcode") or "")[:5]
    city = next((address[k] for k in ("city", "town", "village", "municipality") if address.get(k)), "")
    if not city or not re.fullmatch(r"\d{5}", zip_code):
        return None
    return Location(zip_code, city, round(float(hit["lat"]), 4), round(float(hit["lon"]), 4))


# --- Zutat → passende Angebote -------------------------------------------------------------
# Gibt es im Prospekt praktisch nie einzeln oder lohnt nicht
SKIP = {"salz", "pfeffer", "wasser", "eiswasser", "leitungswasser", "eiswürfel", "meersalz", "salzwasser"}
# Beschreibende Wörter, die im Laden keine Rolle spielen
FILLER = re.compile(
    r"\b(frisch\w*|gehackt\w*|gemahlen\w*|getrocknet\w*|gewürfelt\w*|geschnitten\w*|gerieben\w*|zerlassen\w*|"
    r"weich\w*|kalt\w*|warm\w*|fein\w*|grob\w*|klein\w*|groß\w*|mittelgroß\w*|optional|etwas|ca\.?|tk|bio|"
    r"nach|geschmack|belieben|zum|zur|für|und|oder|n\.\s*b\.)\b", re.I)
ENDINGS = ("en", "er", "n", "e", "s")
# "Thunfisch aus der Dose", "Pflanzenöl oder Sonnenblumenöl": gekauft wird, was davor steht
CUT = re.compile(r"\s(?:aus|in|im|mit|vom|von|oder|bzw\.?|/)\s.*$")
# Im Rezept steht die Einzahl oder ein anderes Wort, im Prospekt das, was man kauft
SHOP_WORDS = {"ei": "eier", "zitrone": "zitronen", "limette": "limetten",
              "frühlingszwiebel": "lauchzwiebeln", "frühlingszwiebeln": "lauchzwiebeln"}
# Steht so ein Wort hinter der Zutat, ist es ein anderes Produkt ("Ingwer Shots", "Paprika Chips")
OTHER_PRODUCT = {"shot", "shots", "drink", "chips", "snack", "snacks", "riegel", "pudding", "joghurt", "saft",
                 "nektar", "likör", "bonbons", "tee", "kekse", "dip", "aufstrich", "creme", "waffeln", "sticks", "eis",
                 "style", "geschmack"}
# "Hähnchenbrust" ist im Prospekt oft "Hähnchenbrustfilet"
CUTS = ("filet", "filets", "fleisch", "streifen", "würfel", "stücke", "teile")


def _stem(word: str) -> str:
    for end in ENDINGS:
        if word.endswith(end) and len(word) - len(end) >= 3:
            return word[:-len(end)]
    return word


def search_term(name: str) -> str | None:
    """'2 rote Zwiebeln (gewürfelt)' liegt als Name 'rote Zwiebeln (gewürfelt)' vor → 'rote zwiebeln'.
    None = nicht suchen (Salz, Wasser, …)."""
    # casefold macht aus "ß" "ss": Prospekte schreiben oft "Weisswein", und Marktguru findet beides
    text = re.sub(r"\([^)]*\)", " ", name).split(",")[0].casefold()
    text = FILLER.sub(" ", CUT.sub("", f" {text} "))
    words = re.findall(r"[\wäöüß\-]+", text)
    words = [w for w in words if len(w) >= 2 and not w.isdigit()]
    if not words or any(w in SKIP for w in words):
        return None
    words[-1] = SHOP_WORDS.get(words[-1], words[-1])
    return " ".join(words[-2:])  # Adjektiv + Hauptwort reicht, das Hauptwort steht im Deutschen hinten


def _token_score(head: str, token: str) -> int:
    part = token.split("-")[-1]  # "Rosmarin-Olivenöl-Brot" ist ein Brot
    if _stem(part) == head:
        # "Saarland-Eier" sind Eier, "Vanillin-Zucker" ist aber kein Zucker: nur schwächer werten
        return 2 if part == token else 1
    if len(part) > len(head) + 2 and (part.endswith(head) or _stem(part).endswith(head)):
        return 1
    if part.startswith(head) and part[len(head):].lstrip("s") in CUTS:
        return 1
    return 0


def _score(term: str, offer: Offer) -> int:
    """0 = passt nicht. 3 = ganze Wortfolge, 2 = Hauptwort genau, 1 = Hauptwort am Ende eines
    zusammengesetzten Worts ("Speisezwiebeln" zu "Zwiebeln", aber nicht "Zwiebelmettwurst").
    Verglichen wird nur mit dem Titel: in Beschreibung und Kategorien steht oft nur eine Geschmacksrichtung."""
    head = _stem(term.split()[-1])
    title = offer.title.casefold()
    tokens = re.findall(r"[\wäöüß\-]+", title)
    best, at = 0, -1
    for i, token in enumerate(tokens):
        score = _token_score(head, token)
        if score > best:
            best, at = score, i
    if not best or any(t in OTHER_PRODUCT for t in tokens[at + 1:]):
        return 0
    return 3 if " " in term and term in title else best


def best_offers(term: str, offers: list[Offer], limit: int = PER_INGREDIENT) -> list[Offer]:
    now = datetime.now(timezone.utc)
    seen, scored = set(), []
    for o in offers:
        if o.valid_to and o.valid_to < now:
            continue
        key = (o.retailer.casefold(), o.title.casefold(), o.price)
        score = _score(term, o)
        if score and key not in seen:
            seen.add(key)
            # Beste Übereinstimmung zuerst, dann kurze Titel (eher die Zutat selbst als ein Fertigprodukt), dann Preis
            scored.append((-score, len(o.title.split()), o.price or 999, o))
    return [o for *_, o in sorted(scored, key=lambda t: t[:3])][:limit]


class Finder:
    """Hält die Verbindungen und den Zwischenspeicher. Ein Exemplar pro Server."""

    def __init__(self, location: Location, retailers: list[str], kaufda_retailers: list[str]):
        self.retailers, self.kaufda_retailers = retailers, kaufda_retailers
        self.http = httpx.AsyncClient(timeout=20, follow_redirects=True)
        self.marktguru = MarktguruClient(self.http)
        self._locks: dict[str, asyncio.Lock] = {}
        self._limit = asyncio.Semaphore(4)  # Marktguru nicht mit zehn Anfragen gleichzeitig bombardieren
        self._kaufda_task: asyncio.Task | None = None
        self.set_location(location)

    def set_location(self, location: Location) -> None:
        """Neuer Ort (aus den Einstellungen): alles Zwischengespeicherte gehört zum alten."""
        if self._kaufda_task:
            self._kaufda_task.cancel()
            self._kaufda_task = None
        self.zip_code = location.zip_code
        self.kaufda = (KaufdaClient(self.http, location.kaufda_city, location.lat, location.lng, self.kaufda_retailers)
                       if self.kaufda_retailers else None)
        self._searches: dict[str, tuple[float, list[Offer]]] = {}
        self._kaufda: tuple[float, list[Offer]] = (0.0, [])
        self.kaufda_status = "aus" if not self.kaufda else "lädt"

    async def close(self) -> None:
        if self._kaufda_task:
            self._kaufda_task.cancel()
        await self.http.aclose()

    # kaufDA dauert beim ersten Mal eine halbe Minute (Pausen laut robots.txt): läuft im Hintergrund,
    # bis dahin gibt es nur die Marktguru-Treffer. Der Server stößt es gleich beim Start an.
    def refresh_kaufda(self) -> None:
        fresh = time.monotonic() - self._kaufda[0] < CACHE_SECONDS
        if not self.kaufda or fresh or (self._kaufda_task and not self._kaufda_task.done()):
            return
        self._kaufda_task = asyncio.create_task(self._load_kaufda())

    async def _load_kaufda(self) -> None:
        try:
            offers = await self.kaufda.all_offers()
            self._kaufda = (time.monotonic(), offers)
            self.kaufda_status = "ok"
            log.info("kaufDA: %d Produkte geladen", len(offers))
        except Exception as exc:  # noqa: BLE001 – auch ein geändertes Seitenformat soll nur Globus abschalten
            log.warning("kaufDA nicht erreichbar: %r", exc)
            # In zehn Minuten nochmal probieren, nicht bei jedem Klick
            self._kaufda = (time.monotonic() - CACHE_SECONDS + 600, self._kaufda[1])
            self.kaufda_status = "fehler" if not self._kaufda[1] else "ok"

    async def _marktguru(self, term: str) -> list[Offer]:
        hit = self._searches.get(term)
        if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
        async with self._locks.setdefault(term, asyncio.Lock()):
            hit = self._searches.get(term)  # kam inzwischen von einer gleichzeitigen Anfrage?
            if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
                return hit[1]
            async with self._limit:
                # Gesucht wird nach dem Hauptwort; "rote" filtert danach die Wertung
                found = await self.marktguru.search(term.split()[-1], self.zip_code)
            found = [o for o in found if retailer_allowed(o.retailer, self.retailers)]
            self._searches[term] = (time.monotonic(), found)
            return found

    async def lookup(self, names: list[str]) -> dict:
        """Zu jedem Zutatennamen die besten Angebote. Antwort: {"items": [...], "globus": Status, "error": ...}"""
        self.refresh_kaufda()
        terms = [(name, search_term(name)) for name in names]  # Liste: Namen können doppelt vorkommen
        unique = sorted({t for _, t in terms if t})
        results = await asyncio.gather(*(self._marktguru(t) for t in unique), return_exceptions=True)
        by_term, failed = {}, 0
        for term, result in zip(unique, results):
            if isinstance(result, Exception):
                log.warning("Marktguru-Suche %r fehlgeschlagen: %s", term, result)
                failed += 1
                result = []
            by_term[term] = result + self._kaufda[1]
        items = [{"name": name, "offers": [o.view() for o in best_offers(t, by_term[t])] if t else []}
                 for name, t in terms]
        error = "Marktguru gerade nicht erreichbar" if unique and failed == len(unique) else ""
        return {"items": items, "globus": self.kaufda_status, "error": error}
