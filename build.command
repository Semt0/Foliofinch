#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
bundle="Foliofinch.app/Contents"
mkdir -p "$bundle/MacOS" "$bundle/Resources" .runtime
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
/usr/bin/python3 - <<'PY'
from pathlib import Path
import plistlib
p=Path('Foliofinch.app/Contents/Info.plist')
p.write_bytes(plistlib.dumps(dict(
    CFBundleExecutable='Foliofinch', CFBundleIdentifier='local.blogdaily.reader',
    CFBundleName='Foliofinch',CFBundleDisplayName='Foliofinch',CFBundlePackageType='APPL',
    CFBundleIconFile='Foliofinch.icns', CFBundleShortVersionString='1.1',CFBundleVersion='2',
    LSMinimumSystemVersion='14.0',NSHighResolutionCapable=True,ProjectDirectory=str(Path.cwd()))))
PY
/usr/bin/codesign --force --sign - "Foliofinch.app"
