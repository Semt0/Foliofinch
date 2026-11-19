#!/bin/bash
set -eu
cd "$(dirname "$0")"
if [[ ! -x .runtime/frankenphp || ! -s .runtime/qwen.gguf || ! -d .runtime/llama ]]; then ./setup.command; fi
if [[ ! -x "博客日报.app/Contents/MacOS/BlogDaily" || macOS/BlogDaily.swift -nt "博客日报.app/Contents/MacOS/BlogDaily" ]]; then ./build.command; fi
/usr/bin/python3 schedule.py install
exec open "博客日报.app"
