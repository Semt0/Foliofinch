#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
test "$(uname -sm)" = "Darwin arm64" || { echo "此安装脚本适用于 Apple Silicon Mac"; exit 1; }
mkdir -p .runtime
cd .runtime
download() {
  local file="$1" digest="$2" url="$3"
  if [[ ! -f "$file" ]]; then
    curl -fL --retry 3 --continue-at - "$url" -o "$file.download"
    echo "$digest  $file.download" | shasum -a 256 -c -
    mv "$file.download" "$file"
  else
    echo "$digest  $file" | shasum -a 256 -c -
  fi
}
download frankenphp 2de72f9e9c5ad6bac2057a4fa74ad6fa7f5f260d5528b75b9f2f24569aa9c982 https://github.com/php/frankenphp/releases/download/v1.13.5/frankenphp-mac-arm64
download qwen.gguf 6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/91cad51170dc346986eccefdc2dd33a9da36ead9/qwen2.5-1.5b-instruct-q4_k_m.gguf
chmod +x frankenphp
if [[ ! -f FreshRSS-1.30.4/cli/do-install.php ]]; then
  download freshrss.tar.gz 4c750c794479e6158bfb4f970050a7083f4e3afdbaea3bc6063aba3a5df8d01d https://codeload.github.com/FreshRSS/FreshRSS/tar.gz/refs/tags/1.30.4
  tar xzf freshrss.tar.gz
  rm -f freshrss.tar.gz
fi
if [[ ! -x llama/llama-server ]]; then
  download llama.tar.gz e3162c7d47e796435bcdef4ca348c56f4a315eebefc90109ca15d40e1d136ca8 https://github.com/ggml-org/llama.cpp/releases/download/b12532/llama-b12532-bin-macos-arm64.tar.gz
  mkdir -p llama
  tar xzf llama.tar.gz -C llama --strip-components=1
  rm -f llama.tar.gz
fi
echo "安装完成。双击 start.command 启动。"
