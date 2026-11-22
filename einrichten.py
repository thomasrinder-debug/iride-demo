"""iRide Demo einrichten: venv, Pakete, Browser-Check, Server starten, Anleitung öffnen, Google und Garmin.

Normalerweise über einrichten.cmd (Windows) bzw. einrichten.command / einrichten.sh (macOS, Linux) gestartet.
Erneut aufrufen schadet nicht: was schon da ist, bleibt.

    python einrichten.py                  # alles, mit Fragen im Terminal
    python einrichten.py --garmin         # nur Garmin anmelden
    python einrichten.py --port 18765     # anderer Port
    python einrichten.py --ohne-fragen --kein-browser   # für Tests: nur einrichten und Server starten
"""

import sys

MIN = (3, 12)          # garminconnect braucht Python 3.12
if sys.version_info < MIN:
    print("Python %d.%d gefunden, iRide braucht %d.%d oder neuer." % (sys.version_info[:2] + MIN))
    if sys.platform == "win32":
        print("Installieren: https://www.python.org/downloads/  (Haken bei 'Add python.exe to PATH')")
        print("oder in PowerShell:  winget install Python.Python.3.13")
    elif sys.platform == "darwin":
        print("Installieren: https://www.python.org/downloads/macos/  oder mit Homebrew:  brew install python@3.13")
    else:
        print("Installieren über die Paketverwaltung, z. B.  sudo apt install python3.12 python3.12-venv")
    sys.exit(1)

import argparse
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

HIER = Path(__file__).resolve().parent
sys.path.insert(0, str(HIER))
import browser          # noqa: E402
import einstellungen    # noqa: E402

VENV = HIER / ".venv"
VPY = VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
GRUEN, GELB, ROT, AUS = ("\033[32m", "\033[33m", "\033[31m", "\033[0m") if sys.stdout.isatty() else ("",) * 4


def schritt(text):
    print(f"\n== {text}")


def ok(text):
    print(f"   {GRUEN}OK{AUS}  {text}")


def warnung(text):
    print(f"   {GELB}!!{AUS}  {text}")


def venv_anlegen():
    schritt("Python-Umgebung (.venv im Repo-Ordner)")
    ok(f"Python {sys.version.split()[0]}")
    if not VPY.exists():
        print("   lege .venv an …")
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    print("   installiere Pakete aus requirements.txt (beim ersten Mal ~1 min) …")
    r = subprocess.run([str(VPY), "-m", "pip", "install", "--disable-pip-version-check", "-q",
                        "-r", str(HIER / "requirements.txt")])
    if r.returncode:
        sys.exit(f"   {ROT}pip ist fehlgeschlagen{AUS} – Internetverbindung prüfen und einrichten erneut starten.")
    ok("Pakete installiert (nur in .venv, nichts global)")


def browser_pruefen():
    schritt("Browser mit Web Bluetooth")
    b = browser.finden()
    if b:
        ok(f"{b[0]} gefunden: {b[1]}")
        if sys.platform == "darwin":
            print("   macOS: beim ersten Verbinden fragt macOS nach Bluetooth für Chrome – erlauben")
            print("   (sonst: Systemeinstellungen → Datenschutz & Sicherheit → Bluetooth → Chrome an).")
    else:
        warnung("Weder Chrome noch Edge gefunden. Firefox und Safari können kein Web Bluetooth.")
        warnung(f"Den Testmodus kannst du trotzdem nutzen. Chrome: {browser.CHROME_LADEN}")
    return b


def server_url(port):
    return f"http://localhost:{port}"


def server_laeuft(port):
    try:
        with urllib.request.urlopen(server_url(port) + "/api/einrichtung", timeout=1) as r:
            return json.load(r)
    except (OSError, ValueError):
        return None


def server_starten(port):
    schritt("iRide-Server starten")
    if server_laeuft(port):
        ok(f"läuft schon auf {server_url(port)}")
        return None
    proc = subprocess.Popen([str(VPY), str(HIER / "iride_server.py"), "--http-port", str(port), "--leise"], cwd=str(HIER))
    for _ in range(40):
        time.sleep(0.25)
        if server_laeuft(port):
            ok(f"läuft auf {server_url(port)}")
            return proc
        if proc.poll() is not None:
            break
    sys.exit(f"   {ROT}Server startet nicht{AUS} (Port {port} belegt?). Erneut mit:  einrichten --port 18765")


