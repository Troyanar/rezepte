"""Rezepte aus der Textdatei des TikTok-Bots in die Sammlung übernehmen.

    python import_tiktok.py beschreibungen_xyz.txt          fragt einmal nach, dann geht's los
    python import_tiktok.py datei.txt --probe               nur zählen, nichts an die KI schicken
    python import_tiktok.py datei.txt --max 5               nur die ersten 5 (zum Ausprobieren)

Jede Beschreibung geht einzeln durch dieselbe KI wie "Aus Text" auf der Seite (Anbieter und Keys
aus den Einstellungen) und wird direkt gespeichert. Als Quelle steht der Link zum Video drin:
Schon importierte Videos werden beim nächsten Lauf übersprungen, man kann also jederzeit
abbrechen (Strg+C) und später weitermachen oder eine neuere Datei vom Bot einspielen.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

import ai
import config
import db
import images

TRENNER = re.compile(r"^-{60}$", re.M)
# Ein Rezept hat Zutaten UND Schritte; sonst ist es meist eine Ankündigung ("Day 1 drops tomorrow!")
ZUTATEN = re.compile(r"ingredients|zutaten", re.I)
SCHRITTE = re.compile(r"method|zubereitung|instructions|directions|anleitung|steps|\b1\.\s", re.I)


def ist_rezept(text: str) -> bool:
    return bool(ZUTATEN.search(text) and SCHRITTE.search(text))

EXTRA = """Zusatz für Rezepte aus TikTok-Beschreibungen:
- Titel: der Name des Gerichts in normaler Schreibweise (nicht in Großbuchstaben), ohne "Day 4/50" o. Ä.
  Japanische oder andere fremde Namen bleiben, bei Bedarf mit kurzer deutscher Erklärung,
  z. B. "Kakuni (geschmorter Schweinebauch)".
- Englische Einheiten umrechnen: tbsp = EL, tsp = TL, cup = 240 ml, oz = 28 g, lb = 450 g.
  "pinch"/"a pinch" = 1 Prise, "as needed" = Menge null.
- Gruppen wie [Seasoning] oder [Sauce] nicht als eigene Zutat. Kommt eine Zutat in mehreren Gruppen vor,
  bekommt jede Zeile einen kurzen Zusatz im Namen, z. B. "Sojasauce (für die Soße)".
