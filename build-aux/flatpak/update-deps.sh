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

cd "$PROJECT_DIR"
uv export --format requirements-txt --no-dev --no-hashes \
  | python3 -c "
import sys, re
from packaging.version import Version

deps = {}
for line in sys.stdin:
    line = line.strip()
    if not line or line.startswith('#') or line.startswith('-e '):
        continue
    line = re.sub(r' ; .*', '', line)
    if '==' in line:
        name, ver = line.split('==', 1)
        if name not in deps or Version(ver) > Version(deps[name]):
            deps[name] = ver
for name, ver in sorted(deps.items()):
    print(f'{name}=={ver}')
" > "$SCRIPT_DIR/requirements.txt"

python3 -m flatpak_pip_generator \
  --requirements-file="$SCRIPT_DIR/requirements.txt" \
  --runtime="$RUNTIME" \
  --prefer-wheels="$JOINED" \
  --output="$OUT"

rm "$SCRIPT_DIR/requirements.txt"
echo "Wrote $OUT"
