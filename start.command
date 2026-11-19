#!/bin/bash
set -eu
cd "$(dirname "$0")"
if [[ ! -x .runtime/frankenphp || ! -s .runtime/qwen.gguf || ! -d .runtime/llama ]]; then ./setup.command; fi
if [[ ! -x "Foliofinch.app/Contents/MacOS/Foliofinch" || macOS/Foliofinch.swift -nt "Foliofinch.app/Contents/MacOS/Foliofinch" || assets/Foliofinch.png -nt "Foliofinch.app/Contents/Resources/Foliofinch.icns" || ! -f "Foliofinch.app/Contents/Resources/Foliofinch.icns" ]]; then ./build.command; fi
/usr/bin/python3 schedule.py install
exec open "Foliofinch.app"
