# Rezepte

Recipe collection with a shopping list, AI help and supermarket deals – a small self-hosted web app.

## Features
- Recipes typed in by hand, read by AI from a screenshot or text, or suggested by AI (DeepSeek, Gemini, OpenRouter via the OpenAI-compatible API)
- Shopping list: add a recipe's ingredients with one click, merged and grouped
- Photos from Unsplash or your own uploads, categories as filters
- Current supermarket deals per ingredient (Marktguru, kaufDA)
- Optional public read-only view; editing behind one shared password (rate-limited login)
- Import of recipe text files, Docker deployment

## Stack
Python 3, aiohttp, SQLite, vanilla JS. Entry point: `app.py`.

## Setup
```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env      # optional: AI keys, region for the deals
.venv\Scripts\python set_password.py
.venv\Scripts\python app.py # http://localhost:8300
```
Or with Docker: `docker compose up -d`.

German documentation: [README.de.md](README.de.md)
