#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
bundle="博客日报.app/Contents"
mkdir -p "$bundle/MacOS" .runtime
/usr/bin/swiftc -parse-as-library -O -target arm64-apple-macos14.0 -module-cache-path .runtime/swift-cache macOS/BlogDaily.swift -o "$bundle/MacOS/BlogDaily"
/usr/bin/python3 - <<'PY'
from pathlib import Path
import plistlib
p=Path('博客日报.app/Contents/Info.plist')
p.write_bytes(plistlib.dumps(dict(
    CFBundleExecutable='BlogDaily', CFBundleIdentifier='local.blogdaily.reader',
    CFBundleName='博客日报',CFBundleDisplayName='博客日报',CFBundlePackageType='APPL',
    CFBundleShortVersionString='1.0',CFBundleVersion='1',LSMinimumSystemVersion='14.0',
    NSHighResolutionCapable=True,ProjectDirectory=str(Path.cwd()))))
PY
/usr/bin/codesign --force --sign - "博客日报.app"
