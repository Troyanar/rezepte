# Rezepte

Private Rezeptsammlung mit Einkaufszettel. Rezepte lassen sich von Hand eintragen,
aus einem Screenshot oder kopierten Text auslesen oder von einer KI vorschlagen lassen.
Ein Klick auf den kleinen Wagen legt die Zutaten auf den Zettel, gleiche Zutaten werden zusammengerechnet.

## Starten (Windows)

`start.bat` doppelklicken. Beim ersten Mal wird die Python-Umgebung eingerichtet und nach dem
gemeinsamen Passwort gefragt. Danach im Browser: http://localhost:8300

Passwort ändern: auf der Seite unter **einstellungen**, oder (falls vergessen)
`.venv\Scripts\python set_password.py`. Danach müssen sich alle anderen Geräte neu anmelden.

## Öffentliche Ansicht

Ohne Anmeldung zeigt die Seite die Rezepte und den Einkaufszettel nur zum Lesen: kein Abhaken,
Bearbeiten, keine KI und keine Einstellungen. Oben rechts gibt es einen Knopf „Anmelden“.
Beides lässt sich unter Einstellungen → Öffentlich getrennt ein- und ausschalten (standardmäßig an).

## Fotos und Kategorien

Jedes Rezept hat eine Kategorie (Suppen, Hauptgerichte, …) und ein kleines Foto von Unsplash.
Den kostenlosen Key gibt es auf unsplash.com/developers („New Application“, dann den **Access Key** nehmen),
eingetragen wird er unter **Einstellungen → Bilder** (oder `UNSPLASH_ACCESS_KEY` in der `.env`).
Neue Rezepte bekommen beim Speichern automatisch ein Foto, im Formular kann man ein anderes wählen.
„Ohne Foto füllen“ holt Fotos für ältere Rezepte nach (20 auf einmal). Die Bilder werden direkt von Unsplash
geladen, Fotograf und Unsplash stehen auf der Rezeptseite, und ein gewähltes Foto wird Unsplash als
verwendet gemeldet – beides verlangen deren API-Regeln. Neue Keys laufen im Demo-Modus mit 50 Anfragen
pro Stunde, für eine private Sammlung reicht das.

(Bis Oktober 2026 kamen die Fotos von Pexels, dort gibt es aber keine neuen API-Keys mehr.)

**Eigene Fotos:** Im Formular auf „Eigenes Foto“ tippen, ein Bild auf das Foto ziehen oder mit Strg+V einfügen.
Der Browser verkleinert es vorher (1600 px fürs Rezept, 600 px für die Liste), gespeichert wird es im Ordner
`fotos/` neben der Datenbank (anders: `PHOTO_DIR` in der `.env`). Fotos, die kein Rezept mehr nutzt, räumt der
Server nach einer Stunde selbst weg. **Beim Sichern oder Umziehen `rezepte.db` und `fotos/` zusammen kopieren.**

## KI einrichten

Am einfachsten unter **einstellungen → ki**: Key eintragen und auf [testen] klicken.
Alternativ in der `.env` (Werte auf der Seite haben Vorrang). Nur Anbieter mit Key erscheinen beim Anlegen.

| Anbieter   | Key holen                         | Eintrag                                     |
|------------|-----------------------------------|---------------------------------------------|
| DeepSeek   | platform.deepseek.com             | `DEEPSEEK_API_KEY`, Modell `deepseek-flash` |
| Gemini     | aistudio.google.com/apikey        | `GEMINI_API_KEY`, Modell `gemini-3.8-flash` |
| OpenRouter | openrouter.ai/keys                | `OPENROUTER_API_KEY` + `OPENROUTER_MODEL`   |

Alle drei laufen über die OpenAI-kompatible Chat-Schnittstelle (`ai.py`, Anbieterliste in `config.py`).
Fürs Auslesen von Bildern muss das gewählte Modell Bilder verstehen. KI-Ergebnisse sind immer nur
ein Entwurf im Formular und werden erst mit [speichern] übernommen. `AI_LIMIT_PER_HOUR` bremst die Kosten.

## Supermarkt-Angebote

