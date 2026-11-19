#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
mode="${1:-development}"
app="${2:-Foliofinch.app}"
bundle="$app/Contents"
mkdir -p "$bundle/MacOS" "$bundle/Resources"
workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
iconset="$workdir/Foliofinch.iconset"
mkdir -p "$iconset"
for points in 16 32 128 256 512; do
  for scale in 1 2; do
    pixels=$((points * scale))
    name="icon_${points}x${points}"
    if [[ "$scale" == 2 ]]; then name="${name}@2x"; fi
    /usr/bin/sips -z "$pixels" "$pixels" assets/Foliofinch.png --out "$iconset/$name.png" >/dev/null
  done
done
/usr/bin/iconutil -c icns "$iconset" -o "$bundle/Resources/Foliofinch.icns"
/usr/bin/swiftc -parse-as-library -O -target arm64-apple-macos14.0 -module-cache-path "$workdir/modules" macOS/Foliofinch.swift -o "$bundle/MacOS/Foliofinch"
if [[ "$mode" == "release" ]]; then
  # Only pristine, explicitly selected program files go into a public build.
  runtime="dist/cache/python.tar.gz"
  mkdir -p dist/cache
  if [[ ! -f "$runtime" ]]; then
    /usr/bin/curl -fL --retry 3 'https://github.com/astral-sh/python-build-standalone/releases/download/20261110/cpython-3.13.18%2B20261110-aarch64-apple-darwin-install_only_stripped.tar.gz' -o "$workdir/python.tar.gz"
    mv "$workdir/python.tar.gz" "$runtime"
  fi
  echo "7a2384641584ab9984221fcb0fc9265df465922af71be71410529e2a9c3f0540  $runtime" | shasum -a 256 -c -
  tar xzf "$runtime" -C "$bundle/Resources" --exclude '*/__pycache__' --exclude '*/site-packages/*' \
    python/bin/python3.13 python/lib/libpython3.13.dylib python/lib/python3.13
  cp LICENSE app.py avatars.py inbox.py papers.py schedule.py services.py websources.py feeds.opml setup.command "$bundle/Resources/"
fi
/usr/bin/python3 - "$bundle" "$mode" <<'PY'
import sys
from pathlib import Path
import plistlib
p=Path(sys.argv[1])/'Info.plist'
extra = {'ProjectDirectory': str(Path.cwd())} if sys.argv[2] == 'development' else {}
p.write_bytes(plistlib.dumps(dict(
    CFBundleExecutable='Foliofinch', CFBundleIdentifier='local.blogdaily.reader',
    CFBundleName='Foliofinch',CFBundleDisplayName='Foliofinch',CFBundlePackageType='APPL',
    CFBundleIconFile='Foliofinch.icns', CFBundleShortVersionString='1.5.3',CFBundleVersion='9',
    LSMinimumSystemVersion='14.0',NSHighResolutionCapable=True,**extra)))
PY
/usr/bin/codesign --force --sign - "$app"
