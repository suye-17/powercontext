#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
QODER_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
DATA_DIR="${PAPERDESK_DATA_DIR:-$QODER_DIR/.runtime}"
PID_FILE="$DATA_DIR/powercontext.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "no PowerContext pid file at $PID_FILE"
  exit 0
fi

pid="$(cat "$PID_FILE")"
if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
  echo "PowerContext process $pid is not running"
  rm -f "$PID_FILE"
  exit 0
fi

kill "$pid"
for _ in $(seq 1 20); do
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$PID_FILE"
    echo "PowerContext stopped"
    exit 0
  fi
  sleep 0.25
done

echo "error: PowerContext $pid did not stop" >&2
exit 1
