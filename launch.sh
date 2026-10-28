#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  printf 'First run: please run bash "%s/setup.sh" once.\n' "$ROOT" >&2
  exit 1
fi
exec "$ROOT/.venv/bin/python" "$ROOT/server.py"
