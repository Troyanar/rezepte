"""Rezepte von einer KI vorschlagen oder aus Bild/Text auslesen lassen.

Alle Anbieter sprechen das OpenAI-Chat-Format, der Unterschied steckt nur in config.AI_PROVIDERS.
Das Ergebnis ist immer ein Entwurf: Er landet im Formular und wird erst nach Prüfung gespeichert.
"""

from __future__ import annotations

import json
import re

import httpx

import config
import db

FORMAT = """Antworte ausschließlich mit einem JSON-Objekt in genau diesem Aufbau:
{"title": "...", "servings": 4, "minutes": 45,
 "ingredients": [{"amount": 250, "unit": "g", "name": "Linsen"}, {"amount": 2, "unit": "", "name": "Karotten"}],
 "steps": ["Zwiebel fein würfeln und in Öl glasig dünsten.", "..."],
 "notes": "", "category": "Suppen", "image_query": "lentil soup"}

Regeln:
- Alles auf Deutsch.
- "amount" ist eine Zahl oder null (z.B. bei "etwas Salz"). Brüche als Dezimalzahl (0.5).
- "unit" nur aus: g, kg, ml, l, EL, TL, Prise, Msp., Bund, Dose, Pck., Zehe, Scheibe, Becher, Tasse, Glas, Zweig, Würfel.
  Bei Stückzahlen ("2 Karotten") bleibt "unit" leer.
- "name" ohne Menge und ohne Einheit, Zusätze wie "fein gehackt" kommen in den Zubereitungsschritt.
- "steps" sind einzelne Arbeitsschritte ohne Nummerierung.
- "minutes" ist die Gesamtzeit in Minuten oder null, "servings" die Portionen oder null.
- "category" genau eine aus: """ + ", ".join(db.CATEGORIES) + """.
- "image_query" ist ein kurzer englischer Suchbegriff (2-4 Wörter) für ein Foto des fertigen Gerichts."""

GENERATE = f"""Du schreibst Rezepte für eine private Rezeptsammlung, wie sie in einem Kochheft stehen.
Normale Hausmannskost-Sprache: kurze, klare Schritte, keine Werbesprache, keine Superlative,
keine Emojis, kein Einleitungstext. Der Titel ist schlicht ("Linsensuppe mit Speck", nicht "Die perfekte Linsensuppe").
Mengen realistisch für den Alltag, Zutaten aus einem normalen deutschen Supermarkt.

{FORMAT}"""

EXTRACT = f"""Du überträgst ein Rezept aus einem Foto, Screenshot oder Text in eine Rezeptsammlung.
Übernimm das Rezept so originalgetreu wie möglich und erfinde nichts dazu. Fehlende Angaben bleiben null bzw. leer.
Ist eine Quelle erkennbar (Webseite, Buch, Person), schreib sie in "notes".
Ist kein Rezept zu erkennen, antworte mit {{"error": "kurze Begründung"}}.

{FORMAT}"""


class AIError(Exception):
    pass


def providers(store: db.Store) -> dict[str, dict]:
    """Anbieter mit den Werten aus den Einstellungen; was dort fehlt, kommt aus der .env."""
    result = {}
    for pid, base in config.AI_PROVIDERS.items():
        result[pid] = {**base, "id": pid,
                       "key": store.setting(f"ai.{pid}.key", base["key"]),
                       "model": store.setting(f"ai.{pid}.model", base["model"])}
    return result


def default_id(store: db.Store) -> str:
    return store.setting("ai.default", config.AI_DEFAULT)


def available(store: db.Store) -> list[dict]:
    return [{"id": p["id"], "label": p["label"]} for p in providers(store).values() if p["key"] and p["model"]]


def pick(store: db.Store, provider_id: str | None) -> dict:
    ids = [p["id"] for p in available(store)]
    if not ids:
        raise AIError("Kein KI-Anbieter eingerichtet (API-Key unter einstellungen eintragen)")
    if provider_id not in ids:
        provider_id = default_id(store) if default_id(store) in ids else ids[0]
    return providers(store)[provider_id]


def _find_json(content: str) -> dict | None:
    """Erstes vollständiges JSON-Objekt im Text, auch mit ```json-Block oder Text davor/danach."""
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", content):
        try:
            data, _ = decoder.raw_decode(content, match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _parse(content: str) -> dict:
    if "{" not in content:
        raise AIError("Die KI hat kein Rezept geliefert, bitte nochmal versuchen")
    data = _find_json(content)
    if data is None:
        raise AIError("Die Antwort der KI war unvollständig, bitte nochmal versuchen")
    if data.get("error"):
        raise AIError(f"Kein Rezept erkannt: {str(data['error'])[:200]}")
    try:
        return db.clean_recipe(data)
    except ValueError as exc:
        raise AIError(str(exc)) from None


def _text_of(content) -> str:
    """Manche Anbieter liefern den Text als Liste von Teilen statt als String."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict) and isinstance(p.get("text"), str))
    return ""


async def _chat(provider: dict, system: str, user_content, temperature: float, json_out: bool = True) -> str:
    body = {
        "model": provider["model"],
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_content}],
        "temperature": temperature,
    }
    if provider["json_mode"] and json_out:
        body["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {provider['key']}"}
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(provider["url"], json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise AIError(f"{provider['label']} nicht erreichbar ({type(exc).__name__})") from None
    if resp.status_code in (401, 403):
        raise AIError(f"{provider['label']}: API-Key ungültig oder ohne Berechtigung")
    if resp.status_code == 402:
        raise AIError(f"{provider['label']}: Guthaben aufgebraucht")
    if resp.status_code == 429:
        raise AIError(f"{provider['label']}: zu viele Anfragen, kurz warten")
    if resp.status_code >= 400:
        raise AIError(f"{provider['label']} meldet Fehler {resp.status_code}: {resp.text[:200]}")
    try:
        return _text_of(resp.json()["choices"][0]["message"]["content"])
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise AIError(f"{provider['label']}: unerwartete Antwort") from None


async def generate(provider: dict, wish: str) -> dict:
    return _parse(await _chat(provider, GENERATE, f"Rezeptwunsch: {wish}", 0.8))


async def from_text(provider: dict, text: str, extra: str = "") -> dict:
    """extra: zusätzliche Regeln, z. B. vom TikTok-Import."""
    system = f"{EXTRACT}\n\n{extra}" if extra else EXTRACT
    return _parse(await _chat(provider, system, f"Rezepttext:\n\n{text}", 0.1))


async def from_images(provider: dict, images: list[str], hint: str) -> dict:
    content: list[dict] = [{"type": "text", "text": "Übertrage dieses Rezept." + (f" Hinweis: {hint}" if hint else "")}]
    content += [{"type": "image_url", "image_url": {"url": url}} for url in images]
    return _parse(await _chat(provider, EXTRACT, content, 0.1))


async def ping(provider: dict) -> str:
    """Kurzer Test, ob Key und Modell funktionieren."""
    if not provider["key"] or not provider["model"]:
        raise AIError("Key und Modell eintragen")
    answer = await _chat(provider, "Antworte nur mit dem Wort ok.", "Test", 0, json_out=False)
    return answer.strip()[:40] or "(leere Antwort)"
