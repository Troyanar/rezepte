"""SQLite-Speicher: Rezepte, Einkaufszettel und Anmelde-Sitzungen."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS recipes (
    id          INTEGER PRIMARY KEY,
    title       TEXT NOT NULL,
    servings    INTEGER,
    minutes     INTEGER,
    source      TEXT NOT NULL DEFAULT '',
    ingredients TEXT NOT NULL DEFAULT '[]',
    steps       TEXT NOT NULL DEFAULT '[]',
    notes       TEXT NOT NULL DEFAULT '',
    created     TEXT NOT NULL,
    updated     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shopping (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL,
    amount  REAL,
    unit    TEXT NOT NULL DEFAULT '',
    sources TEXT NOT NULL DEFAULT '[]',
    checked INTEGER NOT NULL DEFAULT 0,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    expires    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Für den Einkaufszettel wird auf die kleinere Einheit umgerechnet, damit sich
# "1 kg Mehl" und "250 g Mehl" (oder 1 EL und 2 TL) zusammenrechnen lassen.
BASE_UNITS = {"kg": ("g", 1000), "l": ("ml", 1000), "cl": ("ml", 10), "el": ("TL", 3), "stk.": ("", 1)}
# Zusatz, der nur im Rezept zählt: "Sojasauce (für die Marinade)" ist im Laden einfach Sojasauce
USE_NOTE = re.compile(r"\s*\((?:für|zum|zur|als)\b[^)]*\)\s*$", re.I)

# Der Zettel leert sich von selbst, wenn so lange nichts mehr daran passiert ist
# (nichts dazugelegt, abgehakt oder gestrichen) – so bleibt eine laufende Einkaufsliste zusammen.
SHOPPING_IDLE_HOURS = 48

# Eigene Fotos: zufälliger Name, groß und klein ("_s"), immer JPEG
OWN_PHOTO = re.compile(r"^/fotos/[0-9a-f]{32}(?:_s)?\.jpg$")

LIMITS = {"title": 200, "source": 300, "notes": 4000, "name": 200, "unit": 20, "step": 2000}

CATEGORIES = ["Frühstück", "Suppen", "Salate", "Hauptgerichte", "Beilagen", "Snacks", "Kuchen & Süßes", "Getränke"]

# Ernährungs-Tags erkennt die Seite selbst aus den Zutaten; gespeichert werden nur Handkorrekturen
# ({"Vegan": false} = "ist nicht vegan, auch wenn es so aussieht")
DIETS = ["Vegan", "Vegetarisch", "Low Carb", "High Protein", "Schnell"]

# Spalten, die nach der ersten Version dazugekommen sind (werden beim Start nachgerüstet)
NEW_COLUMNS = {"category": "TEXT NOT NULL DEFAULT ''", "image": "TEXT NOT NULL DEFAULT ''",
               "tags": "TEXT NOT NULL DEFAULT '{}'"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _text(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def to_int(value, lo: int, hi: int) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(float(value))
    except (TypeError, ValueError, OverflowError):  # OverflowError: "inf", 1e999
        return None
    return number if lo <= number <= hi else None


def to_bool(value) -> bool:
    """JSON-Wahrheitswert; der Text "false"/"0" zählt als falsch."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "ja", "on")
    return value is True or (isinstance(value, int) and value == 1)


def _amount(value) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", "."))
    except ValueError:
        return None
    return round(number, 3) if 0 < number < 1_000_000 else None


