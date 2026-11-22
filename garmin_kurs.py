"""Kurs-GPX als privaten Kurs zu Garmin Connect hochladen und an den Edge schicken.

Der Edge holt ihn beim nächsten Sync über die Connect-App aufs Gerät (kein USB nötig). Genutzt von
iride_server.py beim Übernehmen einer Katalogstrecke; lässt sich auch direkt aufrufen:

    python garmin_kurs.py anmelden            # einmalig, fragt E-Mail, Passwort (und ggf. MFA-Code)
    python garmin_kurs.py pruefen             # Anmeldung testen (zeigt den Kontonamen)
    python garmin_kurs.py senden <datei.gpx>  # hochladen + an den Edge schicken

Inoffizielle Garmin-Connect-Schnittstellen (dieselben wie die Connect-Website), geprüft am 15.10.2026:
- POST /course-service/course/import parst die GPX, legt aber nichts an und lässt die Höhen weg.
  Die Höhen kommen deshalb aus der GPX selbst; sonst ginge das vorgezogene Profil der _edge.gpx verloren.
- POST /course-service/course speichert; startPoint und boundingBox sind Pflicht.
- POST /device-service/devicemessage/messages stellt den Kurs für das Gerät in die Warteschlange.

Gespeichert wird nur die Anmeldung (Tokens) im Ordner GARMIN_TOKENS aus lokal.py (Standard daten/garmin), nie das Passwort.
Eine Zuordnung Datei -> courseId (mit SHA-256 der GPX) verhindert doppelte Kurse, wenn dieselbe Strecke
erneut kommt; ist die GPX inzwischen neu berechnet, wird sie neu importiert (der alte Kurs bleibt in
Connect stehen und kann dort gelöscht werden).
"""

import getpass
import hashlib
import json
import math
import re
import sys
import threading
import time
from pathlib import Path

import einstellungen

EINST = einstellungen.laden()
HIER = Path(__file__).resolve().parent
TOKENS = (HIER / EINST.get("GARMIN_TOKENS", "daten/garmin")).resolve()
ZUORDNUNG = TOKENS / "kurse.json"
RENNRAD = 10            # activityTypePk „road_biking“, wie die vorhandenen Kurse
PRIVAT = 2              # rulePK
SPERRE = threading.Lock()   # kurse.json: der Server kann zwei Übernahmen gleichzeitig bearbeiten


def angemeldet() -> bool:
    return any(TOKENS.glob("*.json")) and (TOKENS / "garmin_tokens.json").exists()


def api():
    import garminconnect          # erst hier: Status und Server brauchen die Bibliothek nicht
    g = garminconnect.Garmin()
    g.login(tokenstore=str(TOKENS))
    return g


def anmelden() -> None:
    TOKENS.mkdir(parents=True, exist_ok=True)
    email = input("Garmin-E-Mail: ").strip()
    passwort = getpass.getpass("Garmin-Passwort (wird nicht gespeichert): ")
    import garminconnect
    g = garminconnect.Garmin(email, passwort, prompt_mfa=lambda: input("MFA-Code: ").strip())
    g.login(tokenstore=str(TOKENS))
    name = g.get_full_name()
    print("Angemeldet als", name, "– nur die Tokens liegen in", TOKENS)
    _einrichtung_merken(name)


def _einrichtung_merken(name) -> None:
    """Anzeigename für die Anleitungsseite merken (daten/einrichtung.json, gitignored)."""
    datei = einstellungen.DATEN / "einrichtung.json"
    try:
        info = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        info = {}
    info["garmin"] = {"name": name}
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")


def pruefen() -> str:
    """Mit den gespeicherten Tokens anmelden und den Namen lesen (Test der Einrichtung)."""
    name = api().get_full_name()
    _einrichtung_merken(name)
    return name


def _daten(antwort):
    """client.post liefert je nach Bibliotheksversion schon ein dict oder noch eine Response."""
    return antwort.json() if hasattr(antwort, "json") else antwort


def _edge_id(g) -> int:
    for d in g.get_devices():
        if "Edge" in str(d.get("productDisplayName", "")) and d.get("deviceStatus") == "active":
            return d["deviceId"]
    raise RuntimeError("kein aktiver Edge im Garmin-Konto")


def senden(gpx: Path) -> dict:
    """GPX hochladen (oder vorhandenen Kurs wiederverwenden) und an den Edge schicken."""
    with SPERRE:
        return _senden(gpx)


def _zuordnung_schreiben(zuordnung: dict) -> None:
    tmp = ZUORDNUNG.with_suffix(".neu")
    tmp.write_text(json.dumps(zuordnung, indent=1), encoding="utf-8")
    tmp.replace(ZUORDNUNG)


def _v(watt: float, steigung: float, masse: float) -> float:
    """Geschwindigkeit in m/s bei konstanter Leistung (Fahrmodell wie kickr_bergkurs.py: C_rr 0,004, Wind 0,23 kg/m)."""
    th = math.atan(steigung / 100)
    lo, hi = 0.5, 25.0
    for _ in range(40):
        v = (lo + hi) / 2
        p = v * (masse * 9.81 * (math.sin(th) + 0.004 * math.cos(th)) + 0.23 * v * v)
        lo, hi = (v, hi) if p < watt else (lo, v)
    return min(v, 16.0)   # bergab nicht schneller als ~58 km/h


