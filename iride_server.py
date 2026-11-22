"""iRide Demo – lokaler Server für die Kickr-Anzeige (web/index.html).

Liefert die Seite, die Kurse aus dem Ordner kurse/, den Streckenkatalog und die Anleitung. Standard ist nur
http://localhost:8765 auf diesem Rechner: localhost gilt in Chrome als sicherer Kontext, Web Bluetooth
geht damit ohne Zertifikat. Mit --lan kommt HTTPS (Port 8443) mit eigener kleiner CA für Tablets im WLAN dazu.

    python iride_server.py                    # http://localhost:8765/
    python iride_server.py --oeffnen          # und gleich in Chrome öffnen
    python iride_server.py --lan              # zusätzlich https://<LAN-IP>:8443/ fürs Tablet
    python iride_server.py --http-port 18765  # anderer Port
"""

import argparse
import datetime as dt
import gzip
import html
import ipaddress
import json
import re
import socket
import ssl
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import browser
import einstellungen

HIER = Path(__file__).resolve().parent
SEITE = HIER / "web" / "index.html"
VENDOR = HIER / "web" / "vendor"
ANLEITUNG = HIER / "web" / "anleitung"
STATISCH = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".png": "image/png",
            ".jpg": "image/jpeg", ".svg": "image/svg+xml", ".html": "text/html; charset=utf-8"}
EINST = einstellungen.laden()
KURSE = (HIER / EINST.get("KURSE", "kurse")).resolve()
KATALOG = (HIER / EINST.get("KATALOG", "katalog")).resolve()
LOGS = (HIER / EINST.get("LOGS", "logs")).resolve()
NUTZER = (HIER / EINST.get("NUTZER", "nutzer")).resolve()
SPUREN = NUTZER / "spuren"
ZERT = HIER / "_zertifikat"
MASSE = EINST.get("MASSE_SYSTEM", 80)
HOSTNAMEN = list(EINST.get("HOSTS", []) or [])
GOOGLE_MAPS_KEY = EINST.get("GOOGLE_MAPS_KEY", "") or ""
SCHLUESSEL_FORM = re.compile(r"AIza[0-9A-Za-z_\-]{35}")
KATALOG_DATEIEN = {"gpx": ".gpx", "kickr": ".gpx"}       # Unterordner -> erlaubte Endung
GEPACKT: dict = {}
GEPACKT_SPERRE = threading.Lock()
NUTZER_SPERRE = threading.RLock()
LOG_MAX = 2000
TEST = False          # --test: nichts zu Garmin schicken
LEISE = False         # --leise: keine Zugriffszeilen (einrichten.py, sonst stehen sie mitten im Menü)
HISTORIE_FELDER = {"id": str, "kurs": str, "name": str, "datum": str, "fahrzeit": (int, float),
                   "km": (int, float), "anteil": (int, float), "w": (int, float, type(None)),
                   "hf": (int, float, type(None)), "kad": (int, float, type(None)), "ziel": bool,
                   "anstieg": (int, float, type(None)), "anstieg_w": (int, float, type(None)),
                   "tf_hf": (int, float, type(None)), "tf_w": (int, float, type(None)),
                   "tf_n": (int, float, type(None))}


# ---------------------------------------------------------------- Zertifikate (nur --lan)

