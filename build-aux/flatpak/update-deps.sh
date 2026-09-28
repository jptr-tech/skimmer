#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT="$SCRIPT_DIR/pypi-dependencies.json"
STAMP="$SCRIPT_DIR/.pypi-deps.stamp"
MANIFEST="$SCRIPT_DIR/tech.jptr.Skimmer.yml"
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

REQUIREMENTS="$(mktemp)"
trap 'rm -f "$REQUIREMENTS"' EXIT

cd "$PROJECT_DIR"
uv export --format requirements-txt --no-dev --no-hashes \
  | FLATPAK_SKIP="$JOINED_SKIP" python3 "$SCRIPT_DIR/filter-requirements.py" \
  > "$REQUIREMENTS"

# The manifest's runtime-version selects the Python ABI (and therefore the
# wheels), so a runtime bump must invalidate the cache.
MANIFEST_RUNTIME="$(python3 - "$MANIFEST" <<'PY'
import re
import sys

text = open(sys.argv[1], encoding="utf-8").read()
match = re.search(r'^runtime-version:\s*"?([^"\n]+?)"?\s*$', text, re.M)
print(match.group(1) if match else "")
PY
)"

# Cache key: the pinned requirements plus the scripts that shape the output and
# the runtime/wheel settings. The generator is intentionally unpinned, so its
# version is not part of the key (use `make flatpak-deps-force` after upgrades).
KEY="$(EXTRA_KEY="$RUNTIME|$JOINED|$MANIFEST_RUNTIME" python3 - \
  "$REQUIREMENTS" "$SCRIPT_DIR/update-deps.sh" "$SCRIPT_DIR/filter-requirements.py" <<'PY'
import hashlib
import os
import sys

digest = hashlib.sha256()
for path in sys.argv[1:]:
    with open(path, "rb") as handle:
        digest.update(handle.read())
    digest.update(b"\0")
digest.update(os.environ.get("EXTRA_KEY", "").encode())
print(digest.hexdigest())
PY
)"

if [ -z "${FORCE:-}" ] && [ -f "$OUT" ] && [ -f "$STAMP" ] \
  && [ "$(head -n1 "$STAMP")" = "$KEY" ]; then
  echo "Flatpak deps up to date (generated $(sed -n 2p "$STAMP")); skipping."
  exit 0
fi

uvx --from flatpak-pip-generator python -m flatpak_pip_generator \
  --requirements-file="$REQUIREMENTS" \
  --runtime="$RUNTIME" \
  --prefer-wheels="$JOINED" \
  --output="$OUT"

printf '%s\n%s\n' "$KEY" "$(date -Iseconds)" > "$STAMP"
echo "Wrote $OUT"