def _partner_zeiten(punkte: list) -> float:
    """Zeitstempel je Kurspunkt für den Virtual Partner: konstante Leistung PARTNER_WATT statt Garmins pauschaler
    25 km/h (15.11.2026: der Partner war am Kickr am Berg doppelt so schnell). Steigung über ~100 m geglättet.
    Gibt die Gesamtdauer in s zurück."""
    watt, masse = EINST.get("PARTNER_WATT", 160), EINST.get("MASSE_SYSTEM", 80)
    d = [p["distance"] for p in punkte]
    h = [p["elevation"] for p in punkte]
    t0, t = punkte[0].get("timestamp") or int(time.time() * 1000), 0.0
    punkte[0]["timestamp"] = t0
    j = 0
    for i in range(1, len(punkte)):
        while d[i] - d[j] > 100 and j < i - 1:   # Fenster ~100 m zurück
            j += 1
        k = max(0, j - 1)
        g = 100 * (h[i] - h[k]) / max(d[i] - d[k], 1.0)
        t += (d[i] - d[i - 1]) / _v(watt, max(-15.0, min(15.0, g)), masse)
        punkte[i]["timestamp"] = int(t0 + 1000 * t)
    return max(t, 1.0)


def _senden(gpx: Path) -> dict:
    text = gpx.read_text(encoding="utf-8")
    # Partner-Leistung gehört zum Kurs: ändert sie sich (oder die Zeitlogik, „vp1“), wird neu importiert
    sha = hashlib.sha256(gpx.read_bytes() + f"vp1:{EINST.get('PARTNER_WATT', 160)}".encode()).hexdigest()
    name = (re.search(r"<name>([^<]+)</name>", text) or [None, gpx.stem])[1].strip()
    hoehen = [float(h) for h in re.findall(r"<ele>([-\d.]+)</ele>", text)]
    zuordnung = json.loads(ZUORDNUNG.read_text(encoding="utf-8")) if ZUORDNUNG.exists() else {}
    g = api()
    eintrag = zuordnung.get(gpx.name)
    # Altes Format {datei: courseId} ohne Hash: Stand unbekannt, also einmal neu importieren
    cid, alt_sha = (eintrag.get("id"), eintrag.get("sha")) if isinstance(eintrag, dict) else (eintrag, None)
    if cid and alt_sha != sha:
        cid = None                                      # GPX neu berechnet: neuen Kurs anlegen
    if cid:
        try:
            g.connectapi(f"/course-service/course/{cid}")
        except Exception:
            cid = None                                  # in Connect gelöscht: neu anlegen
    if not cid:
        with gpx.open("rb") as f:
            k = _daten(g.client.post("connectapi", "/course-service/course/import",
                                     files={"file": (gpx.name, f)}, api=True))
        punkte = k["geoPoints"]
        if len(punkte) != len(hoehen):
            raise RuntimeError(f"Punktzahl weicht ab ({len(punkte)} statt {len(hoehen)})")
        for p, h in zip(punkte, hoehen):
            p["elevation"] = h
        dauer = _partner_zeiten(punkte)
        la, lo = [p["latitude"] for p in punkte], [p["longitude"] for p in punkte]
        laenge = punkte[-1]["distance"]
        k.update(courseName=name, activityTypePk=RENNRAD, rulePK=PRIVAT, sourceTypeId=3, elevationSource=1,
                 coordinateSystem="WGS84", originalCoordinateSystem="WGS84", targetCoordinateSystem="WGS84",
                 distanceMeter=laenge, elapsedSeconds=round(dauer), speedMeterPerSecond=laenge / dauer,
                 elevationGainMeter=sum(max(0, b - a) for a, b in zip(hoehen, hoehen[1:])),
                 elevationLossMeter=sum(max(0, a - b) for a, b in zip(hoehen, hoehen[1:])),
                 startPoint=dict(punkte[0]),
                 boundingBox={"center": None,
                              "lowerLeft": {"latitude": min(la), "longitude": min(lo)},
                              "upperRight": {"latitude": max(la), "longitude": max(lo)},
                              "lowerLeftLatIsSet": True, "lowerLeftLongIsSet": True,
                              "upperRightLatIsSet": True, "upperRightLongIsSet": True})
        for linie in k.get("courseLines") or []:
            linie.update(distanceInMeters=laenge, coordinateSystem="WGS84", originalCoordinateSystem="WGS84")
        cid = _daten(g.client.post("connectapi", "/course-service/course", json=k, api=True))["courseId"]
    if zuordnung.get(gpx.name) != {"id": cid, "sha": sha}:
        zuordnung[gpx.name] = {"id": cid, "sha": sha}
        _zuordnung_schreiben(zuordnung)
    geraet = _edge_id(g)
    g.client.post("connectapi", "/device-service/devicemessage/messages", api=True, json=[{
        "deviceId": geraet, "messageUrl": f"course-service/course/fit/{cid}/{geraet}?elevation=true",
        "messageType": "courses", "messageName": name, "groupName": None, "priority": 1,
        "fileType": "FIT", "metaDataId": cid}])
    return {"courseId": cid, "name": name}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) >= 2 and sys.argv[1] == "anmelden":
        anmelden()
    elif len(sys.argv) == 2 and sys.argv[1] == "pruefen":
        if not angemeldet():
            sys.exit("Garmin ist noch nicht angemeldet – im Einrichtungsfenster [g] wählen.")
        try:
            print("Angemeldet als", pruefen())
        except Exception as e:
            sys.exit(f"Anmeldung abgelaufen oder abgelehnt ({str(e)[:120]}) – neu anmelden mit [g].")
    elif len(sys.argv) == 3 and sys.argv[1] == "senden":
        print(senden(Path(sys.argv[2])))
    else:
        sys.exit(__doc__)
