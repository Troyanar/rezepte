# Rezepte

Private recipe collection with a shopping list. Recipes can be typed in by hand,
read from a screenshot or copied text, or suggested by an AI.
A click on the small cart puts the ingredients on the list; identical ingredients are added up.

**Stack:** Python 3, aiohttp, SQLite, vanilla JS. Entry point: `app.py`.

## Start (Windows)

Double-click `start.bat`. The first time, it sets up the Python environment and asks for the
shared password. Then open http://localhost:8300 in the browser.

Manual setup:
```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env      # optional: AI keys, region for the deals
.venv\Scripts\python set_password.py
.venv\Scripts\python app.py
```
The database (`rezepte.db`) is created on first start.

Change the password on the site under **settings**, or (if forgotten) with
`.venv\Scripts\python set_password.py`. All other devices then have to log in again.

## Public view

Without logging in, the site shows the recipes and the shopping list read-only: no ticking off,
no editing, no AI and no settings. A "Log in" button sits in the top right.
Both can be switched on and off separately under Settings → Public (on by default).

## Photos and categories

Every recipe has a category (soups, main dishes, …) and a small photo from Unsplash.
The free key is available at unsplash.com/developers ("New Application", then take the **Access Key**);
enter it under **Settings → Images** (or `UNSPLASH_ACCESS_KEY` in `.env`).
New recipes get a photo automatically when saved; the form lets you pick another one.
"Fill without photo" adds photos to older recipes (20 at a time). Images are loaded directly from Unsplash,
photographer and Unsplash are credited on the recipe page, and a chosen photo is reported to Unsplash as
used – both are required by their API rules. New keys run in demo mode with 50 requests
per hour, which is enough for a private collection.

(Until October 2026 the photos came from Pexels, which no longer issues new API keys.)

**Own photos:** tap "Own photo" in the form, drag an image onto the photo or paste it with Ctrl+V.
The browser shrinks it first (1600 px for the recipe, 600 px for the list); it is stored in the folder
`fotos/` next to the database (change with `PHOTO_DIR` in `.env`). Photos no longer used by any recipe are
removed by the server after an hour. **When backing up or moving, copy `rezepte.db` and `fotos/` together.**

## Servings

Under **Settings → Personen** you set how many people you usually cook for (default 3). AI suggestions come in that
amount, and the recipe form offers a button "Zutaten von 2 auf 3 Personen umrechnen". Typing another number into
"Personen" offers to convert to that number instead. Amounts are rounded the way you weigh things: grams to 5 or 10,
spoons to halves, eggs to whole ones. Numbers inside the preparation steps are not changed.
On the recipe page, − and + still scale the amounts just for viewing and for the shopping list.

## Diet tags

Recipes get the tags **Vegan**, **Vegetarisch**, **Low Carb**, **High Protein** and **Schnell** automatically;
the row "Ernährung" above the list filters by them (several at once = all of them must apply).
The site works them out from the ingredients, the same way it estimates calories:

| Tag          | Rule                                                                                    |
|--------------|-----------------------------------------------------------------------------------------|
| Vegetarisch  | no meat or fish, also no meat broth, fish/oyster sauce, Worcester sauce or gelatine     |
| Vegan        | vegetarian and no milk products, egg, honey or mayo (unless "vegan", oat milk, …)        |
| Low Carb     | at most 20 g carbs per person and under 30 % of the calories (no servings: under 20 %)  |
| High Protein | at least 25 % of the calories from protein and 20 g per person                          |
| Schnell      | ready in 30 minutes or less                                                             |

Low Carb and High Protein are only given when every measured ingredient is known to the nutrient table
and no side dish (rice, noodles, bread …) is listed without an amount.
If a tag is wrong, tap it in the recipe form: the change is stored with the recipe (✎) and wins over the detection.
Tapping it back to what was detected removes the correction again.

## Setting up the AI

