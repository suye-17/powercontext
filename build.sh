#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$repo_root"

if ! command -v uv >/dev/null 2>&1; then
  printf 'Error: uv is required to build PowerContext. Install uv and try again.\n' >&2
  exit 127
fi

make build