def lan_ip() -> str:
    """IP-Adresse der Schnittstelle, über die der Rechner ins Netz geht (es wird nichts gesendet)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def _schreibe(pfad: Path, key, cert) -> None:
    from cryptography.hazmat.primitives import serialization
    pfad.with_suffix(".key").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    pfad.with_suffix(".crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def ca_laden():
    """Eigene CA laden oder einmalig anlegen (10 Jahre gültig)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    ZERT.mkdir(exist_ok=True)
    pfad = ZERT / "ca"
    if pfad.with_suffix(".crt").exists():
        return (serialization.load_pem_private_key(pfad.with_suffix(".key").read_bytes(), None),
                x509.load_pem_x509_certificate(pfad.with_suffix(".crt").read_bytes()))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "iRide Demo lokale CA")])
    jetzt = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(jetzt - dt.timedelta(days=1)).not_valid_after(jetzt + dt.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                         content_commitment=False, key_encipherment=False, data_encipherment=False,
                                         key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    _schreibe(pfad, key, cert)
    print("Neue CA angelegt:", pfad.with_suffix(".crt"))
    return key, cert


def server_zertifikat(namen: list, neu: bool = False):
    """Serverzertifikat für alle Namen/IPs; neu, wenn ein Name fehlt oder es bald abläuft (iOS: max. 825 Tage)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    pfad = ZERT / "server"
    crt, key = pfad.with_suffix(".crt"), pfad.with_suffix(".key")
    if crt.exists() and not neu:
        cert = x509.load_pem_x509_certificate(crt.read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        vorhanden = {str(v) for v in san.get_values_for_type(x509.IPAddress)} | set(san.get_values_for_type(x509.DNSName))
        if set(namen) <= vorhanden and cert.not_valid_after_utc - dt.datetime.now(dt.timezone.utc) > dt.timedelta(days=30):
            return crt, key
    ca_key, ca_cert = ca_laden()
    skey = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    eintraege = []
    for n in namen:
        try:
            eintraege.append(x509.IPAddress(ipaddress.ip_address(n)))
        except ValueError:
            eintraege.append(x509.DNSName(n))
    jetzt = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, namen[0])]))
            .issuer_name(ca_cert.subject).public_key(skey.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(jetzt - dt.timedelta(days=1)).not_valid_after(jetzt + dt.timedelta(days=800))
            .add_extension(x509.SubjectAlternativeName(eintraege), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    _schreibe(pfad, skey, cert)
    print("Serverzertifikat ausgestellt für:", ", ".join(namen))
    return crt, key


# ---------------------------------------------------------------- Daten

def gpx_name(pfad: Path) -> str:
    """Erster <name> der GPX (Track-Name), sonst der Dateiname."""
    kopf = pfad.read_text(encoding="utf-8", errors="ignore")[:4000]
    for teil in kopf.split("<name>")[1:]:
        name = html.unescape(teil.split("</name>")[0]).strip()
        if name:
            return name
    return pfad.stem.replace("_", " ")


def nutzer_lesen(name: str) -> list:
    with NUTZER_SPERRE:     # unter Windows scheitert replace(), solange jemand die Datei liest
        try:
            return json.loads((NUTZER / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []


def nutzer_schreiben(name: str, daten: list) -> None:
    NUTZER.mkdir(parents=True, exist_ok=True)
    tmp = NUTZER / (name + ".neu")
    tmp.write_text(json.dumps(daten, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(NUTZER / name)


def historie_eintragen(fahrt: dict) -> None:
    """Fahrt einfügen oder (gleiche id: die Seite schickt minütlich den Zwischenstand) ersetzen."""
    if set(fahrt) - set(HISTORIE_FELDER) or not all(
            isinstance(fahrt.get(k), t) for k, t in HISTORIE_FELDER.items() if k in ("id", "kurs", "datum")):
        raise ValueError("Felder")
    if any(k in fahrt and not isinstance(fahrt[k], t) for k, t in HISTORIE_FELDER.items()):
        raise ValueError("Typen")
    with NUTZER_SPERRE:
        h = [f for f in nutzer_lesen("historie.json") if f.get("id") != fahrt["id"]] + [fahrt]
        nutzer_schreiben("historie.json", sorted(h, key=lambda f: f["datum"]))


def spur_datei(fid: str) -> Path:
    return SPUREN / ("".join(c for c in fid if c.isalnum() or c in "-_") + ".json")


def spur_speichern(s: dict) -> None:
    pkte = s.get("punkte")
    if not (isinstance(s.get("id"), str) and isinstance(s.get("kurs"), str) and isinstance(pkte, list)
            and all(isinstance(q, list) and len(q) == 2 and all(isinstance(v, (int, float)) for v in q) for q in pkte)):
        raise ValueError("Felder")
    SPUREN.mkdir(parents=True, exist_ok=True)
    tmp = spur_datei(s["id"]).with_suffix(".neu")
    tmp.write_text(json.dumps({"id": s["id"], "kurs": s["kurs"], "punkte": pkte}), encoding="utf-8")
    tmp.replace(spur_datei(s["id"]))


def geist(kurs: str, ohne: str = ""):
    """Spur der letzten Fahrt auf diesem Kurs für den Geisterfahrer."""
    for f in sorted(nutzer_lesen("historie.json"), key=lambda f: f["datum"], reverse=True):
        if f.get("kurs") == kurs and f.get("id") != ohne and spur_datei(f["id"]).exists():
            s = json.loads(spur_datei(f["id"]).read_text(encoding="utf-8"))
            return {**s, "datum": f["datum"], "anstieg": f.get("anstieg"), "fahrzeit": f.get("fahrzeit")}
    return None


_STRECKEN = None


def strecke(kid: str):
    """Texte einer Katalogstrecke (Landschaft, bekannt aus) aus katalog.html."""
    global _STRECKEN
    if _STRECKEN is None:
        try:
            seite = (KATALOG / "katalog.html").read_text(encoding="utf-8")
            daten, _ = json.JSONDecoder().raw_decode(seite.split("const DATEN = ", 1)[1])
            _STRECKEN = {d["id"]: {"name": d["name"], "land": d.get("land"), "region": d.get("region"),
                                   "text": d.get("text") or {}} for d in daten}
        except (OSError, IndexError, ValueError):
            _STRECKEN = {}
    return _STRECKEN.get(kid)


def favorit_setzen(schluessel: str, an: bool) -> list:
    with NUTZER_SPERRE:
        fav = [f for f in nutzer_lesen("favoriten.json") if f != schluessel] + ([schluessel] if an else [])
        nutzer_schreiben("favoriten.json", fav)
    return fav


def garmin_status() -> dict:
    """Garmin-Anmeldung vorhanden? Name kommt aus daten/einrichtung.json (einrichten.py), nicht von Garmin."""
    try:
        import garmin_kurs
        an = garmin_kurs.angemeldet()
    except Exception:
        an = False
    info = einrichtung_lesen().get("garmin") or {}
    return {"angemeldet": an, "name": info.get("name") if an else None}


def einrichtung_lesen() -> dict:
    try:
        return json.loads((einstellungen.DATEN / "einrichtung.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def einrichtung_status() -> dict:
    """Live-Status für die Anleitungsseite."""
    b = browser.finden()
    return {
        "python": sys.version.split()[0],
        "browser": {"name": b[0], "pfad": b[1]} if b else None,
        "google": {"gesetzt": bool(GOOGLE_MAPS_KEY),
                   "ende": GOOGLE_MAPS_KEY[-4:] if GOOGLE_MAPS_KEY else None},
        "garmin": garmin_status(),
        "kurse": sorted(p.name for p in KURSE.glob("*.gpx") if not p.stem.endswith("_edge")),
        "katalog": (KATALOG / "katalog.html").is_file(),
    }


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    def _senden(self, code: int, daten: bytes, typ: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(daten)))
        if "Cache-Control" not in (extra or {}):
            self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(daten)

    def _json(self, obj, code: int = 200) -> None:
        self._senden(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _gross_senden(self, datei: Path, typ: str) -> None:
        """Größere Seite gzip-gepackt mit ETag."""
        st = datei.stat()
        etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
        kopf = {"ETag": etag, "Cache-Control": "no-cache", "Vary": "Accept-Encoding"}
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            for k, v in kopf.items():
                self.send_header(k, v)
            self.end_headers()
            return
        if "gzip" not in self.headers.get("Accept-Encoding", ""):
            self._senden(200, datei.read_bytes(), typ, kopf)
            return
        with GEPACKT_SPERRE:
            if not (GEPACKT.get(datei) and GEPACKT[datei][0] == etag):
                GEPACKT[datei] = (etag, gzip.compress(datei.read_bytes(), 6))
            daten = GEPACKT[datei][1]
        self._senden(200, daten, typ, {**kopf, "Content-Encoding": "gzip"})

    def _statisch(self, ordner: Path, rel: str) -> None:
        datei = (ordner / (rel or "index.html")).resolve()
        if datei.is_file() and ordner in datei.parents and datei.suffix.lower() in STATISCH:
            self._senden(200, datei.read_bytes(), STATISCH[datei.suffix.lower()])
        else:
            self._senden(404, b"nicht gefunden", "text/plain")

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        pfad, q = unquote(url.path), parse_qs(url.query)
        if pfad in ("/", "/index.html"):
            self._senden(200, SEITE.read_bytes(), "text/html; charset=utf-8")
        elif pfad.startswith("/vendor/"):
            self._statisch(VENDOR, pfad[len("/vendor/"):])
        elif pfad == "/anleitung":
            self._senden(301, b"", "text/plain", {"Location": "/anleitung/"})
        elif pfad.startswith("/anleitung/"):
            self._statisch(ANLEITUNG, pfad[len("/anleitung/"):])
        elif pfad == "/api/einrichtung":
            self._json(einrichtung_status())
        elif pfad == "/api/config":
            self._json({"googleMapsKey": GOOGLE_MAPS_KEY, "masse": MASSE, "coaching": None, "test": TEST})
        elif pfad == "/api/nutzer":
            self._json({"favoriten": nutzer_lesen("favoriten.json"), "historie": nutzer_lesen("historie.json")})
        elif pfad == "/api/geist":
            self._json(geist(q.get("kurs", [""])[0], q.get("ohne", [""])[0]))
        elif pfad == "/api/strecke":
            self._json(strecke(q.get("id", [""])[0]))
        elif pfad in ("/log", "/fehler"):
            self._protokoll(pfad[1:], unquote(url.query))
            self._senden(204, b"", "text/plain")
        elif pfad == "/api/kurse":
            kurse = [{"datei": p.name, "name": gpx_name(p)} for p in KURSE.glob("*.gpx") if not p.stem.endswith("_edge")]
            self._json(sorted(kurse, key=lambda k: k["name"].lower()))
        elif pfad.startswith("/kurse/"):
            name = pfad[len("/kurse/"):]
            datei = (KURSE / name).resolve()
            if ("/" in name or "\\" in name or "\0" in name or not name.lower().endswith(".gpx")
                    or datei.parent != KURSE or not datei.is_file()):
                self._senden(404, b"nicht gefunden", "text/plain")
            else:
                self._senden(200, datei.read_bytes(), "application/gpx+xml")
        elif pfad == "/katalog":
            self._senden(301, b"", "text/plain", {"Location": "/katalog/"})
        elif pfad in ("/katalog/", "/katalog/katalog.html"):
            seite = KATALOG / "katalog.html"
            if seite.is_file():
                self._gross_senden(seite, "text/html; charset=utf-8")
            else:
                self._senden(404, "kein Katalog".encode(), "text/plain; charset=utf-8")
        elif pfad.startswith("/katalog/"):
            datei = self._katalog_datei(pfad[len("/katalog/"):])
            if datei:
                self._senden(200, datei.read_bytes(), "application/gpx+xml")
            else:
                self._senden(404, b"nicht gefunden", "text/plain")
        elif pfad == "/ca.crt" and (ZERT / "ca.crt").exists():
            from cryptography import x509
            from cryptography.hazmat.primitives import serialization
            cert = x509.load_pem_x509_certificate((ZERT / "ca.crt").read_bytes())
            self._senden(200, cert.public_bytes(serialization.Encoding.DER), "application/x-x509-ca-cert",
                         {"Content-Disposition": 'attachment; filename="iride-demo-ca.crt"'})
        else:
            self._senden(404, b"nicht gefunden", "text/plain")

    @staticmethod
    def _protokoll(art: str, text: str) -> None:
        """Zeile an logs/iride_<datum>.log anhängen."""
        try:
            LOGS.mkdir(exist_ok=True)
            jetzt = dt.datetime.now()
            with open(LOGS / f"iride_{jetzt:%Y-%m-%d}.log", "a", encoding="utf-8") as f:
                sauber = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")[:LOG_MAX]
                f.write("\t".join((f"{jetzt:%H:%M:%S}", art, sauber)) + "\n")
        except OSError as e:
            print("Log nicht schreibbar:", e)

    @staticmethod
    def _katalog_datei(rel: str):
        """Nur <ordner>/<name><endung> aus KATALOG_DATEIEN, ohne Pfadtricks."""
        teile = rel.split("/")
        if len(teile) != 2 or teile[0] not in KATALOG_DATEIEN or "\\" in rel or "\0" in rel:
            return None
        datei = (KATALOG / teile[0] / teile[1]).resolve()
        if (datei.suffix.lower() != KATALOG_DATEIEN[teile[0]] or not datei.is_file()
                or datei.parent != (KATALOG / teile[0]).resolve()):
            return None
        return datei

    def _fremde_herkunft(self) -> bool:
        """POST aus einer fremden Webseite (CSRF): Origin vorhanden und ungleich dem eigenen Host."""
        herkunft = self.headers.get("Origin")
        return bool(herkunft) and urlparse(herkunft).netloc != self.headers.get("Host", "")

    def _von_hier(self) -> bool:
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def _laenge(self, grenze: int) -> int:
        laenge = int(self.headers.get("Content-Length") or 0)
        if not 0 < laenge <= grenze:
            raise ValueError("Länge")
        return laenge

    def do_POST(self):  # noqa: N802
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if self._fremde_herkunft():
            self._senden(403, b"fremde Herkunft", "text/plain")
        elif url.path == "/api/einrichtung/google":
            self._google_setzen()
        elif url.path == "/api/uebernehmen":
            self._uebernehmen(q.get("id", [""])[0])
        elif url.path == "/api/favorit":
            schluessel = q.get("id", [""])[0]
            if not schluessel or len(schluessel) > 200:
                self._senden(400, b"id fehlt", "text/plain")
            else:
                self._json(favorit_setzen(schluessel, q.get("an", ["1"])[0] == "1"))
        elif url.path in ("/api/historie", "/api/spur"):
            try:
                daten = json.loads(self.rfile.read(self._laenge(4096 if url.path == "/api/historie" else 65536)))
                (historie_eintragen if url.path == "/api/historie" else spur_speichern)(daten)
            except (ValueError, TypeError, AttributeError) as e:
                self._senden(400, f"ungültig: {e}".encode(), "text/plain; charset=utf-8")
                return
            self._senden(204, b"", "text/plain")
        else:
            self._senden(404, b"nicht gefunden", "text/plain")

    def _google_setzen(self) -> None:
        """Schlüssel von der Anleitungsseite in lokal.py schreiben; nur vom eigenen Rechner aus."""
        global GOOGLE_MAPS_KEY
        if not self._von_hier():
            self._senden(403, "nur auf dem Rechner selbst (localhost)".encode(), "text/plain; charset=utf-8")
            return
        try:
            schluessel = json.loads(self.rfile.read(self._laenge(1024))).get("schluessel", "").strip()
        except (ValueError, AttributeError):
            schluessel = None
        if schluessel is None or (schluessel and not SCHLUESSEL_FORM.fullmatch(schluessel)):
            self._json({"ok": False, "fehler": "Das sieht nicht wie ein Google-Schlüssel aus (AIza… mit 39 Zeichen)."}, 400)
            return
        einstellungen.setzen("GOOGLE_MAPS_KEY", schluessel)
        GOOGLE_MAPS_KEY = schluessel
        self._json({"ok": True, "gesetzt": bool(schluessel)})

    def _uebernehmen(self, kid: str) -> None:
        """Katalogstrecke in den Kursordner übernehmen und (wenn angemeldet) an Garmin schicken."""
        quelle = self._katalog_datei(f"kickr/{kid}.gpx") if kid.replace("-", "").isalnum() else None
        if not quelle:
            self._senden(404, b"unbekannte Strecke", "text/plain")
            return
        name = f"Katalog_{kid}.gpx"
        KURSE.mkdir(parents=True, exist_ok=True)
        if not (KURSE / name).exists():       # Demo-Kurse tragen ihre Stufe im Namen: nicht überschreiben
            (KURSE / name).write_bytes(quelle.read_bytes())
        antwort = {"kurs": name}
        edge = self._katalog_datei(f"kickr/{kid}_edge.gpx")
        if edge:
            antwort["garmin"] = self._an_garmin(edge)
        print(f"Katalog -> Kurse: {name}  Garmin: {antwort.get('garmin', '-')}")
        self._json(antwort)

    def _an_garmin(self, gpx: Path) -> str:
        """Kurs über Garmin Connect an den Edge schicken; Ergebnis als kurzer Text (nie eine Ausnahme)."""
        if TEST:
            return f"Testmodus: {gpx.name} NICHT an Garmin geschickt"
        try:
            import garmin_kurs
            if not garmin_kurs.angemeldet():
                return "Garmin nicht eingerichtet (siehe Anleitung)"
            r = garmin_kurs.senden(gpx)
            text = f"an den Edge geschickt (Kurs {r['courseId']})"
        except Exception as e:
            text = f"Fehler: {str(e)[:160]}"
        self._protokoll("log", f"garmin-kurs {gpx.name} {text}")
        return text

    def log_message(self, fmt, *args):
        if LEISE:
            return
        pfad = getattr(self, "path", None)
        if pfad is None:
            print(f"unlesbare Anfrage auf Port {self.server.server_address[1]} – https:// nur auf dem HTTPS-Port")
        elif ("/api/" in pfad and not pfad.startswith("/api/einrichtung")) or pfad.startswith("/fehler"):
            print(f"{self.address_string()}  {unquote(fmt % args)}")


def laeuft_schon(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--http-port", type=int, default=8765)
    p.add_argument("--https-port", type=int, default=8443)
    p.add_argument("--lan", action="store_true", help="auch im WLAN erreichbar, mit HTTPS für Tablets")
    p.add_argument("--oeffnen", nargs="?", const="/", metavar="PFAD", help="Seite in Chrome öffnen (z. B. /anleitung/)")
    p.add_argument("--neues-zertifikat", action="store_true", help="Serverzertifikat neu ausstellen (mit --lan)")
    p.add_argument("--test", action="store_true", help="nichts zu Garmin schicken")
    p.add_argument("--leise", action="store_true", help="keine Zugriffszeilen im Terminal")
    a = p.parse_args()
    global TEST, LEISE
    TEST, LEISE = a.test, a.leise
    sys.stdout.reconfigure(errors="replace")     # Windows-Konsole (cp1252) kennt nicht jedes Zeichen

    if laeuft_schon(a.http_port):
        sys.exit(f"Port {a.http_port} ist belegt – läuft iRide schon? Sonst: --http-port 18765")
    adresse = "0.0.0.0" if a.lan else "127.0.0.1"
    http = ThreadingHTTPServer((adresse, a.http_port), Handler)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    basis = f"http://localhost:{a.http_port}"
    print()
    print(f"iRide:          {basis}/")
    print(f"Anleitung:      {basis}/anleitung/")
    if a.lan:
        ip = lan_ip()
        namen = list(dict.fromkeys(HOSTNAMEN + [ip, "localhost", "127.0.0.1", socket.gethostname()]))
        ca_laden()
        crt, key = server_zertifikat(namen, a.neues_zertifikat)
        https = ThreadingHTTPServer((adresse, a.https_port), Handler)
        kontext = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        kontext.load_cert_chain(crt, key)
        https.socket = kontext.wrap_socket(https.socket, server_side=True)
        threading.Thread(target=https.serve_forever, daemon=True).start()
        print(f"Tablet (WLAN):  https://{ip}:{a.https_port}/")
        print(f"CA fürs Tablet: http://{ip}:{a.http_port}/ca.crt")
    print("Street View:   ", "Schlüssel eingetragen" if GOOGLE_MAPS_KEY else "kein Schlüssel (Anleitung, Schritt 2)")
    print(f"Kurse:          {len(list(KURSE.glob('*.gpx')))} GPX")
    if TEST:
        print("TESTMODUS:      nichts zu Garmin")
    if a.oeffnen:
        print("Geöffnet mit:  ", browser.oeffnen(basis + a.oeffnen))
    print("Beenden mit Strg+C (Fenster offen lassen, solange du fährst)")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
