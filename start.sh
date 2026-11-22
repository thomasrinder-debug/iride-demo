#!/bin/sh
# iRide Demo starten (macOS, Linux): Server aus .venv, Seite in Chrome
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "Noch nicht eingerichtet - bitte zuerst einrichten.command bzw. einrichten.sh starten."
  exit 1
fi
exec .venv/bin/python iride_server.py --oeffnen "$@"
