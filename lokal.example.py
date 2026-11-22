# Vorlage für lokal.py. einrichten.py legt lokal.py als Kopie an; lokal.py kommt nie ins Git.
# Alle Pfade sind relativ zum Repo-Ordner (oder absolut).

# Google-Maps-Schlüssel (Maps JavaScript API) für Street View; leer = ohne Street View.
# Am einfachsten über die Anleitungsseite eintragen (http://localhost:8765/anleitung/).
GOOGLE_MAPS_KEY = ""

# Systemmasse Fahrer + Rad in kg (Fahrmodell im Testmodus, Virtual Partner am Edge)
MASSE_SYSTEM = 80

# Leistung in W, mit der der Virtual Partner auf dem Edge die an Garmin geschickten Kurse fährt
PARTNER_WATT = 160

# Zusätzliche Hostnamen fürs HTTPS-Zertifikat (nur mit --lan), z. B. ["meinpc.fritz.box"]
HOSTS = []

# Ordner
KURSE = "kurse"                  # Kurs-GPX, die iRide in der Liste zeigt
KATALOG = "katalog"              # Streckenkatalog (katalog.html, gpx/, kickr/)
LOGS = "logs"                    # Tagesprotokolle der Seite
NUTZER = "nutzer"                # Favoriten, Historie, Spuren für den Geisterfahrer
GARMIN_TOKENS = "daten/garmin"   # Garmin-Anmeldung (nur Tokens, nie das Passwort)