def _list(value) -> list:
    """Listen bleiben Listen; ein einzelner Text (kommt bei KI-Antworten vor) wird zeilenweise aufgeteilt."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return value.splitlines()
    return []


def clean_ingredient(raw) -> dict | None:
    if isinstance(raw, str):  # KI liefert manchmal nur "250 g Linsen" als Text
        raw = {"name": raw}
    if not isinstance(raw, dict):
        return None
    name = _text(raw.get("name"), LIMITS["name"])
    if not name:
        return None
    return {"amount": _amount(raw.get("amount")), "unit": _text(raw.get("unit"), LIMITS["unit"]), "name": name}


def clean_image(raw) -> dict | None:
    """Eigene hochgeladene Fotos oder Fotos von Unsplash (die werden direkt von dort geladen), samt Fotograf
    für die Namensnennung. "download" ist die Adresse, über die Unsplash die Verwendung gemeldet bekommt."""
    if not isinstance(raw, dict):
        return None
    small, large, url, download = (str(raw.get(k) or "") for k in ("small", "large", "url", "download"))
    if OWN_PHOTO.match(small) and OWN_PHOTO.match(large):
        return {"small": small, "large": large, "by": "", "url": "", "download": ""}
    if not (small.startswith("https://images.unsplash.com/") and large.startswith("https://images.unsplash.com/")):
        return None
    return {"small": small[:600], "large": large[:600], "by": _text(raw.get("by"), 120),
            "url": url[:300] if url.startswith("https://unsplash.com/") else "",
            "download": download[:300] if download.startswith("https://api.unsplash.com/photos/") else ""}


def clean_tags(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    return {d: raw[d] for d in DIETS if isinstance(raw.get(d), bool)}


def clean_recipe(raw) -> dict:
    """Prüft und kürzt Eingaben vom Browser oder von der KI. Wirft ValueError ohne Titel."""
    if not isinstance(raw, dict):
        raise ValueError("Ungültige Daten")
    title = _text(raw.get("title"), LIMITS["title"])
    if not title:
        raise ValueError("Das Rezept braucht einen Titel")
    ingredients = [i for i in map(clean_ingredient, _list(raw.get("ingredients"))) if i][:100]
    steps = [_text(s, LIMITS["step"]) for s in _list(raw.get("steps"))
             if isinstance(s, (str, int, float)) and _text(s, LIMITS["step"])][:60]
    return {
        "title": title,
        "servings": to_int(raw.get("servings"), 1, 100),
        "minutes": to_int(raw.get("minutes"), 1, 10_000),
        "source": _text(raw.get("source"), LIMITS["source"]),
        "ingredients": ingredients,
        "steps": steps,
        "notes": _text(raw.get("notes"), LIMITS["notes"]),
        "category": raw.get("category") if raw.get("category") in CATEGORIES else "",
        "image": clean_image(raw.get("image")),
        "tags": clean_tags(raw.get("tags")),
        # nur ein Suchbegriff für das Foto, wird nicht gespeichert
        "image_query": _text(raw.get("image_query"), 80),
    }


def shop_name(name: str) -> str:
    return USE_NOTE.sub("", name).strip() or name


def _shop_key(name: str, unit: str, amount: float | None) -> tuple:
    """Woran der Zettel gleiche Zutaten erkennt: Groß/klein, Soße/Sauce, Bindestriche
    und Mehrzahl ("Zwiebel"/"Zwiebeln", "Ei"/"Eier") spielen keine Rolle."""
    k = shop_name(name).casefold().replace("soße", "sauce").replace("sosse", "sauce")
    k = re.sub(r"[\s\-]+", "", k)
    for end in ("en", "er", "n", "e", "s"):
        if k.endswith(end) and len(k) - len(end) >= 2:
            k = k[:-len(end)]
            break
    return k, unit.casefold(), amount is None


def _to_base(amount: float | None, unit: str) -> tuple[float | None, str]:
    if unit.casefold() in BASE_UNITS:
        unit, factor = BASE_UNITS[unit.casefold()]
        amount = amount * factor if amount is not None else None
    return amount, unit


class Store:
    def __init__(self, path: Path) -> None:
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        existing = {r["name"] for r in self.conn.execute("PRAGMA table_info(recipes)")}
        for column, definition in NEW_COLUMNS.items():
            if column not in existing:
                self.conn.execute(f"ALTER TABLE recipes ADD COLUMN {column} {definition}")
        self.conn.commit()
        self.tidy_shopping()

    # --- Rezepte ---------------------------------------------------------------
    @staticmethod
    def _recipe(row: sqlite3.Row) -> dict:
        data = dict(row)
        data["ingredients"] = json.loads(data["ingredients"])
        data["steps"] = json.loads(data["steps"])
        data["image"] = json.loads(data["image"]) if data["image"] else None
        data["tags"] = json.loads(data["tags"] or "{}")
        return data

    def recipes(self) -> list[dict]:
        rows = [self._recipe(r) for r in self.conn.execute("SELECT * FROM recipes")]
        # deutsch sortieren: "Äpfel" gehört zu A, nicht hinter Z
        umlaut = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "ss"})
        return sorted(rows, key=lambda r: r["title"].casefold().translate(umlaut))

    def recipe(self, recipe_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        return self._recipe(row) if row else None

    def save_recipe(self, data: dict, recipe_id: int | None = None) -> dict | None:
        values = (
            data["title"], data["servings"], data["minutes"], data["source"],
            json.dumps(data["ingredients"], ensure_ascii=False),
            json.dumps(data["steps"], ensure_ascii=False), data["notes"], data["category"],
            json.dumps(data["image"], ensure_ascii=False) if data["image"] else "",
            json.dumps(data["tags"], ensure_ascii=False), _now(),
        )
        with self.conn:
            if recipe_id is None:
                cur = self.conn.execute(
                    "INSERT INTO recipes (title, servings, minutes, source, ingredients, steps, notes, category, image,"
                    " tags, updated, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (*values, _now()))
                recipe_id = cur.lastrowid
            else:
                cur = self.conn.execute(
                    "UPDATE recipes SET title=?, servings=?, minutes=?, source=?, ingredients=?, steps=?, notes=?,"
                    " category=?, image=?, tags=?, updated=? WHERE id=?", (*values, recipe_id))
                if cur.rowcount == 0:
                    return None
        return self.recipe(recipe_id)

    def set_image(self, recipe_id: int, image: dict | None) -> None:
        with self.conn:
            self.conn.execute("UPDATE recipes SET image = ? WHERE id = ?",
                              (json.dumps(image, ensure_ascii=False) if image else "", recipe_id))

    def delete_recipe(self, recipe_id: int) -> bool:
        with self.conn:
            return self.conn.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,)).rowcount > 0

    # --- Einkaufszettel --------------------------------------------------------
    @staticmethod
    def _item(row: sqlite3.Row) -> dict:
        data = dict(row)
        data["sources"] = json.loads(data["sources"])
        data["checked"] = bool(data["checked"])
        return data

    def shopping(self) -> list[dict]:
        self.expire_shopping()
        rows = self.conn.execute("SELECT * FROM shopping ORDER BY checked, id")
        return [self._item(r) for r in rows]

    def _touch_shopping(self) -> None:
        self.set_setting("shopping.changed", str(time.time()))

    def expire_shopping(self) -> bool:
        """Leert den Zettel, wenn SHOPPING_IDLE_HOURS lang nichts passiert ist. True = geleert."""
        last = self.setting("shopping.changed")
        try:
            idle = time.time() - float(last)
        except ValueError:  # noch nie gemerkt (alter Zettel): ab jetzt zählen statt sofort zu löschen
            self._touch_shopping()
            return False
        if idle < SHOPPING_IDLE_HOURS * 3600:
            return False
        with self.conn:
            gone = self.conn.execute("DELETE FROM shopping").rowcount
        self._touch_shopping()
        return gone > 0

    def add_item(self, name: str, amount: float | None, unit: str, source: str | None = None) -> None:
        """Legt eine Zutat auf den Zettel oder rechnet sie zu einem offenen Eintrag dazu."""
        name = shop_name(name)
        amount, unit = _to_base(amount, unit)
        # Vergleich in Python, weil SQLites lower() keine Umlaute kann ("Öl" vs. "öl")
        key = _shop_key(name, unit, amount)
        existing = next((row for row in self.conn.execute("SELECT * FROM shopping WHERE checked = 0")
                         if _shop_key(row["name"], row["unit"], row["amount"]) == key), None)
        if existing:
            sources = json.loads(existing["sources"])
            if source and source not in sources:
                sources.append(source)
            total = round(existing["amount"] + amount, 3) if amount is not None else None
            self.conn.execute("UPDATE shopping SET amount = ?, sources = ? WHERE id = ?",
                              (total, json.dumps(sources, ensure_ascii=False), existing["id"]))
        else:
            self.conn.execute(
                "INSERT INTO shopping (name, amount, unit, sources, created) VALUES (?, ?, ?, ?, ?)",
                (name, amount, unit, json.dumps([source] if source else [], ensure_ascii=False), _now()))

    def tidy_shopping(self) -> None:
        """Offene Einträge, die nach den aktuellen Regeln dieselbe Zutat sind, zusammenlegen
        (z. B. alte "Sojasauce (für die Sauce)" in EL und "Sojasauce" in TL)."""
        keep: dict[tuple, dict] = {}
        with self.conn:
            for row in self.conn.execute("SELECT * FROM shopping WHERE checked = 0 ORDER BY id").fetchall():
                name = shop_name(row["name"])
                amount, unit = _to_base(row["amount"], row["unit"])
                sources = json.loads(row["sources"])
                first = keep.get(_shop_key(name, unit, amount))
                if first is None:
                    keep[_shop_key(name, unit, amount)] = {
                        "id": row["id"], "name": name, "amount": amount, "unit": unit, "sources": sources,
                        "changed": (name, amount, unit) != (row["name"], row["amount"], row["unit"])}
                    continue
                if amount is not None:
                    first["amount"] = round(first["amount"] + amount, 3)
                first["sources"] += [s for s in sources if s not in first["sources"]]
                first["changed"] = True
                self.conn.execute("DELETE FROM shopping WHERE id = ?", (row["id"],))
            for item in keep.values():
                if item["changed"]:
                    self.conn.execute("UPDATE shopping SET name = ?, amount = ?, unit = ?, sources = ? WHERE id = ?",
                                      (item["name"], item["amount"], item["unit"],
                                       json.dumps(item["sources"], ensure_ascii=False), item["id"]))

    def add_recipe_to_shopping(self, recipe: dict, servings: int | None) -> int:
        factor = 1.0
        if servings and recipe["servings"]:
            factor = servings / recipe["servings"]
        self.expire_shopping()  # alter, liegengebliebener Zettel: neu anfangen statt dazuzurechnen
        with self.conn:
            for ing in recipe["ingredients"]:
                amount = round(ing["amount"] * factor, 3) if ing["amount"] is not None else None
                self.add_item(ing["name"], amount, ing["unit"], recipe["title"])
        self._touch_shopping()
        return len(recipe["ingredients"])

    def add_manual_item(self, raw: dict) -> bool:
        item = clean_ingredient(raw)
        if not item:
            return False
        self.expire_shopping()
        with self.conn:
            self.add_item(item["name"], item["amount"], item["unit"])
        self._touch_shopping()
        return True

    def set_checked(self, item_id: int, checked: bool) -> None:
        with self.conn:
            self.conn.execute("UPDATE shopping SET checked = ? WHERE id = ?", (int(checked), item_id))
        self._touch_shopping()

    def delete_item(self, item_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM shopping WHERE id = ?", (item_id,))
        self._touch_shopping()

    def clear_shopping(self, only_checked: bool) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM shopping" + (" WHERE checked = 1" if only_checked else ""))
        self._touch_shopping()

    # --- Sitzungen (nur der Hash des Tokens wird gespeichert) -----------------------
    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def add_session(self, token: str, seconds: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
            self.conn.execute("INSERT INTO sessions VALUES (?, ?)", (self._hash(token), time.time() + seconds))

    def session_valid(self, token: str | None, seconds: int) -> bool:
        if not token:
            return False
        with self.conn:
            cur = self.conn.execute("UPDATE sessions SET expires = ? WHERE token_hash = ? AND expires > ?",
                                    (time.time() + seconds, self._hash(token), time.time()))
        return cur.rowcount > 0

    def end_session(self, token: str | None) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM sessions WHERE token_hash = ?", (self._hash(token or ""),))

    def end_other_sessions(self, token: str | None) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM sessions WHERE token_hash != ?", (self._hash(token or ""),))

    # --- Einstellungen (Schlüssel/Wert; was hier fehlt, kommt aus der .env) ------------
    def setting(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str | None) -> None:
        with self.conn:
            if value is None:
                self.conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            else:
                self.conn.execute("INSERT INTO settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                                  (key, value))