Angemeldet zeigt jedes Rezept unter den Zutaten, was davon gerade im Angebot ist (Lidl, Aldi, Kaufland, Rewe,
Edeka, Netto, Penny über Marktguru, Globus über kaufDA – dieselben Quellen wie der Angebote-Bot). Hinter jeder
Zutat steht ein gelbes Preisschild mit dem passendsten Angebot. Auf dem Zettel stehen die Schilder ebenfalls,
und rechts steht, was es wo gerade günstig gibt, nach Markt sortiert.

- Gesucht wird erst beim Öffnen eines Rezepts oder des Zettels, jede Suche gilt dann eine Stunde.
  Globus lädt beim Start im Hintergrund (etwa eine halbe Minute), bis dahin fehlen nur dessen Angebote.
- Salz, Pfeffer und Wasser werden übersprungen. Die Zuordnung geht über das Hauptwort („rote Zwiebeln“ findet
  „Zwiebeln“ und „Speisezwiebeln“, aber keine „Zwiebelmettwurst“), ganz treffsicher ist sie nicht.
- Ort und Märkte: `OFFERS_ZIP`, `OFFERS_KAUFDA_CITY`, `OFFERS_LAT`/`OFFERS_LNG`, `OFFERS_RETAILERS`,
  `OFFERS_KAUFDA_RETAILERS` in der `.env` (Vorgabe: Zweibrücken).
- Beide Quellen sind inoffiziell, ihre AGB untersagen das automatische Auslesen. Nur privat nutzen.
  Der Code dafür (`angebote.py`) ist aus dem Angebote-Bot übernommen; ändert sich dort eine Quelle, hier mitziehen.

## Rezepte vom TikTok-Bot importieren

Die txt-Datei vom TikTok-Bot (`Documents\Claude\tiktok-beschreibungen`) auf `TikTok-Import.bat` ziehen
oder `.venv\Scripts\python import_tiktok.py datei.txt` aufrufen. Ankündigungen ohne Zutaten und Schritte
werden übersprungen, jedes Rezept geht einzeln durch die KI (wie „Aus Text“, inkl. Übersetzung, Tipps landen
in der Notiz) und wird direkt gespeichert. Quelle ist der Video-Link, darum werden schon importierte Videos
beim nächsten Lauf übersprungen – Abbrechen mit Strg+C und später weitermachen geht jederzeit.
Fotos holt der Import, bis das Unsplash-Stundenlimit erreicht ist, den Rest „ohne Foto füllen“ in den Einstellungen.
`--probe` zählt nur, `--max 5` nimmt nur die ersten fünf.

## Aufbau

- `app.py` Webserver (aiohttp), Login, API
- `db.py` SQLite (`rezepte.db`): Rezepte, Einkaufszettel, Sitzungen, Einstellungen (inkl. Passwort-Hash und KI-Keys)
- `ai.py` Prompts und Anbindung der KI
- `images.py` Fotosuche bei Unsplash
- `angebote.py` Supermarkt-Angebote zu den Zutaten (Marktguru, kaufDA)
- `import_tiktok.py` Import aus der txt des TikTok-Bots
- `static/` Oberfläche (eine Seite, ohne Build-Schritt)

## Sicherheit

- Ein gemeinsames Passwort (mindestens 8 Zeichen), Anmeldung bleibt 60 Tage, solange man die Seite nutzt.
- Login-Sperre: 8 Fehlversuche pro IP und 40 insgesamt in 15 Minuten.
- Schreibzugriffe nur von der eigenen Seite (JSON + Sec-Fetch-Site), dazu Sicherheits-Header (CSP, kein Einbetten).
- `TRUSTED_PROXIES`: Nur diesem Proxy wird die weitergereichte Besucher-IP geglaubt. Lokal reicht die Vorgabe.

## Mit Docker hosten

`docker compose up -d` startet den Container `rezepte` (Port 8300, nur auf 127.0.0.1). Die `docker-compose.yml`
erwartet ein externes Docker-Netz `npm_default` für einen Reverse Proxy (z. B. Nginx Proxy Manager); ohne
Proxy den Netz-Eintrag entfernen. Daten liegen in `data/` (Datenbank + `fotos/`). `TRUSTED_PROXIES` auf das
Netz des Proxys setzen.
