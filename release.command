#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p dist
stage="$(mktemp -d "$PWD/dist/image.XXXXXX")"
trap 'rm -rf "$stage"' EXIT
./build.command release "$stage/Foliofinch.app"
codesign --verify --deep --strict "$stage/Foliofinch.app"
ln -s /Applications "$stage/Applications"
hdiutil create -ov -fs HFS+ -format UDZO -volname Foliofinch -srcfolder "$stage" dist/Foliofinch-macOS-arm64.dmg
hdiutil verify dist/Foliofinch-macOS-arm64.dmg
