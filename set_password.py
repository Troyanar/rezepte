"""Setzt das gemeinsame Passwort der Seite (nur als Hash in der Datenbank).

    python set_password.py              Passwort setzen oder ändern, meldet alle Geräte ab
    python set_password.py --if-missing nur fragen, wenn noch keins gesetzt ist
Später geht das Ändern auch auf der Seite unter "einstellungen".
"""

from __future__ import annotations

import getpass
import sys

import auth
import config
import db


def main() -> None:
    store = db.Store(config.DB_PATH)
    if "--if-missing" in sys.argv and store.setting("password_hash"):
        return
    while True:
        password = getpass.getpass("Neues Passwort für die Rezeptseite: ")
        if len(password) < auth.MIN_PASSWORD:
            print(f"Bitte mindestens {auth.MIN_PASSWORD} Zeichen.")
            continue
        if getpass.getpass("Nochmal zur Kontrolle: ") != password:
            print("Stimmt nicht überein, nochmal.")
            continue
        break
    store.set_setting("password_hash", auth.hash_password(password))
    store.end_other_sessions(None)
    print("Passwort gespeichert.")


if __name__ == "__main__":
    main()
