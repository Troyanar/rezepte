"""Einstellungen aus der .env (siehe .env.example)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
load_dotenv(ENV_FILE)


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    """Zahl aus der .env; Tippfehler führen zur Vorgabe statt zum Absturz beim Start."""
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if lo <= value <= hi else default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


HOST = os.getenv("HOST", "127.0.0.1")
PORT = _int_env("PORT", 8300, 1, 65535)
DB_PATH = Path(os.getenv("DB_PATH", BASE_DIR / "rezepte.db"))
# Eigene Fotos liegen neben der Datenbank (beim Sichern beides mitnehmen)
PHOTO_DIR = Path(os.getenv("PHOTO_DIR", DB_PATH.parent / "fotos"))
# Nur von diesen Adressen werden X-Real-IP/X-Forwarded-For geglaubt (Proxy auf demselben Rechner).
# Läuft der Nginx Proxy Manager in Docker, hier dessen Adresse ergänzen, z. B. 172.17.0.1
TRUSTED_PROXIES = {p.strip() for p in os.getenv("TRUSTED_PROXIES", "127.0.0.1,::1").split(",") if p.strip()}

# KI-Anbieter: alle über die OpenAI-kompatible Chat-Schnittstelle.
# Keys und Modelle lassen sich auf der Seite unter "einstellungen" ändern; die Werte hier sind die Vorgabe.
# Ein Anbieter erscheint auf der Seite nur, wenn sein Key (und Modell) gesetzt ist.
AI_PROVIDERS = {
    "deepseek": {
        "label": "DeepSeek",
        "url": "https://api.deepseek.com/chat/completions",
        "key": os.getenv("DEEPSEEK_API_KEY", "").strip(),
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-flash").strip(),
        "json_mode": True,
    },
    "gemini": {
        "label": "Gemini",
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "key": os.getenv("GEMINI_API_KEY", "").strip(),
        "model": os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip(),
        "json_mode": False,
    },
    "openrouter": {
        "label": "OpenRouter",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "key": os.getenv("OPENROUTER_API_KEY", "").strip(),
        "model": os.getenv("OPENROUTER_MODEL", "").strip(),
        "json_mode": False,
    },
}
AI_DEFAULT = os.getenv("AI_DEFAULT", "").strip()

# Supermarkt-Angebote zu den Zutaten (wie beim Angebote-Bot: Zweibrücken)
OFFERS_ZIP = os.getenv("OFFERS_ZIP", "").strip() or "66482"
OFFERS_KAUFDA_CITY = os.getenv("OFFERS_KAUFDA_CITY", "").strip() or "Zweibruecken"
OFFERS_LAT = _float_env("OFFERS_LAT", 49.2497)
OFFERS_LNG = _float_env("OFFERS_LNG", 7.3606)
OFFERS_RETAILERS = [r.strip() for r in (os.getenv("OFFERS_RETAILERS", "").strip()
                    or "Lidl,Aldi Süd,Aldi Nord,Kaufland,Rewe,Edeka,Netto Marken-Discount,Penny").split(",") if r.strip()]
# Märkte, die Marktguru nicht hat und die über kaufDA kommen (leer = aus)
OFFERS_KAUFDA_RETAILERS = [r.strip() for r in os.getenv("OFFERS_KAUFDA_RETAILERS", "Globus").split(",") if r.strip()]
UNSPLASH_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "").strip()
AI_LIMIT_PER_HOUR = _int_env("AI_LIMIT_PER_HOUR", 40, 1, 10_000)
# Digimon-Partner: "Gekocht!"-Knopf im Rezept öffnet diese Seite (leer = Knopf aus)
_partner = os.getenv("PARTNER_URL", "").strip().rstrip("/")
PARTNER_URL = _partner if _partner.startswith(("https://", "http://localhost", "http://127.0.0.1")) else ""
