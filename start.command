#!/bin/sh
# Doppelklick im Finder (macOS): startet iRide und öffnet die Seite in Chrome
cd "$(dirname "$0")" || exit 1
sh ./start.sh "$@"
