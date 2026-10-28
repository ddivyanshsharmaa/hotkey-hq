#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"
SYSTEM="$(uname -s)"
case "$SYSTEM" in
  Darwin|Linux) ;;
  *) printf 'Use setup.bat on Windows. This script supports macOS and Linux.\n' >&2; exit 1 ;;
esac
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  printf 'Python 3 was not found. Install Python 3 with venv support, then retry.\n' >&2
  exit 1
fi
printf 'Creating a private environment in %s/.venv ...\n' "$ROOT"
if [[ "$SYSTEM" == Linux ]]; then
  "$PYTHON" -m venv --system-site-packages "$ROOT/.venv"
else
  "$PYTHON" -m venv "$ROOT/.venv"
fi
export PIP_CACHE_DIR="$ROOT/.cache/pip"
"$ROOT/.venv/bin/python" -m pip install -r "$ROOT/requirements.txt"
"$ROOT/.venv/bin/python" -c 'import webview'
if [[ "$SYSTEM" == Linux ]]; then
  if ! "$ROOT/.venv/bin/python" -c 'import gi; gi.require_version("Gtk", "3.0"); gi.require_version("WebKit2", "4.1"); from gi.repository import Gtk, WebKit2'; then
    printf 'GTK/WebKitGTK support is missing. See the Linux requirements in README.md, install them, and retry.\n' >&2
    exit 1
  fi
else
  "$ROOT/.venv/bin/python" -c 'import AppKit, WebKit, Quartz'
fi
printf 'Done. Start Hotkey HQ with: bash "%s/launch.sh"\n' "$ROOT"
