#!/bin/sh
# iRide Demo einrichten (macOS, Linux): sucht Python 3.12+ und startet einrichten.py
cd "$(dirname "$0")" || exit 1
for p in python3.14 python3.13 python3.12 python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
    exec "$p" einrichten.py "$@"
  fi
done
echo "Python 3.12 oder neuer fehlt (macOS bringt nur 3.9 mit)."
echo "Installieren: https://www.python.org/downloads/macos/  oder  brew install python@3.13"
echo "Danach einrichten erneut starten."
exit 1
