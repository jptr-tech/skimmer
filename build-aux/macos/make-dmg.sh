#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."

APP="dist/Skimmer.app"
DMG="dist/Skimmer.dmg"

if [ ! -d "$APP" ]; then
    echo "No $APP — run build-app.sh first"
    exit 1
fi

rm -f "$DMG"

create-dmg \
    --volname "Skimmer" \
    --window-pos 200 120 \
    --window-size 600 400 \
    --icon-size 100 \
    --icon "Skimmer.app" 175 190 \
    --hide-extension "Skimmer.app" \
    --app-drop-link 425 190 \
    "$DMG" \
    "$APP"

echo "---"
echo "DMG at $DMG"
