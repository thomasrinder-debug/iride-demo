#!/bin/sh
# Doppelklick im Finder (macOS): öffnet ein Terminal und richtet iRide ein
cd "$(dirname "$0")" || exit 1
sh ./einrichten.sh "$@"
