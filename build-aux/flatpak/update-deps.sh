#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT="$SCRIPT_DIR/pypi-dependencies.json"
RUNTIME="org.gnome.Sdk//50"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

NATIVE_PKGS=(
  jellyfish pillow pyyaml lap numpy numba llvmlite scipy charset_normalizer
)
JOINED=$(IFS=,; echo "${NATIVE_PKGS[*]}")

# Packages provided by the org.gnome.Platform runtime. Bundling these either
# duplicates the runtime or fails (pycairo's meson-python backend is absent
# from the SDK), so skip them entirely.
RUNTIME_PKGS=(
  pygobject pycairo
)
JOINED_SKIP=$(IFS=,; echo "${RUNTIME_PKGS[*]}")

cd "$PROJECT_DIR"
uv export --format requirements-txt --no-dev --no-hashes \
  | FLATPAK_SKIP="$JOINED_SKIP" python3 "$SCRIPT_DIR/filter-requirements.py" \
  > "$SCRIPT_DIR/requirements.txt"

uvx --from flatpak-pip-generator python -m flatpak_pip_generator \
  --requirements-file="$SCRIPT_DIR/requirements.txt" \
  --runtime="$RUNTIME" \
  --prefer-wheels="$JOINED" \
  --output="$OUT"

rm "$SCRIPT_DIR/requirements.txt"
echo "Wrote $OUT"