- Tipps und Varianten ("KEY POINTS", "TRADITIONAL OPTION" usw.) kurz auf Deutsch in "notes".
- Hashtags, Emojis, Werbe- und Haftungshinweise ("DISCLAIMER", "not affiliated") komplett weglassen."""


def lese_datei(pfad: Path) -> list[dict]:
    """Blöcke aus der txt des Bots: erste Zeile "autor | datum | url", danach die Beschreibung."""
    text = pfad.read_text(encoding="utf-8-sig")
    videos = []
    for block in TRENNER.split(text):
        zeilen = block.strip().splitlines()
        if not zeilen:
            continue
        kopf = [t.strip() for t in zeilen[0].split("|")]
        url = kopf[-1] if kopf[-1].startswith("http") else ""
        videos.append({"autor": kopf[0] if len(kopf) > 1 else "", "url": url,
                       "text": "\n".join(zeilen[1:]).strip()})
    return videos


def quelle(video: dict) -> str:
    return f"TikTok @{video['autor']}: {video['url']}" if video["autor"] else video["url"]


async def importieren(args) -> None:
    store = db.Store(config.DB_PATH)
    videos = lese_datei(Path(args.datei))
    if not videos:
        sys.exit("In der Datei stehen keine Videos (ist es die txt vom TikTok-Bot?).")

    vorhanden = " ".join(r["source"] for r in store.recipes())
    neu = [v for v in videos if v["url"] and v["url"] not in vorhanden]
    rezepte = [v for v in neu if args.alle or ist_rezept(v["text"])]
    print(f"{len(videos)} Videos in der Datei, {len(videos) - len(neu)} schon importiert, "
          f"{len(neu) - len(rezepte)} ohne Rezept, {len(rezepte)} neue Rezepte.")
    if args.max:
        rezepte = rezepte[:args.max]
    if not rezepte or args.probe:
        return

    try:
        provider = ai.pick(store, args.anbieter)
    except ai.AIError as exc:
        sys.exit(str(exc))
    key = "" if args.ohne_fotos else store.setting("unsplash.key", config.UNSPLASH_KEY)
    print(f"KI: {provider['label']} ({provider['model']})" + ("" if key else " · ohne Fotos"))
    if not args.ja and input(f"{len(rezepte)} Rezepte importieren? (j/n) ").strip().lower() not in ("j", "ja", "y"):
        return

    ok, fehler, fotos, fehler_am_stueck = 0, [], 0, 0
    breite = len(str(len(rezepte)))
    for nr, video in enumerate(rezepte, 1):
        print(f"[{nr:>{breite}}/{len(rezepte)}] ", end="", flush=True)
        data = None
        for versuch in range(3):
            try:
                data = await ai.from_text(provider, video["text"], EXTRA)
                break
            except ai.AIError as exc:
                if "zu viele Anfragen" in str(exc) and versuch < 2:
                    print("(kurz warten) ", end="", flush=True)
                    await asyncio.sleep(30)
                    continue
                fehler.append((video["url"], str(exc)))
                print(f"übersprungen: {exc}")
                break
        if data is None:
            fehler_am_stueck += 1
            if fehler_am_stueck >= 5:  # z. B. Key ungültig oder Guthaben leer: nicht 100 Mal dasselbe versuchen
                print("Fünf Fehler hintereinander, Import angehalten.")
                break
            continue
        fehler_am_stueck = 0
        data["source"] = quelle(video)[:db.LIMITS["source"]]
        if key:
            try:
                data["image"] = await images.first_match(key, data["title"], data["image_query"])
            except images.ImageError as exc:
                print(f"(Fotos ab hier später: {exc}) ", end="")
                key = ""  # Stundenlimit: den Rest füllt "ohne Foto füllen" in den Einstellungen
        saved = store.save_recipe(data)
        if data["image"]:
            await images.track(key, data["image"])
            fotos += 1
        ok += 1
        print(f"{saved['title']}  ·  {saved['category'] or 'ohne Kategorie'}")

    print(f"\nFertig: {ok} Rezepte gespeichert, {fotos} davon mit Foto.")
    if ok - fotos:
        print("Fehlende Fotos: auf der Seite unter Einstellungen → Bilder auf „ohne Foto füllen“ (20 pro Klick, "
              "Unsplash erlaubt rund 25 Fotos pro Stunde).")
    if fehler:
        print(f"{len(fehler)} nicht übernommen – beim nächsten Lauf wird es nochmal versucht:")
        for url, grund in fehler:
            print(f"  {url}  ({grund})")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Rezepte aus der txt des TikTok-Bots importieren")
    p.add_argument("datei", help="txt-Datei vom TikTok-Bot")
    p.add_argument("--probe", action="store_true", help="nur zählen, nichts importieren")
    p.add_argument("--max", type=int, help="höchstens so viele Rezepte")
    p.add_argument("--anbieter", help="deepseek, gemini oder openrouter (sonst der vorausgewählte)")
    p.add_argument("--ohne-fotos", action="store_true", help="keine Fotos suchen")
    p.add_argument("--alle", action="store_true", help="auch Beschreibungen ohne Wörter wie 'Ingredients' an die KI schicken")
    p.add_argument("--ja", action="store_true", help="nicht nachfragen")
    try:
        asyncio.run(importieren(p.parse_args()))
    except KeyboardInterrupt:
        print("\nAbgebrochen. Was bis hier gespeichert ist, bleibt; beim nächsten Lauf geht's dahinter weiter.")


if __name__ == "__main__":
    main()
