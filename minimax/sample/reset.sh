#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
echo "Stop the PowerContext server before running reset."
rm -rf \
  .runtime/seekdb \
  .runtime/seekdb-v2 \
  .runtime/powercontext.db \
  .runtime/.powercontext.db.cursor-key \
  .runtime/powercontext-home
echo "Removed generated seekdb and PowerContext databases."
