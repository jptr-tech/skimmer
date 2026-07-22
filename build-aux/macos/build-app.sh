#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."

APP_NAME="Skimmer"
ICNS="build-aux/data/icons/Skimmer.icns"

if [ ! -f "$ICNS" ]; then
    echo "Missing $ICNS — run ./build-aux/macos/make-icns.sh first"
    exit 1
fi

HOMEBREW_PREFIX=$(brew --prefix)
echo "Homebrew: $HOMEBREW_PREFIX"

export PKG_CONFIG_PATH="$HOMEBREW_PREFIX/lib/pkgconfig:$HOMEBREW_PREFIX/share/pkgconfig"
export DYLD_LIBRARY_PATH="$HOMEBREW_PREFIX/lib"
export XDG_DATA_DIRS="$HOMEBREW_PREFIX/share"

uv run pyinstaller \
    --name "$APP_NAME" \
    --icon "$ICNS" \
    --windowed \
    --onedir \
    --add-data "skimmer/data:skimmer/data" \
    --add-data "$HOMEBREW_PREFIX/share:share" \
    --add-binary "$HOMEBREW_PREFIX/lib/gstreamer-1.0:lib/gstreamer-1.0" \
    --collect-data gi \
    --collect-submodules gi \
    --collect-data ytmusicapi \
    --hidden-import gi \
    --hidden-import gi.repository \
    --hidden-import gi.repository.Gtk \
    --hidden-import gi.repository.Adw \
    --hidden-import gi.repository.Gst \
    --hidden-import gi.repository.GdkPixbuf \
    --hidden-import gi.repository.Gio \
    --hidden-import gi.repository.GLib \
    --hidden-import gi.repository.Pango \
    --hidden-import gi.repository.PangoCairo \
    --hidden-import gi.repository.cairo \
    --hidden-import gi.repository.HarfBuzz \
    --hidden-import gi.repository.Gdk \
    --hidden-import gi.repository.GObject \
    --hidden-import ytmusicapi \
    --hidden-import yt_dlp \
    --hidden-import beets \
    --hidden-import platformdirs \
    --hidden-import mpris_server \
    --runtime-hook build-aux/macos/runtime-hook.py \
    skimmer/__main__.py

# Remove GTK3 libraries and plugins to avoid conflicts with GTK4
FRAMEWORKS="dist/$APP_NAME.app/Contents/Frameworks"
rm -f "$FRAMEWORKS"/libgdk-3* "$FRAMEWORKS"/libgtk-3* "$FRAMEWORKS"/libgail*
find "$FRAMEWORKS" -name "libgstgtk*" -delete
find "$FRAMEWORKS" -name "libgstvalidategtk*" -delete

# Re-sign after removing libraries (ad-hoc signing for local use)
codesign --force --deep --sign - "dist/$APP_NAME.app" 2>/dev/null || true

echo "---"
echo "App bundle at dist/$APP_NAME.app"
