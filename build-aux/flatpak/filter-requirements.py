#!/usr/bin/env python3
"""Turn `uv export` output into a flat requirements list for the Flatpak bundle.

- Drops dependencies gated to non-Linux platforms (pyobjc on macOS, colorama on
  Windows) so they never leak into the Flatpak.
- Drops packages already provided by the org.gnome.Platform runtime, passed via
  the FLATPAK_SKIP environment variable (comma separated).
"""

import os
import re
import sys

from packaging.version import Version

skip = {name.lower() for name in os.environ.get("FLATPAK_SKIP", "").split(",") if name}

non_linux = re.compile(
    r"""['"](darwin|win32|cygwin)['"]|os_name\s*==\s*['"]nt['"]""",
    re.IGNORECASE,
)

deps: dict[str, str] = {}
for line in sys.stdin:
    line = line.strip()
    if not line or line.startswith("#") or line.startswith("-e "):
        continue
    if non_linux.search(line):
        continue
    line = re.sub(r" ; .*", "", line)
    if "==" not in line:
        continue
    name, ver = line.split("==", 1)
    if name.lower() in skip:
        continue
    if name not in deps or Version(ver) > Version(deps[name]):
        deps[name] = ver

for name, ver in sorted(deps.items()):
    print(f"{name}=={ver}")
