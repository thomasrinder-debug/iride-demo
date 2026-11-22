"""Chrome (oder Edge) finden und eine Adresse darin öffnen – nur Standardbibliothek.

Web Bluetooth gibt es nur in Chromium-Browsern (Chrome, Edge). Firefox und Safari können es nicht, deshalb
öffnet iRide die Seite gezielt in Chrome und nicht im Standardbrowser.

    python browser.py                 # zeigt, welcher Browser gefunden wurde
    python browser.py http://localhost:8765/
"""

import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

CHROME_LADEN = "https://www.google.com/chrome/"


def _windows() -> list:
    kandidaten = []
    try:
        import winreg
        for wurzel in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for exe, name in (("chrome.exe", "Chrome"), ("msedge.exe", "Edge")):
                try:
                    with winreg.OpenKey(wurzel, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as k:
                        kandidaten.append((name, winreg.QueryValue(k, None)))
                except OSError:
                    pass
    except ImportError:
        pass
    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        basis = os.environ.get(var)
        if basis:
            kandidaten.append(("Chrome", str(Path(basis) / "Google" / "Chrome" / "Application" / "chrome.exe")))
            kandidaten.append(("Edge", str(Path(basis) / "Microsoft" / "Edge" / "Application" / "msedge.exe")))
    return kandidaten


def _mac() -> list:
    kandidaten = []
    for app, name in (("Google Chrome.app", "Chrome"), ("Microsoft Edge.app", "Edge")):
        kandidaten += [(name, str(Path(o) / app)) for o in ("/Applications", Path.home() / "Applications")]
    for bundle, name in (("com.google.Chrome", "Chrome"), ("com.microsoft.edgemac", "Edge")):
        try:
            aus = subprocess.run(["mdfind", f"kMDItemCFBundleIdentifier == '{bundle}'"],
                                 capture_output=True, text=True, timeout=5).stdout
            kandidaten += [(name, z) for z in aus.splitlines() if z.endswith(".app")]
        except (OSError, subprocess.SubprocessError):
            pass
    return kandidaten


def _linux() -> list:
    namen = [("google-chrome", "Chrome"), ("google-chrome-stable", "Chrome"), ("chromium", "Chromium"),
             ("chromium-browser", "Chromium"), ("microsoft-edge", "Edge")]
    return [(name, shutil.which(exe)) for exe, name in namen if shutil.which(exe)]


def finden():
    """(Name, Pfad) des ersten gefundenen Browsers mit Web Bluetooth (Chrome vor Edge), sonst None."""
    if sys.platform == "win32":
        kandidaten = _windows()
    elif sys.platform == "darwin":
        kandidaten = _mac()
    else:
        kandidaten = _linux()
    kandidaten.sort(key=lambda k: k[0] != "Chrome")      # Chrome zuerst, sonst Reihenfolge behalten
    for name, pfad in kandidaten:
        if pfad and Path(pfad).exists():
            return name, pfad
    return None


def oeffnen(url: str) -> str:
    """url in Chrome/Edge öffnen; gibt zurück, womit (sonst Standardbrowser mit Hinweis)."""
    gefunden = finden()
    try:
        if gefunden and sys.platform == "darwin":
            subprocess.Popen(["open", "-a", gefunden[1], url])
            return gefunden[0]
        if gefunden:
            subprocess.Popen([gefunden[1], url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return gefunden[0]
    except OSError:
        pass
    webbrowser.open(url)
    return "Standardbrowser (ohne Web Bluetooth, falls nicht Chrome/Edge)"


if __name__ == "__main__":
    if len(sys.argv) > 1:
        print("geöffnet mit", oeffnen(sys.argv[1]))
    else:
        print(finden() or f"kein Chrome/Edge gefunden – {CHROME_LADEN}")