Easiest under **settings → ai**: enter a key and click [test].
Alternatively in `.env` (values on the site take priority). Only providers with a key show up when adding a recipe.

| Provider   | Get a key                         | Setting                                     |
|------------|-----------------------------------|---------------------------------------------|
| DeepSeek   | platform.deepseek.com             | `DEEPSEEK_API_KEY`, model `deepseek-flash`  |
| Gemini     | aistudio.google.com/apikey        | `GEMINI_API_KEY`, model `gemini-3.8-flash`  |
| OpenRouter | openrouter.ai/keys                | `OPENROUTER_API_KEY` + `OPENROUTER_MODEL`   |

All three use the OpenAI-compatible chat API (`ai.py`, provider list in `config.py`).
To read images, the chosen model has to understand images. AI results are always just
a draft in the form and are only kept with [save]. `AI_LIMIT_PER_HOUR` keeps costs down.

## Supermarket deals

When logged in, every recipe shows below the ingredients which of them are currently on sale (Lidl, Aldi, Kaufland, Rewe,
Edeka, Netto, Penny via Marktguru, Globus via kaufDA – the same sources as the Angebote-Bot). Each
ingredient gets a yellow price tag with the best-matching deal. The shopping list shows the tags too,
and on the right what is currently cheap where, sorted by store.

- Searches only run when a recipe or the list is opened; each search is then valid for an hour.
  Globus loads in the background at startup (about half a minute); until then only its deals are missing.
- Salt, pepper and water are skipped. Matching uses the main noun ("rote Zwiebeln" finds
  "Zwiebeln" and "Speisezwiebeln" but not "Zwiebelmettwurst"); it is not perfectly accurate.
- Place and stores: `OFFERS_ZIP`, `OFFERS_KAUFDA_CITY`, `OFFERS_LAT`/`OFFERS_LNG`, `OFFERS_RETAILERS`,
  `OFFERS_KAUFDA_RETAILERS` in `.env` (default: Zweibrücken).
- Both sources are unofficial and their terms forbid automated scraping. Private use only.
  The code (`angebote.py`) is taken from the Angebote-Bot; if a source changes there, update it here too.

## Importing recipes from the TikTok bot

Drag the TikTok bot's txt file onto `TikTok-Import.bat`
or run `.venv\Scripts\python import_tiktok.py file.txt`. Announcements without ingredients and steps
are skipped; each recipe goes through the AI on its own (like "From text", including translation; tips end up
in the note) and is saved directly. The source is the video link, so videos that were already imported
are skipped on the next run – you can stop with Ctrl+C and continue later at any time.
The import fetches photos until the Unsplash hourly limit is reached; fill in the rest with "fill without photo" in the settings.
`--probe` only counts, `--max 5` only takes the first five.

## Layout

- `app.py` web server (aiohttp), login, API
- `db.py` SQLite (`rezepte.db`): recipes, shopping list, sessions, settings (including password hash and AI keys)
- `ai.py` prompts and AI connection
- `images.py` photo search on Unsplash
- `angebote.py` supermarket deals for the ingredients (Marktguru, kaufDA)
- `import_tiktok.py` import from the TikTok bot's txt file
- `static/` UI (single page, no build step)

## Security

- One shared password (at least 8 characters); a login lasts 60 days as long as the site is used.
- Login lockout: 8 failed attempts per IP and 40 in total within 15 minutes.
- Writes only from the site itself (JSON + Sec-Fetch-Site), plus security headers (CSP, no embedding).
- `TRUSTED_PROXIES`: only this proxy is trusted to pass on the visitor's IP. The default is fine locally.

## Hosting with Docker

`docker compose up -d` starts the container `rezepte` (port 8300, only on 127.0.0.1). `docker-compose.yml`
expects an external Docker network `npm_default` for a reverse proxy (e.g. Nginx Proxy Manager); without
a proxy, remove the network entry. Data lives in `data/` (database + `fotos/`). Set `TRUSTED_PROXIES` to the
proxy's network.