def google_im_terminal(port):
    schluessel = input("   Google-Schlüssel einfügen (leer = abbrechen): ").strip()
    if not schluessel:
        return
    req = urllib.request.Request(server_url(port) + "/api/einrichtung/google", method="POST",
                                 data=json.dumps({"schluessel": schluessel}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            json.load(r)
        ok("Schlüssel in lokal.py eingetragen – auf der Anleitungsseite „Testen“ drücken")
    except urllib.error.HTTPError as e:
        warnung(json.loads(e.read() or b"{}").get("fehler", f"Fehler {e.code}"))
    except OSError as e:
        warnung(f"Server nicht erreichbar: {e}")


def garmin_anmelden():
    schritt("Garmin Connect anmelden")
    print("   Passwort und MFA-Code gibst du nur hier im Terminal ein. Gespeichert werden nur Tokens in daten/garmin/.")
    print("   Hinweis: Die Bibliothek garminconnect ist inoffiziell und kann bei Änderungen von Garmin ausfallen.")
    r = subprocess.run([str(VPY), str(HIER / "garmin_kurs.py"), "anmelden"], cwd=str(HIER))
    if r.returncode == 0:
        ok("Garmin verbunden – die Anleitungsseite zeigt es gleich an")
    else:
        warnung("Anmeldung hat nicht geklappt. Später erneut:  einrichten --garmin")


def garmin_testen():
    r = subprocess.run([str(VPY), str(HIER / "garmin_kurs.py"), "pruefen"], cwd=str(HIER))
    if r.returncode:
        warnung("Test fehlgeschlagen – neu anmelden mit [g]")


def fragen(port):
    schritt("Google und Garmin (beides freiwillig, Erklärungen stehen auf der Anleitungsseite)")
    while True:
        print("\n   [s] Google-Schlüssel hier eingeben (geht auch auf der Anleitungsseite)")
        print("   [g] Garmin anmelden      [t] Garmin-Anmeldung testen")
        print("   [Enter] fertig")
        try:
            wahl = input("   Auswahl: ").strip().lower()
        except EOFError:
            return
        if wahl == "s":
            google_im_terminal(port)
        elif wahl == "g":
            garmin_anmelden()
        elif wahl == "t":
            garmin_testen()
        elif wahl == "":
            return


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--garmin", action="store_true", help="nur Garmin anmelden")
    p.add_argument("--ohne-fragen", action="store_true", help="keine Fragen im Terminal")
    p.add_argument("--kein-browser", action="store_true", help="Anleitung nicht öffnen")
    p.add_argument("--beenden", action="store_true", help="Server danach nicht weiterlaufen lassen (Tests)")
    a = p.parse_args()
    os.chdir(HIER)
    print("iRide Demo – Einrichtung")
    if a.garmin:
        if not VPY.exists():
            venv_anlegen()
        garmin_anmelden()
        return
    venv_anlegen()
    schritt("Einstellungen")
    neu = not einstellungen.LOKAL.exists()
    einstellungen.anlegen()
    ok("lokal.py " + ("aus der Vorlage angelegt" if neu else "vorhanden, bleibt unverändert"))
    browser_pruefen()
    proc = server_starten(a.port)
    anleitung = server_url(a.port) + "/anleitung/"
    if not a.kein_browser:
        schritt("Anleitung öffnen")
        ok(f"{anleitung}  (geöffnet mit {browser.oeffnen(anleitung)})")
    else:
        print(f"\n   Anleitung: {anleitung}")
    if not a.ohne_fragen:
        fragen(a.port)
    if a.beenden or proc is None:
        if proc:
            proc.terminate()
        print("\nFertig.")
        return
    print(f"\nFertig. Der Server läuft weiter: {server_url(a.port)}/")
    print("Dieses Fenster offen lassen, solange du fährst. Beenden mit Strg+C.")
    print("Nächstes Mal reicht start.cmd (Windows) bzw. start.command (Mac).")
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()


if __name__ == "__main__":
    main()
