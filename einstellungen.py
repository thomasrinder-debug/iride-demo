"""Einstellungen aus lokal.py lesen und einzelne Werte setzen (nur Standardbibliothek).

lokal.py ist eine Kopie von lokal.example.py und bleibt außerhalb von Git. Fehlt sie, wird sie beim ersten
Zugriff aus der Vorlage angelegt. einrichten.py und die Anleitungsseite (Google-Schlüssel) schreiben hier hinein.
"""

import re
import shutil
from pathlib import Path

HIER = Path(__file__).resolve().parent
LOKAL = HIER / "lokal.py"
VORLAGE = HIER / "lokal.example.py"
DATEN = HIER / "daten"          # Laufzeitdaten der Einrichtung (gitignored)


def anlegen() -> Path:
    if not LOKAL.exists():
        shutil.copy(VORLAGE, LOKAL)
    return LOKAL


def laden() -> dict:
    """Alle Großbuchstaben-Namen aus lokal.py als dict."""
    werte: dict = {}
    exec(compile(anlegen().read_text(encoding="utf-8"), str(LOKAL), "exec"), {}, werte)
    return {k: v for k, v in werte.items() if k.isupper()}


def setzen(name: str, wert) -> None:
    """Zeile NAME = … in lokal.py ersetzen (oder anhängen); Kommentare und Rest bleiben, wie sie sind."""
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
        raise ValueError(name)
    text = anlegen().read_text(encoding="utf-8")
    zeile = f"{name} = {wert!r}"
    muster = re.compile(rf"^{name}\s*=.*$", re.M)
    text = muster.sub(lambda _: zeile, text, count=1) if muster.search(text) else text.rstrip("\n") + "\n" + zeile + "\n"
    tmp = LOKAL.with_suffix(".neu")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(LOKAL)
